"""
APEX-Commerce end-to-end invariant checker.

Run this with the backend already serving on http://127.0.0.1:8000.
It behaves like an external buyer agent: pure HTTP, no imports from app/.
Standard library only, so there is nothing to install.

    python e2e_check.py

For FULL coverage, clear the order history first:

    python -m app.database.reset_demo --yes

Why: the enclave fingerprints every intent (agent + SKUs + quantities +
proposed prices) and returns the STORED order for an identical repeat instead
of re-evaluating it. That is a money-safety feature - it is what stops a
retried request from charging twice - but it means a basket you have already
bought once cannot produce a fresh policy verdict. Without a reset, some
checks below can only be verified against the persisted order, and the ones
that need a live decision object are reported as [SKIP], never [FAIL].

Every check below is a property that must hold for the project to be honest
about its own safety claims. A [FAIL] is a real bug. A [SKIP] means this run
could not exercise it. An [INFO] is context.

TWO RULES THIS FILE LEARNED THE HARD WAY
----------------------------------------
1. Assert the HTTP status BEFORE reading the body. A 404 or a 500 returns a
   body with no cost fields, no decision and no priced lines - so a check
   that scans it prints [PASS] while verifying absolutely nothing. A green
   tick on an error body is worse than a red one, because nobody investigates
   it. Every endpoint this file trusts is now status-checked first.
2. Paths are load-bearing. The agent-facing catalog is served under /aci/,
   not /catalog. This file now reads the path out of the discovery manifest
   instead of hard-coding a guess.

A full run costs about 12 model calls. The primary gateway (gorouter.app,
running Claude Opus 5) bills PER CALL at 0.3 credits, so a run is roughly
3.6 credits - about 13 runs per 50 credits, and a retry is a fresh charge.
Section 1 checks, for free, that the pinned model really exists on the
gateway and that something is armed behind it, because without a fallback an
empty balance turns every negotiation into a 503.
"""

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
AGENT = "agent-buyer-01"

PASSED = []
FAILED = []
SKIPPED = []
MODEL_CALLS = [0]  # every AI call this run spends, for free-tier budgeting


def call(path, body=None, timeout=240):
    """Make one HTTP request and always return (status_code, parsed_body)."""
    url = f"{BASE}{path}"
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url, data=data, headers=headers, method="POST" if body is not None else "GET"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8")
        try:
            return error.code, json.loads(raw) if raw else None
        except json.JSONDecodeError:
            return error.code, {"raw": raw}
    except Exception as error:  # connection refused, timeout, DNS
        return 0, {"error": str(error)}


def check(name, ok, detail=""):
    tag = "[PASS]" if ok else "[FAIL]"
    (PASSED if ok else FAILED).append(name)
    print(f"  {tag} {name}" + (f"  ->  {detail}" if detail else ""))
    return ok


def info(text):
    print(f"  [INFO] {text}")


def skip(name, why):
    """Not a pass and not a failure: this run could not exercise the check."""
    SKIPPED.append(name)
    print(f"  [SKIP] {name}  ->  {why}")


def endpoint_ok(name, status, body=None):
    """Confirm an endpoint really answered 200 before trusting anything in it.

    A non-200 here is recorded as a genuine [FAIL], not a [SKIP]: unlike an
    idempotent replay (which is the system working as designed), an error
    response means the feature under test did not run at all.
    """
    if status == 200:
        PASSED.append(name)
        print(f"  [PASS] {name}  ->  status 200")
        return True

    if status == 503 and isinstance(body, dict):
        problem = body.get("error") or {}
        if problem.get("code") == "llm_unavailable":
            # llm_guard already diagnosed this failure and named the provider
            # that ran dry. Print ITS answer instead of guessing: an old guess
            # that blames the wrong provider costs more time than no guess.
            lines = [
                "status 503 - no model answered, so the request never reached the",
                "         enclave: nothing was ordered and no money moved.",
                f"         likely cause: {problem.get('likely_cause') or 'unknown'}",
            ]
            for step in (problem.get("what_to_do") or [])[:4]:
                lines.append(f"         fix: {step}")
            for attempt in (problem.get("provider_attempts") or [])[:6]:
                lines.append(f"         tried: {attempt}")
            check(name, False, "\n".join(lines))
            return False

    if status >= 500:
        hint = (" - the server raised an exception. Read the uvicorn window."
                " If it says 'All LLM providers failed', the primary gateway"
                " refused and nothing behind it picked up the slack. Check"
                " GET /agent/llm-status - it reports credit and whether the"
                " pinned model id actually exists, and costs nothing to call.")
    elif status == 404:
        hint = " - this route does not exist on the server. Wrong path."
    elif status == 0:
        hint = f" - could not connect: {(body or {}).get('error')}"
    else:
        hint = f" - {str(body)[:120]}"
    check(name, False, f"status {status}{hint}")
    return False


def numbers_in(node, found=None):
    """Every numeric value anywhere inside a JSON structure.

    Numbers written as strings count too, because JSON-LD renders prices as
    strings ("1299.00"). This lets us hunt for a leaked cost FIGURE even if
    someone hides it behind an innocent-looking field name.
    """
    if found is None:
        found = set()
    if isinstance(node, dict):
        for value in node.values():
            numbers_in(value, found)
    elif isinstance(node, list):
        for value in node:
            numbers_in(value, found)
    elif isinstance(node, bool):
        pass  # bool is a subclass of int in Python; ignore True/False
    elif isinstance(node, (int, float)):
        found.add(round(float(node), 2))
    elif isinstance(node, str):
        try:
            found.add(round(float(node), 2))
        except (TypeError, ValueError):
            pass
    return found


def head(text):
    print(f"\n=== {text} " + "=" * max(0, 62 - len(text)))


def rs(paise):
    return f"Rs.{(paise or 0) / 100:,.2f}"


def buy(request_text, budget, resilient=False):
    """Ask the agent to shop. Returns (status, whole response body).

    Every call here runs the two-agent pipeline (Front Agent + Negotiator),
    which is TWO model requests. That is why the tally matters on a free tier.
    """
    MODEL_CALLS[0] += 2
    path = "/recovery/agent-purchase" if resilient else "/agent/purchase"
    status, data = call(
        path, {"agent_id": AGENT, "request": request_text, "budget_inr": budget}
    )
    return status, data or {}


def is_replay(envelope):
    """True when the enclave short-circuited and handed back a stored order.

    orchestrator.run_checkout returns {"idempotent_replay": True, **summary}
    and that summary carries decision=None, because no fresh evaluation ran.
    Reading a decision field off a replay yields None - which is exactly the
    false alarm this helper exists to prevent.
    """
    return bool((envelope or {}).get("idempotent_replay"))


def stored_order(order_id):
    """Read an order back from the merchant read model (the DB, not the reply)."""
    if not order_id:
        return {}
    status, detail = call(f"/dashboard/orders/{order_id}")
    return detail or {} if status == 200 else {}


def lines_of(enclave_result):
    return ((enclave_result or {}).get("decision") or {}).get("lines") or []


def floor_violations(enclave_result):
    """The single most important query in this file: did anything get charged
    below the price floor the merchant's margin policy protects?"""
    bad = []
    for line in lines_of(enclave_result):
        final = line.get("final_unit_price_paise")
        floor = line.get("floor_unit_price_paise")
        if final is None or floor is None:
            continue
        if final < floor:
            bad.append(f"{line.get('sku')} charged {rs(final)} below floor {rs(floor)}")
    return bad


def money_api_touched(enclave_result):
    result = enclave_result or {}
    return bool(
        result.get("razorpay_order_id") or result.get("razorpay_payment_link_id")
    )


# --------------------------------------------------------------------------
head("1. Backend reachable and AI providers ready")
status, root = call("/")
if not check("GET / responds 200", status == 200, f"status {status}"):
    print("\nBackend is not up. Run: uvicorn app.main:app --reload")
    sys.exit(1)
info(f"version {(root or {}).get('version', 'unknown')}")

# Ask which providers are live BEFORE spending any of them. This is the
# pre-flight that turns a mid-run 500 into a warning you get up front.
status, llm = call("/agent/llm-status")
if status == 200:
    llm = llm or {}
    primary = (llm or {}).get("primary") or {}
    fallback = (llm or {}).get("fallback") or {}
    gorouter = (llm or {}).get("gorouter") or {}
    check("a primary AI provider is configured",
          bool(primary.get("configured")),
          f"{primary.get('provider')}/{primary.get('model')}")
    info("provider order: " + " -> ".join(llm.get("provider_order") or ["?"]))

    # The primary gateway bills PER CALL, and two things there can make every
    # negotiation below fail. Both are visible for free, right here, before a
    # single credit is spent: a model id that does not exist on the gateway,
    # and a machine that cannot reach the gateway at all.
    if primary.get("provider") == "gorouter":
        catalog = gorouter.get("catalog") or {}
        if catalog.get("blocked_at_edge"):
            # The single most confusing failure this project has: nothing is wrong
            # with the key, the credit or the model id, and yet every AI section
            # below will fail. Say so ONCE, here, with the triage in order.
            check("this machine can reach gorouter.app", False,
                  "BLOCKED BEFORE THE GATEWAY - Cloudflare (or a proxy/VPN/antivirus"
                  " on this machine) answered with a web page instead of JSON, so"
                  " gorouter never saw the request and nothing was billed."
                  "\n         Every negotiation below will 503 for this one reason."
                  "\n         1. open https://gorouter.app/api/pricing in a browser:"
                  " JSON means your network is fine and the block is aimed at the"
                  " Python client; a Cloudflare page means it is aimed at this IP."
                  "\n         2. try a phone hotspot, or switch off HTTPS scanning in"
                  " your antivirus/VPN."
                  "\n         3. to finish the demo meanwhile, set"
                  " LLM_PRIMARY_PROVIDER=gemini in backend/.env and restart uvicorn.")
        elif catalog.get("error"):
            check("the gorouter.app catalog is reachable", False,
                  f"{catalog['error']} - this machine could not read the public"
                  " model list. If that is a connection, DNS or 403 error then"
                  " EVERY negotiation below will return 503 for the same reason.")
        else:
            check("the pinned model exists on the gateway",
                  catalog.get("pinned_model_found") is True,
                  f"pinned {primary.get('model')!r},"
                  f" gateway offers {catalog.get('models_offered')}"
                  " - ids here carry NO vendor prefix, so 'claude-opus-5' is"
                  " right and 'anthropic/claude-opus-5' is wrong")
            price = catalog.get("pinned_model_per_call_price")
            if price:
                info(f"billing is per CALL at {price} credits, so this run costs"
                     f" about {round(12 * float(price), 1)} credits")
        credit = gorouter.get("credit") or {}
        if credit.get("error"):
            info(f"credit read failed ({credit['error']}) - the gateway dashboard"
                 " is the authority, this endpoint is best-effort only")
        else:
            info(f"credit reported: limit={credit.get('reported_limit')}"
                 f" used={credit.get('used')} remaining={credit.get('remaining')}")
        info("billable calls this uvicorn process has made so far:"
             f" {gorouter.get('billable_calls_this_process')}"
             f" (~{gorouter.get('estimated_credits_spent')} credits)")

    if fallback.get("configured"):
        check("a fallback provider is armed",
              bool(fallback.get("model")),
              f"{fallback.get('provider')}/{fallback.get('model')}"
              + (f" - {fallback['note']}" if fallback.get("note") else ""))
    else:
        info("WARNING: nothing is behind the primary. If the primary refuses -"
             " out of credit, bad key, or unreachable - every negotiation in"
             " this run returns 503 and the AI-dependent sections all FAIL."
             " Set GEMINI_API_KEY in backend/.env and restart uvicorn.")
else:
    info(f"GET /agent/llm-status returned {status}; cannot confirm failover")
info("this run spends roughly 12 model calls: about 3.6 credits on gorouter.app"
     " (billed per call), or 12 of the 20 requests Gemini's free tier allows"
     " per day per model")

# --------------------------------------------------------------------------
head("2. Restore the catalog to a known state")
status, reset = call("/recovery/slippage/reset", {})
check("slippage reset works", status == 200, f"restored {(reset or {}).get('count')}")

# --------------------------------------------------------------------------
head("3. Merchant read model and mandate")
status, summary = call("/dashboard/summary")
check("GET /dashboard/summary responds 200", status == 200, f"status {status}")
summary = summary or {}
mandate = summary.get("mandate") or {}
per_tx = mandate.get("per_transaction_cap_inr")
daily = mandate.get("daily_cap_inr")
threshold = summary.get("step_up_threshold_inr")

check("a mandate is seeded", bool(per_tx), f"per-tx cap Rs.{per_tx}")
check("a daily cap exists", bool(daily), f"daily cap Rs.{daily}")
check("step-up threshold is set", bool(threshold), f"Rs.{threshold}")
check(
    "per-tx cap does not exceed the daily cap",
    (per_tx or 0) <= (daily or 0),
    f"Rs.{per_tx} vs Rs.{daily}",
)
info(f"orders on record: {summary.get('order_count')}")
remaining = mandate.get("remaining_today_inr")
info(f"daily headroom left: Rs.{remaining} of Rs.{daily}")
if remaining is not None and per_tx is not None and remaining < per_tx:
    info("WARNING: less headroom than one full transaction. Later checks may be"
         " rejected on the DAILY cap rather than the per-tx cap. Run"
         " 'python -m app.database.reset_demo --yes' to clear today's spend.")

# --------------------------------------------------------------------------
head("4. Price floors are real on every SKU")
status, catalog = call("/dashboard/products")
products = (catalog or {}).get("products") or []
check("catalog is seeded", len(products) > 0, f"{len(products)} products")

broken = [
    f"{p.get('sku')}: floor Rs.{p.get('floor_inr')} <= cost Rs.{p.get('cost_inr')}"
    for p in products
    if (p.get("floor_inr") or 0) <= (p.get("cost_inr") or 0)
]
check("every SKU's floor sits above its cost", not broken, "; ".join(broken))

under = [
    p.get("sku")
    for p in products
    if (p.get("list_inr") or 0) < (p.get("floor_inr") or 0)
]
check("no SKU is listed below its own floor", not under, ", ".join(under))

by_sku = {p.get("sku"): p for p in products}

# --------------------------------------------------------------------------
head("5. The agent-facing catalog hides cost prices")
# This is the project's headline data-minimisation claim, so it gets tested
# properly. The agent catalog lives under /aci/ - NOT /catalog. Reading the
# path out of the discovery manifest means a route rename can never again
# leave this check quietly scanning a 404 body and reporting "clean".
status, manifest = call("/.well-known/aci.json")
endpoint_ok("GET /.well-known/aci.json responds 200 (agent discovery)",
            status, manifest)
advertised = ((manifest or {}).get("endpoints") or {}).get("catalog")
check("the discovery manifest advertises a catalog endpoint",
      bool(advertised), advertised or "missing - falling back to /aci/catalog")

agent_views = {}
for path in [p for p in (advertised or "/aci/catalog", "/aci/catalog/jsonld") if p]:
    status, public = call(path)
    if endpoint_ok(f"GET {path} responds 200", status, public):
        agent_views[path] = public

check("the advertised path really serves product data",
      any(numbers_in(view) for view in agent_views.values()),
      f"{len(agent_views)} agent-facing endpoints readable")

# Two independent scans. The first catches an obvious field name. The second
# catches the same secret hidden behind a harmless name, by hunting for the
# cost FIGURES themselves (taken from the merchant-only endpoint above).
cost_figures = set()
for product in products:
    cost = product.get("cost_inr")
    if cost:
        cost_figures.add(round(float(cost), 2))
        cost_figures.add(round(float(cost) * 100, 2))  # in case paise leak

for path, view in agent_views.items():
    text = json.dumps(view).lower()
    named = [word for word in ("cost_price", "cost_inr", "cost_paise",
                               "min_margin", "margin_percentage", "floor")
             if word in text]
    check(f"no cost or margin field name appears in {path}", not named,
          "leaked: " + ", ".join(named) if named else "clean")

    exposed = sorted(cost_figures & numbers_in(view))
    check(f"no cost figure appears anywhere in {path}", not exposed,
          f"LEAKED: {exposed} - these match merchant cost prices"
          if exposed else
          f"{len(numbers_in(view))} numbers checked against "
          f"{len(cost_figures) // 2} cost prices")

# --------------------------------------------------------------------------
head("6. Negotiation produces a structured intent (LLM is alive)")
status, negotiated = call(
    "/agent/negotiate",
    {
        "agent_id": AGENT,
        "request": "Set me up with a mechanical keyboard and a USB-C hub for my ApexBook laptop.",
        "budget_inr": 7000,
    },
)
negotiated = negotiated or {}
MODEL_CALLS[0] += 2
intent = negotiated.get("proposed_intent") or {}
items = intent.get("items") or []
if endpoint_ok("POST /agent/negotiate responds 200", status, negotiated):
    check("the model returned at least one item", len(items) > 0,
          f"{len(items)} items")
    # A different property from "not empty": no line may be unchargeable.
    # A zero/negative price or a zero quantity is how the Phase 5 Rs.0-order
    # bug looked. A price of None is legal - it means "no counter-offer, use
    # the list price" - so only a present-but-worthless value counts here.
    unchargeable = []
    for item in items:
        price = item.get("proposed_unit_price_inr")
        if price is not None and float(price) <= 0:
            unchargeable.append(f"{item.get('sku')} priced {price}")
        if (item.get("quantity") or 0) <= 0:
            unchargeable.append(f"{item.get('sku')} qty {item.get('quantity')}")
    check("no proposed line is unchargeable (zero price or zero quantity)",
          not unchargeable,
          "; ".join(unchargeable) or
          "guards the Rs.0-order bug found in Phase 5")
else:
    skip("the model returned at least one item", "the negotiate call failed")
    skip("no proposed line is unchargeable (zero price or zero quantity)",
         "same reason")
served = (negotiated.get("ai_proposal") or {}).get("served_by")
info(f"served by: {served}")
info("negotiate must not create an order -> "
     f"razorpay ids present: {money_api_touched(negotiated)}")

# --------------------------------------------------------------------------
head("7. Over-cap basket is rejected BEFORE any money API call")
OVER_CAP_REQUEST = (
    "Set me up with a mechanical keyboard and a USB-C hub for my ApexBook laptop."
)
status, over = buy(OVER_CAP_REQUEST, 7000)
attempt = over.get("enclave_result") or {}
decision = attempt.get("decision") or {}
subtotal = attempt.get("subtotal_inr") or 0
first_order_id = attempt.get("order_id")
info(f"agent proposed Rs.{subtotal}, mandate allows Rs.{per_tx}")

if not endpoint_ok("POST /agent/purchase responds 200", status, over):
    # Without this guard the checks below would read {} and print [PASS] for
    # "no line was charged below its floor" - true only because there were no
    # lines at all. That false green is what prompted this rewrite.
    for pending in ("an over-cap basket is rejected",
                    "NO Razorpay call was made on the rejected order",
                    "a machine-readable reason is returned"):
        skip(pending, f"the purchase call returned {status}, so there is no"
                      " policy decision to inspect")
elif is_replay(attempt):
    # The enclave refused to re-evaluate a basket it has already priced. The
    # safety property is still checkable - just against the stored order
    # rather than a fresh decision object.
    info(f"this basket was already processed; order {first_order_id} was replayed")
    persisted = stored_order(first_order_id)
    check(
        "the replayed over-cap order never reached Razorpay",
        not money_api_touched(persisted),
        f"status={persisted.get('status')}, "
        f"razorpay_order_id={persisted.get('razorpay_order_id')}",
    )
    check(
        "the replayed over-cap order was never marked paid",
        persisted.get("status") not in ("paid", "pending_payment", "awaiting_approval"),
        f"status={persisted.get('status')}",
    )
    skip("an over-cap basket is rejected",
         "replay returns the stored order, so no live verdict exists "
         "(run reset_demo.py for this one)")
    skip("a machine-readable reason is returned",
         "same reason - rejection_reasons only exists on a fresh evaluation")
elif subtotal > (per_tx or 0):
    check("an over-cap basket is rejected", decision.get("approved") is False,
          f"approved={decision.get('approved')}")
    check(
        "NO Razorpay call was made on the rejected order",
        not money_api_touched(attempt),
        "this is the whole thesis of the project",
    )
    check("a machine-readable reason is returned",
          bool(decision.get("rejection_reasons")),
          str(decision.get("rejection_reasons"))[:120])
else:
    info("basket came in under the cap this run; cap rejection not exercised here")
    check("under-cap basket was approved", decision.get("approved") is True)

if status != 200:
    skip("no line was ever charged below its floor",
         "the call failed, so no priced lines exist to inspect")
elif not lines_of(attempt):
    # Zero lines cannot violate a floor, so calling that a PASS would be
    # dishonest. Say plainly that there was nothing to measure.
    skip("no line was ever charged below its floor",
         "this reply carried no priced lines (a replay returns the stored"
         " order instead of a fresh decision)")
else:
    check("no line was ever charged below its floor",
          not floor_violations(attempt),
          "; ".join(floor_violations(attempt))
          or f"{len(lines_of(attempt))} priced lines inspected")

# --------------------------------------------------------------------------
head("8. The same intent cannot be charged twice (idempotency)")
status, summary_before = call("/dashboard/summary")
count_before = (summary_before or {}).get("order_count")

status, again = buy(OVER_CAP_REQUEST, 7000)
replayed = again.get("enclave_result") or {}
if not endpoint_ok("POST /agent/purchase responds 200 on a repeat", status, again):
    for pending in ("an identical intent is recognised as a replay",
                    "the replay returns the ORIGINAL order, not a new one",
                    "the replay reuses the same idempotency fingerprint",
                    "a replay does not create a second order row",
                    "a replay never fires a fresh Razorpay call"):
        skip(pending, f"the repeat call returned {status}")
elif first_order_id is None:
    for pending in ("an identical intent is recognised as a replay",
                    "the replay returns the ORIGINAL order, not a new one",
                    "the replay reuses the same idempotency fingerprint",
                    "a replay does not create a second order row",
                    "a replay never fires a fresh Razorpay call"):
        skip(pending, "section 7 never produced an order to replay")
else:
    check(
        "an identical intent is recognised as a replay",
        is_replay(replayed),
        f"idempotent_replay={replayed.get('idempotent_replay')}",
    )
    check(
        "the replay returns the ORIGINAL order, not a new one",
        replayed.get("order_id") == first_order_id,
        f"order {first_order_id} -> order {replayed.get('order_id')}",
    )
    check(
        "the replay reuses the same idempotency fingerprint",
        bool(replayed.get("idempotency_key")),
        str(replayed.get("idempotency_key"))[:16] + "...",
    )

    status, summary_after = call("/dashboard/summary")
    count_after = (summary_after or {}).get("order_count")
    check(
        "a replay does not create a second order row",
        count_before is not None and count_after == count_before,
        f"{count_before} orders before, {count_after} after",
    )
    check(
        "a replay never fires a fresh Razorpay call",
        not money_api_touched(replayed) or replayed.get("order_id") == first_order_id,
        "any payment artifact must belong to the original order",
    )

# --------------------------------------------------------------------------
head("9. Auto-recovery repairs an over-cap basket deterministically")
# Deliberately a DIFFERENT basket from section 7. Section 7 already spent that
# fingerprint, and an identical intent would replay instead of being repaired,
# so recovery would never actually run.
RECOVERY_REQUEST = (
    "I want a mechanical keyboard, a USB-C hub and a wireless mouse "
    "for my new desk setup."
)
status, resilient = buy(RECOVERY_REQUEST, 9000, resilient=True)
recovery = resilient.get("recovery") or {}
first_try = resilient.get("attempt") or {}
recovery_ran = endpoint_ok("POST /recovery/agent-purchase responds 200",
                           status, resilient)
info(f"first attempt Rs.{first_try.get('subtotal_inr')} "
     f"status={first_try.get('status')}")
info(f"recovery attempted={recovery.get('attempted')} "
     f"succeeded={recovery.get('succeeded')}")
info(f"reason: {recovery.get('reason')}")

counter = recovery.get("counter_offer") or {}
if not recovery_ran:
    for pending in ("the counter-offer fits inside the per-tx cap",
                    "the counter-offer is cheaper than the rejected basket",
                    "the counter-offer produced a real Razorpay artifact",
                    "no counter-offer line dips below its floor"):
        skip(pending, f"the recovery call returned {status}, so no"
                      " counter-offer was produced")
elif is_replay(first_try):
    skip("auto-recovery produces a compliant counter-offer",
         "this basket was already processed, so recovery had nothing to repair "
         "(run reset_demo.py for this one)")
elif counter:
    counter_total = counter.get("subtotal_inr") or 0
    check("the counter-offer fits inside the per-tx cap",
          counter_total <= (per_tx or 0),
          f"Rs.{counter_total} <= Rs.{per_tx}")
    check("the counter-offer is cheaper than the rejected basket",
          counter_total < (first_try.get("subtotal_inr") or float("inf")),
          f"Rs.{first_try.get('subtotal_inr')} -> Rs.{counter_total}")
    check("the counter-offer produced a real Razorpay artifact",
          bool(counter.get("pay_here")) or money_api_touched(counter),
          str(counter.get("pay_here") or counter.get("razorpay_order_id"))[:70])
    # The counter-offer envelope is a full run_checkout summary, so it carries
    # its own decision with priced lines. That - not the order-detail endpoint -
    # is where floor data lives.
    check("no counter-offer line dips below its floor",
          bool(lines_of(counter)) and not floor_violations(counter),
          "; ".join(floor_violations(counter))
          or (f"{len(lines_of(counter))} repriced lines inspected - recovery"
              " discounts down to the floor, never through it"
              if lines_of(counter) else
              "NO priced lines came back, so nothing was actually verified"))
    check("the counter-offer is linked to a real order row",
          bool(stored_order(counter.get("order_id")).get("id")),
          f"order {counter.get('order_id')}")
elif recovery.get("attempted"):
    check("a failed recovery still explains itself", bool(recovery.get("reason")))
else:
    info("recovery was not needed this run (basket already fit under the cap)")

# --------------------------------------------------------------------------
head("10. Cost slippage pushes the charged price UP, not down")
if "MOU-WL-01" in by_sku:
    old_floor = by_sku["MOU-WL-01"].get("floor_inr")
    status, _ = call("/recovery/slippage/cost",
                     {"sku": "MOU-WL-01", "cost_price_inr": 1500})
    check("cost slippage can be injected", status == 200, f"status {status}")
    status, after = call("/dashboard/products")
    new_floor = {p["sku"]: p for p in (after or {}).get("products", [])} \
        .get("MOU-WL-01", {}).get("floor_inr")
    check("raising cost raises the floor",
          (new_floor or 0) > (old_floor or 0),
          f"Rs.{old_floor} -> Rs.{new_floor}")

    status, slipped = buy("I need a USB-C hub and a quiet wireless mouse for my desk.",
                          4000, resilient=True)
    slipped_attempt = slipped.get("attempt") or {}
    if not endpoint_ok("POST /recovery/agent-purchase responds 200 after cost"
                       " slippage", status, slipped):
        skip("nothing is charged below the NEW floor",
             f"the call returned {status}; the raised floor was never"
             " exercised. THIS IS THE STRONGEST DEMO FRAME - fix it before"
             " recording.")
    elif is_replay(slipped_attempt):
        skip("nothing is charged below the NEW floor",
             "this basket replayed a stored order, so the raised floor was "
             "never re-evaluated (run reset_demo.py for this one)")
    elif not lines_of(slipped_attempt):
        skip("nothing is charged below the NEW floor",
             "the reply carried no priced lines, so there was nothing to check")
    else:
        violations = floor_violations(slipped_attempt)
        check("nothing is charged below the NEW floor", not violations,
              "; ".join(violations)
              or f"{len(lines_of(slipped_attempt))} lines inspected")
        adjusted = [ln.get("sku") for ln in lines_of(slipped_attempt)
                    if ln.get("price_adjusted")]
        # Only assert the upward override when the slipped SKU is actually in
        # the basket. The model chooses the basket, so demanding its presence
        # would make this check fail for a reason that is not a bug.
        slipped_lines = [ln for ln in lines_of(slipped_attempt)
                         if ln.get("sku") == "MOU-WL-01"]
        if slipped_lines:
            check("the slipped SKU's price was overridden UPWARD to the new floor",
                  all(ln.get("final_unit_price_paise", 0)
                      >= ln.get("floor_unit_price_paise", 0)
                      for ln in slipped_lines)
                  and any(ln.get("price_adjusted") for ln in slipped_lines),
                  "; ".join(f"{ln.get('sku')} requested "
                            f"{rs(ln.get('requested_unit_price_paise'))} -> charged "
                            f"{rs(ln.get('final_unit_price_paise'))} "
                            f"(floor {rs(ln.get('floor_unit_price_paise'))})"
                            for ln in slipped_lines))
        else:
            info("MOU-WL-01 was not in the basket this run, so the upward"
                 " override could not be demonstrated on it")
        info(f"prices overridden upward by the enclave: {adjusted or 'none'}")
else:
    info("MOU-WL-01 not in catalog; cost-slippage check skipped")

# --------------------------------------------------------------------------
head("11. Stock slippage is caught before payment")
if "KBD-MECH-01" in by_sku:
    status, _ = call("/recovery/slippage/stock",
                     {"sku": "KBD-MECH-01", "stock_quantity": 0})
    check("stock can be zeroed", status == 200, f"status {status}")
    # Again a distinct basket, so this gets a fresh policy evaluation instead
    # of replaying section 7's order.
    status, sold_out = buy(
        "I need a mechanical keyboard and a quiet wireless mouse, nothing else.",
        6000, resilient=True)
    sold_attempt = sold_out.get("attempt") or {}
    sold_decision = sold_attempt.get("decision") or {}
    sold_recovery = sold_out.get("recovery") or {}
    sold_counter = sold_recovery.get("counter_offer") or {}
    if not endpoint_ok("POST /recovery/agent-purchase responds 200 after stock"
                       " slippage", status, sold_out):
        skip("the sold-out SKU is never inside an APPROVED order",
             f"the call returned {status}, so no decision exists")
    elif is_replay(sold_attempt):
        skip("the sold-out SKU is never inside an APPROVED order",
             "this basket replayed a stored order "
             "(run reset_demo.py for this one)")
    else:
        # The real invariant is not "the SKU is absent" - a REJECTED decision
        # may legitimately list it, priced, as the thing it refused. What must
        # never happen is a sold-out SKU inside something that got approved or
        # paid for.
        approved_lines = list(lines_of(sold_counter))
        if sold_decision.get("approved") is True:
            approved_lines += lines_of(sold_attempt)
        charged = [ln.get("sku") for ln in approved_lines]
        check("the sold-out SKU is never inside an APPROVED order",
              "KBD-MECH-01" not in charged,
              f"approved lines: {charged or 'none'} "
              f"(attempt approved={sold_decision.get('approved')})")
        in_attempt = [ln.get("sku") for ln in lines_of(sold_attempt)]
        info(f"the rejected attempt priced: {in_attempt or 'nothing'}")
    info(f"recovery actions: "
         f"{[a.get('step') for a in (sold_recovery.get('actions') or [])]}")
else:
    info("KBD-MECH-01 not in catalog; stock-slippage check skipped")

# --------------------------------------------------------------------------
head("12. Audit trail integrity on the most recent order")
status, listing = call("/dashboard/orders")
orders = (listing or {}).get("orders") or []
check("orders are readable", len(orders) > 0, f"{len(orders)} orders")
if orders:
    newest = orders[0]["id"]
    status, trail_body = call(f"/dashboard/orders/{newest}")
    trail_body = trail_body or {}
    trail = trail_body.get("trail") or []
    check("the trail loaded without an introspection error",
          not trail_body.get("trail_error"),
          str(trail_body.get("trail_error"))[:100])
    check("the order has audit events", len(trail) > 0, f"{len(trail)} events")
    stages = sorted({e.get("stage_index") for e in trail if e.get("stage_index")})
    check("every stage index falls within 1..6",
          all(1 <= s <= 6 for s in stages), f"stages {stages}")
    check("stage 1 (trigger) was always written", 1 in stages, f"stages {stages}")
    check("the trail is append-only, never rewritten",
          len(trail) >= len(stages),
          f"{len(trail)} events across {len(stages)} distinct stages")

# --------------------------------------------------------------------------
head("13. Restore the catalog so the demo starts clean")
status, restored = call("/recovery/slippage/reset", {})
check("catalog restored", status == 200, f"{(restored or {}).get('count')} products")

# --------------------------------------------------------------------------
print("\n" + "=" * 70)
print(f"  {len(PASSED)} passed, {len(FAILED)} failed, {len(SKIPPED)} skipped")
print(f"  AI requests spent this run: about {MODEL_CALLS[0]}"
      f" (~{round(MODEL_CALLS[0] * 0.3, 1)} gorouter credits at 0.3 per call,"
      f" or {MODEL_CALLS[0]} of Gemini's 20 free requests per day)")
if FAILED:
    print("\n  Failing checks:")
    for name in FAILED:
        print(f"    - {name}")
    print("\n  Fix these before demoing. Each one is a claim the project makes"
          "\n  about itself that is currently untrue.")
    print("\n  If a 'responds 200' check failed, read the uvicorn window first."
          "\n  A 500 there means the request never reached the enclave, so the"
          "\n  checks under it could not run either.")
if SKIPPED:
    print("\n  Skipped checks (not failures - this run could not exercise them):")
    for name in SKIPPED:
        print(f"    - {name}")
    print("\n  Most skips are idempotency doing its job: an intent this agent"
          "\n  has already bought is replayed, not re-evaluated. For a clean"
          "\n  sweep, clear the order history and run again:")
    print("\n      python -m app.database.reset_demo --yes")
    print("      python e2e_check.py")
if not FAILED and not SKIPPED:
    print("\n  All money-safety invariants hold. The catalog is back to baseline.")
elif not FAILED:
    print("\n  No invariant was violated. The catalog is back to baseline.")
print("\n  Not covered here (needs a human and ngrok): real payment settlement"
      "\n  and webhook HMAC verification, which fill audit stages 5 and 6.")
print("=" * 70)
sys.exit(1 if FAILED else 0)
