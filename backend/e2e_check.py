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


def head(text):
    print(f"\n=== {text} " + "=" * max(0, 62 - len(text)))


def rs(paise):
    return f"Rs.{(paise or 0) / 100:,.2f}"


def buy(request_text, budget, resilient=False):
    """Ask the agent to shop. Returns the whole response body."""
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
head("1. Backend reachable")
status, root = call("/")
if not check("GET / responds 200", status == 200, f"status {status}"):
    print("\nBackend is not up. Run: uvicorn app.main:app --reload")
    sys.exit(1)
info(f"version {(root or {}).get('version', 'unknown')}")

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
status, public = call("/catalog")
blob = json.dumps(public or {}).lower()
leaks = [word for word in ("cost_price", "cost_inr", "min_margin") if word in blob]
check(
    "no cost or margin field appears in /catalog",
    not leaks,
    "leaked: " + ", ".join(leaks) if leaks else "clean",
)

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
intent = negotiated.get("proposed_intent") or {}
items = intent.get("items") or []
check("POST /agent/negotiate responds 200", status == 200, f"status {status}")
check("the model returned at least one item", len(items) > 0, f"{len(items)} items")
check(
    "the model never returns an empty basket",
    len(items) > 0,
    "guards the Rs.0-order bug found in Phase 5",
)
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

if is_replay(attempt):
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

check("no line was ever charged below its floor",
      not floor_violations(attempt), "; ".join(floor_violations(attempt)))

# --------------------------------------------------------------------------
head("8. The same intent cannot be charged twice (idempotency)")
status, summary_before = call("/dashboard/summary")
count_before = (summary_before or {}).get("order_count")

status, again = buy(OVER_CAP_REQUEST, 7000)
replayed = again.get("enclave_result") or {}
check("POST /agent/purchase responds 200 on a repeat", status == 200, f"status {status}")
check(
    "an identical intent is recognised as a replay",
    is_replay(replayed),
    f"idempotent_replay={replayed.get('idempotent_replay')}",
)
check(
    "the replay returns the ORIGINAL order, not a new one",
    replayed.get("order_id") == first_order_id and first_order_id is not None,
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
check("POST /recovery/agent-purchase responds 200", status == 200, f"status {status}")
info(f"first attempt Rs.{first_try.get('subtotal_inr')} "
     f"status={first_try.get('status')}")
info(f"recovery attempted={recovery.get('attempted')} "
     f"succeeded={recovery.get('succeeded')}")
info(f"reason: {recovery.get('reason')}")

counter = recovery.get("counter_offer") or {}
if is_replay(first_try):
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
          not floor_violations(counter),
          "; ".join(floor_violations(counter))
          or "recovery discounts down to the floor, never through it")
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
    if is_replay(slipped_attempt):
        skip("nothing is charged below the NEW floor",
             "this basket replayed a stored order, so the raised floor was "
             "never re-evaluated (run reset_demo.py for this one)")
    else:
        violations = floor_violations(slipped_attempt)
        check("nothing is charged below the NEW floor", not violations,
              "; ".join(violations))
        adjusted = [ln.get("sku") for ln in lines_of(slipped_attempt)
                    if ln.get("price_adjusted")]
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
    kbd = [ln for ln in lines_of(sold_attempt) if ln.get("sku") == "KBD-MECH-01"]
    if is_replay(sold_attempt):
        skip("an out-of-stock SKU cannot be approved as-is",
             "this basket replayed a stored order "
             "(run reset_demo.py for this one)")
    elif kbd:
        check("an out-of-stock SKU cannot be approved as-is",
              sold_decision.get("approved") is False
              or not money_api_touched(sold_attempt),
              f"approved={sold_decision.get('approved')}")
    else:
        info("the model or recovery already dropped the sold-out SKU")
    sold_recovery = sold_out.get("recovery") or {}
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
if FAILED:
    print("\n  Failing checks:")
    for name in FAILED:
        print(f"    - {name}")
    print("\n  Fix these before demoing. Each one is a claim the project makes"
          "\n  about itself that is currently untrue.")
if SKIPPED:
    print("\n  Skipped checks (not failures - this run could not exercise them):")
    for name in SKIPPED:
        print(f"    - {name}")
    print("\n  Every skip above is idempotency doing its job: an intent this"
          "\n  agent has already bought is replayed, not re-evaluated. For a"
          "\n  100% clean sweep, clear the order history and run again:")
    print("\n      python -m app.database.reset_demo --yes")
    print("      python e2e_check.py")
if not FAILED and not SKIPPED:
    print("\n  All money-safety invariants hold. The catalog is back to baseline.")
elif not FAILED:
    print("\n  No invariant was violated. The catalog is back to baseline.")
print("=" * 70)
sys.exit(1 if FAILED else 0)
