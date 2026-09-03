"""
gorouter.app client - the PRIMARY brain of APEX-Commerce.

WHAT GOROUTER IS
----------------
https://gorouter.app is a "New API" gateway: one OpenAI-compatible endpoint in
front of a small curated model list. It speaks the exact dialect this codebase
already speaks, so nothing downstream changes:

    POST https://gorouter.app/v1/chat/completions
    Authorization: Bearer sk-...
    {"model": "claude-opus-5", "messages": [...]}

HOW IT DIFFERS FROM OPENROUTER - AND WHY THIS IS A SEPARATE FILE
---------------------------------------------------------------
1. MODEL IDS CARRY NO VENDOR PREFIX. It is "claude-opus-5", NOT
   "anthropic/claude-opus-5". Paste an OpenRouter-style id in and the gateway
   answers "no available channel", which reads like an outage but is a typo.

2. THERE ARE NO FREE MODELS HERE, so the free-model discovery that guards the
   OpenRouter chain is meaningless. We pin the model list instead.

3. BILLING IS PER CALL, NOT PER TOKEN. Checked live against the public pricing
   endpoint on 2026-09-03: claude-opus-5 and claude-opus-5-thinking both report
   quota_type 1 (flat rate per request) at model_price 0.3. Two consequences:
     - a long answer costs exactly the same as a short one, so _MAX_OUTPUT_TOKENS
       below is deliberately generous;
     - every RETRY is a fresh charge, which is why GOROUTER_MAX_ATTEMPTS defaults
       to 2 instead of 3.

4. THE CATALOG IS PUBLIC. GET /api/pricing needs no API key at all, so
   /agent/llm-status can prove your pinned model really exists, and tell you what
   it costs, before you spend a single credit.

5. RUNNING OUT OF CREDIT IS NOT AN HTTP 402. This gateway answers with a quota
   message instead (insufficient_user_quota / user quota is not enough / a
   Chinese equivalent), so _describe_http_error below deliberately keeps those
   words in the message and llm_router matches them as PERMANENT - never retried.

Nothing in this file can move money. It returns text; app/agents/llm_router.py
validates that text against a Pydantic schema, and the deterministic policy
enclave validates the result AGAIN before Razorpay is ever called.
"""

import json
from typing import Any, Type

import requests
from pydantic import BaseModel

from app.config import (
    GOROUTER_API_KEY,
    GOROUTER_BASE_URL,
    GOROUTER_MAX_CANDIDATES,
    GOROUTER_MODELS,
    GOROUTER_PRIMARY_MODEL,
)

_TIMEOUT_SECONDS = 120

# Per-CALL billing means length is free, and the thinking variant spends tokens on
# reasoning before it answers. Being stingy here would truncate valid JSON.
_MAX_OUTPUT_TOKENS = 8000

# Catalog from GET /api/pricing. Populated lazily; needs no API key.
_cached_catalog: dict[str, dict[str, Any]] | None = None

# Billable calls made since uvicorn started, so /agent/llm-status can tell you
# what the rehearsal has cost. Resets on restart - it is a meter, not a ledger.
_billable_calls = 0
_estimated_credits = 0.0

def is_configured() -> bool:
    return bool(GOROUTER_API_KEY)


def primary_model() -> str:
    """The pinned model, or "" if the operator blanked it out."""
    return GOROUTER_PRIMARY_MODEL


def manual_models() -> list[str]:
    """Extra models from GOROUTER_MODELS, tried after the pinned one."""
    return [entry.strip() for entry in GOROUTER_MODELS.split(",") if entry.strip()]


def candidate_models() -> list[str]:
    """
    The exact order the router will try, best first:

        1. GOROUTER_PRIMARY_MODEL  - the model you want doing the thinking
        2. GOROUTER_MODELS         - comma-separated understudies

    No free-model discovery happens here, because this gateway has no free models.
    Duplicates are dropped, order is preserved, and slot 0 can never be trimmed by
    GOROUTER_MAX_CANDIDATES - the model you pinned always gets its turn.
    """
    ordered: list[str] = []

    def add(model_id: str) -> None:
        if model_id and model_id not in ordered:
            ordered.append(model_id)

    add(primary_model())
    for model_id in manual_models():
        add(model_id)

    return ordered[: max(1, GOROUTER_MAX_CANDIDATES)]


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {GOROUTER_API_KEY}",
        "Content-Type": "application/json",
    }


def _site_root() -> str:
    """
    https://gorouter.app/v1  ->  https://gorouter.app

    The public pricing endpoint sits at the site root, NOT under /v1.
    """
    base = GOROUTER_BASE_URL.rstrip("/")
    return base[: -len("/v1")] if base.endswith("/v1") else base


def discover_catalog(force_refresh: bool = False) -> dict[str, dict[str, Any]]:
    """
    Read the gateway's public model catalog. NO API KEY REQUIRED.

    GET https://gorouter.app/api/pricing lists every model this gateway serves and
    how it bills for each:

        quota_type 1 -> flat price per call, in `model_price`
        quota_type 0 -> per token, via `model_ratio` / `completion_ratio`

    We use it for one honest purpose: proving at /agent/llm-status that the model
    id you pinned really exists, instead of burning a credit to find out.
    """
    global _cached_catalog

    if _cached_catalog is not None and not force_refresh:
        return _cached_catalog

    # Short timeout on purpose: this runs while /agent/llm-status is loading, and a
    # slow diagnostic during a demo is worse than a missing one.
    response = requests.get(f"{_site_root()}/api/pricing", timeout=10)
    response.raise_for_status()
    payload = response.json()

    catalog: dict[str, dict[str, Any]] = {}
    for entry in payload.get("data") or []:
        name = entry.get("model_name")
        if not name:
            continue
        per_call = int(entry.get("quota_type") or 0) == 1
        catalog[name] = {
            "billing": "per_call" if per_call else "per_token",
            "per_call_price": float(entry.get("model_price") or 0.0) if per_call else None,
            "endpoints": entry.get("supported_endpoint_types") or [],
        }

    _cached_catalog = catalog
    return catalog


def catalog_check(force_refresh: bool = True) -> dict[str, Any]:
    """
    Catalog report for the status endpoint. NEVER raises - a diagnostic that can
    500 the page you opened to diagnose a problem is worse than no diagnostic.
    """
    report: dict[str, Any] = {
        "reachable": False,
        "models_offered": [],
        "pinned_model_found": None,
        "pinned_model_per_call_price": None,
        "error": None,
    }
    try:
        catalog = discover_catalog(force_refresh=force_refresh)
    except Exception as error:  # noqa: BLE001 - see docstring
        report["error"] = str(error)[:300]
        return report

    pinned = primary_model()
    report["reachable"] = True
    report["models_offered"] = sorted(catalog)
    report["pinned_model_found"] = (pinned in catalog) if pinned else None
    if pinned in catalog:
        report["pinned_model_per_call_price"] = catalog[pinned]["per_call_price"]
    return report


def fetch_balance() -> dict[str, Any]:
    """
    Best-effort credit read. NEVER raises.

    New API serves OpenAI's billing shape at /v1/dashboard/billing/subscription and
    /usage, mapping its own credit quota onto fields named after dollars. That
    mapping is undocumented, so we report the raw numbers and say plainly that the
    gorouter.app dashboard is the authority.
    """
    report: dict[str, Any] = {
        "checked": False,
        "reported_limit": None,
        "used": None,
        "remaining": None,
        "error": None,
        "note": (
            "gorouter.app reuses OpenAI's billing field names for its own credit "
            "quota, so treat these figures as indicative."
        ),
    }
    if not GOROUTER_API_KEY:
        report["error"] = "GOROUTER_API_KEY not set"
        return report

    base = GOROUTER_BASE_URL.rstrip("/")
    try:
        subscription = requests.get(
            f"{base}/dashboard/billing/subscription", headers=_headers(), timeout=8
        )
        if subscription.status_code >= 400:
            report["error"] = f"HTTP {subscription.status_code}: {subscription.text[:160]}"
            return report

        limit = subscription.json().get("hard_limit_usd")
        report["checked"] = True
        report["reported_limit"] = float(limit) if limit is not None else None

        usage = requests.get(
            f"{base}/dashboard/billing/usage", headers=_headers(), timeout=8
        )
        if usage.status_code < 400:
            spent = usage.json().get("total_usage")  # cents, as OpenAI reports it
            report["used"] = round(float(spent) / 100.0, 4) if spent is not None else None
        if report["reported_limit"] is not None and report["used"] is not None:
            report["remaining"] = round(report["reported_limit"] - report["used"], 4)
    except Exception as error:  # noqa: BLE001 - a balance read must never 500 a page
        report["error"] = str(error)[:300]

    return report


def describe() -> dict[str, Any]:
    """Config snapshot for /agent/llm-status. Makes NO network call."""
    return {
        "provider": "gorouter",
        "configured": is_configured(),
        "base_url": GOROUTER_BASE_URL,
        "pinned_model": primary_model() or None,
        "manual_models": manual_models(),
        "will_try_in_order": candidate_models(),
        "max_candidates": max(1, GOROUTER_MAX_CANDIDATES),
        "billing": "per call - a retry is a fresh charge",
        "billable_calls_this_process": _billable_calls,
        "estimated_credits_spent": round(_estimated_credits, 4),
    }


def _record_call(model: str) -> None:
    """
    Count one billable call.

    Per-call pricing means one completion is one charge, so this meter stays honest
    as long as we only call it after a reply has actually arrived. It reads the
    cached catalog price and never makes a network call of its own - metering must
    not slow a negotiation down.
    """
    global _billable_calls, _estimated_credits

    _billable_calls += 1
    price = ((_cached_catalog or {}).get(model) or {}).get("per_call_price")
    if isinstance(price, (int, float)):
        _estimated_credits += float(price)


def _build_system_instruction(system_instruction: str, schema: Type[BaseModel]) -> str:
    """
    Paste the JSON Schema into the prompt itself.

    Claude honours response_format, but restating the schema in plain text costs
    nothing under per-call billing and removes a whole class of failure. llm_router
    validates the reply against the same schema afterwards regardless.
    """
    schema_json = json.dumps(schema.model_json_schema(), indent=2)
    return (
        f"{system_instruction}\n\n"
        "OUTPUT FORMAT - THIS IS MANDATORY:\n"
        "Reply with a single raw JSON object and NOTHING else. No markdown code "
        "fences, no explanation before or after, no comments. The object must "
        "validate against this JSON Schema:\n"
        f"{schema_json}"
    )


# What this gateway says when the account is out of credit. It answers with a
# message rather than an HTTP 402, so the wording is all we have to go on.
_QUOTA_HINTS = (
    "insufficient_user_quota",
    "quota is not enough",
    "user quota",
    "额度不足",  # "insufficient quota" in Chinese
    "余额不足",  # "insufficient balance" in Chinese
)


def _describe_http_error(model: str, response: requests.Response) -> RuntimeError:
    """
    Turn a gateway failure into one sentence a beginner can act on.

    IMPORTANT: the literal strings "insufficient_user_quota", "invalid token" and
    "no available channel" are kept in the message text ON PURPOSE - those are
    exactly what llm_router._PERMANENT_MARKERS matches. Client and router therefore
    agree by construction, so a hopeless call is never retried and, under per-call
    billing, never charged twice.
    """
    body = (response.text or "")[:400]
    lowered = body.lower()

    if any(hint in lowered for hint in _QUOTA_HINTS):
        return RuntimeError(
            f"gorouter insufficient_user_quota for '{model}': this account is out of "
            f"credit. Top up at https://gorouter.app, or set "
            f"LLM_PRIMARY_PROVIDER=gemini in backend/.env and restart uvicorn to "
            f"finish the demo on the backup provider. Detail: {body}"
        )

    if response.status_code == 401:
        return RuntimeError(
            f"gorouter HTTP 401 invalid token: GOROUTER_API_KEY is wrong, expired, or "
            f"has no access to '{model}'. Copy the key again from "
            f"https://gorouter.app and restart uvicorn - backend/.env is read once at "
            f"startup. Detail: {body}"
        )

    if "no available channel" in lowered or "无可用渠道" in body:
        return RuntimeError(
            f"gorouter reports no available channel for model id '{model}'. On this "
            f"gateway model ids carry NO vendor prefix: it is 'claude-opus-5', not "
            f"'anthropic/claude-opus-5'. Open http://127.0.0.1:8000/agent/llm-status "
            f"to see the exact ids this gateway offers. Detail: {body}"
        )

    if response.status_code >= 400:
        return RuntimeError(f"gorouter HTTP {response.status_code}: {body}")

    # A 200 carrying an error object. Rare, but New API does it.
    return RuntimeError(f"gorouter error for '{model}': {body}")


def generate_json(
    *,
    model: str,
    system_instruction: str,
    prompt: str,
    schema: Type[BaseModel],
    temperature: float = 0.2,
) -> str:
    """
    Call ONE model on gorouter.app and return its raw text reply.

    Deliberately the same signature as openrouter_client.generate_json, so
    llm_router can drive both providers with one shared walker.
    """
    if not GOROUTER_API_KEY:
        raise RuntimeError(
            "GOROUTER_API_KEY is missing. Add it to backend/.env - copy the key from "
            "https://gorouter.app - then restart uvicorn, because .env is read once "
            "at startup."
        )

    url = f"{GOROUTER_BASE_URL.rstrip('/')}/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "max_tokens": _MAX_OUTPUT_TOKENS,
        "messages": [
            {
                "role": "system",
                "content": _build_system_instruction(system_instruction, schema),
            },
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
    }

    response = requests.post(
        url, headers=_headers(), json=payload, timeout=_TIMEOUT_SECONDS
    )

    # A rejected request is never billed, so retrying once without the hint is free.
    if response.status_code >= 400 and "response_format" in response.text:
        payload.pop("response_format", None)
        response = requests.post(
            url, headers=_headers(), json=payload, timeout=_TIMEOUT_SECONDS
        )

    if response.status_code >= 400:
        raise _describe_http_error(model, response)

    data = response.json()
    if isinstance(data.get("error"), dict):
        # New API can answer 200 with an error object; out of credit arrives this
        # way. Same helper, so the router classifies it identically.
        raise _describe_http_error(model, response)

    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"gorouter returned no choices for '{model}'.")

    # A real completion came back, which means a real charge. Meter it once, here,
    # before any of the checks below can raise.
    _record_call(model)

    choice = choices[0]
    message = choice.get("message") or {}
    content = (message.get("content") or "").strip()
    if not content:  # the -thinking variant may answer in a reasoning field instead
        content = (message.get("reasoning") or "").strip()
    if not content:
        content = (message.get("reasoning_content") or "").strip()

    if not content:
        raise RuntimeError(f"gorouter returned an empty message for '{model}'.")

    # A reply cut off at the token limit is unparseable JSON. Say so plainly rather
    # than letting it surface as a confusing schema-validation error.
    if choice.get("finish_reason") == "length":
        raise RuntimeError(
            f"gorouter reply from '{model}' hit the {_MAX_OUTPUT_TOKENS}-token limit "
            f"and is incomplete JSON. Raise _MAX_OUTPUT_TOKENS in "
            f"backend/app/agents/gorouter_client.py - billing here is per call, so a "
            f"longer reply costs exactly the same."
        )

    return content
