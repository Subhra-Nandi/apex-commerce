"""
Turn an AI outage into an honest, safe answer instead of a 500 traceback.

WHY THIS FILE EXISTS
--------------------
Every negotiation calls a language model, and language models go away. Paid accounts
run out of credit (gorouter.app answers with an insufficient_user_quota message,
OpenRouter answers HTTP 402), free tiers run out of requests (Gemini's free tier
allows only 20 per DAY per model), providers have bad minutes, laptops lose wifi.
When every provider in the chain fails, `app/agents/llm_router.py` raises:

    RuntimeError: All LLM providers failed. Attempts: ...

Until now FastAPI turned that into a bare "500 Internal Server Error" with a
traceback in the terminal. Two things are wrong with that:

1. It reads as "the payment system broke". It did not. The request never got
   as far as the policy enclave, which means no order row was written, no
   Razorpay call was made and no money moved - but nothing in the response
   says any of that.
2. HTTP 500 means "we are broken". The truthful code is 503 Service
   Unavailable: "a dependency is down, try again later."

So we catch exactly that one failure and answer 503 with a plain sentence a
human can read, plus a machine-readable block a buyer agent can act on.

The primary model is now Claude Opus 5 on gorouter.app, billed PER CALL, so "out of
credit" is the single most likely cause of an outage. It gets its own diagnosis with
the top-up link and the exact env var to flip - a generic "try again later" would
send you hunting mid-demo.

This module is deliberately tiny and dependency-free so both route files can
share it without either one importing the other.
"""

from fastapi.responses import JSONResponse

# The marker the model router puts at the front of its error message.
_MARKER = "all llm providers failed"

# The 6-stage audit ledger: 1 Trigger, 2 Agent Reasoning, 3 Policy Evaluation,
# 4 Razorpay Payload, 5 Webhook Verification, 6 Final State. An AI outage can
# only ever fail at stage 2, which is why nothing downstream can have run.
FAILED_STAGE_INDEX = 2
FAILED_STAGE_NAME = "Agent Reasoning"


def is_llm_failure(error: BaseException) -> bool:
    """True only for 'every model provider failed', not for any other bug.

    Anything else must keep bubbling up as a real 500, because silently
    relabelling unknown crashes as 'service unavailable' would hide bugs.
    """
    return _MARKER in str(error).lower()


def _parse_attempts(message: str) -> list[str]:
    """Pull the per-provider attempt lines out of the router's message."""
    _, _, tail = message.partition("Attempts:")
    attempts = []
    for raw_line in tail.splitlines():
        line = raw_line.strip().lstrip("-").strip()
        if line:
            attempts.append(line[:300])
    return attempts


# How gorouter.app says "you are out of credit". It does NOT use HTTP 402, so the
# wording is the only signal. Kept separate from the OpenRouter needles because the
# remedy is different: a different dashboard and a different env var to flip.
_GOROUTER_CREDIT_NEEDLES = (
    "insufficient_user_quota",
    "quota is not enough",
    "额度不足",
)

_OPENROUTER_CREDIT_NEEDLES = (
    "402",
    "insufficient credit",
    "insufficient_quota",
    "requires more credits",
)


def _looks_like_gorouter_credit(message: str) -> bool:
    """Out of credit on gorouter.app specifically."""
    lowered = message.lower()
    return any(needle in lowered for needle in _GOROUTER_CREDIT_NEEDLES)


def _looks_like_credit(message: str) -> bool:
    """Out of money on a paid gateway, as opposed to merely rate limited."""
    lowered = message.lower()
    return _looks_like_gorouter_credit(message) or any(
        needle in lowered for needle in _OPENROUTER_CREDIT_NEEDLES
    )


def _looks_like_quota(message: str) -> bool:
    lowered = message.lower()
    return any(
        needle in lowered
        for needle in ("resource_exhausted", "429", "quota", "rate limit")
    )


def _diagnose(message: str) -> tuple[str, list[str]]:
    """Best guess at WHY every provider failed, plus what to do about it.

    Credit is checked BEFORE rate limits on purpose. An out-of-credit message from
    gorouter contains the word "quota", which would otherwise be diagnosed as a
    temporary rate limit and told to "wait 30 seconds" - advice that would never
    come true.
    """
    if _looks_like_gorouter_credit(message):
        return (
            "gorouter.app answered insufficient_user_quota - the account is out of"
            " credit. Claude Opus 5 there is billed per CALL (about 0.3 credits"
            " each), and this gateway reports an empty balance as a quota message,"
            " not as HTTP 402.",
            [
                "Top up at https://gorouter.app - the balance is on your dashboard.",
                "Or set LLM_PRIMARY_PROVIDER=gemini in backend/.env and restart"
                " uvicorn to finish the demo on the backup provider.",
                "Check GET /agent/llm-status - it reports"
                " billable_calls_this_process, so you can see what this session"
                " has already spent.",
            ],
        )

    if _looks_like_credit(message):
        return (
            "OpenRouter refused with HTTP 402 - the account is out of credit."
            " The primary model (Anthropic Claude) is a paid model, so it stops"
            " answering the moment the balance reaches zero.",
            [
                "Top up at https://openrouter.ai/credits - a couple of dollars"
                " covers a whole demo.",
                "Or set OPENROUTER_FREE_FALLBACK=true in backend/.env so the free"
                " model chain takes over, then restart uvicorn.",
                "Check GET /agent/llm-status to see the chain that will be tried.",
            ],
        )

    if _looks_like_quota(message):
        return (
            "Every provider refused with a rate-limit or quota error. Paid"
            " OpenRouter models are rate limited per minute, free ones far more"
            " tightly, and Gemini's free tier allows only 20 requests per day per"
            " model (daily quotas reset at midnight Pacific = 12:30 PM IST).",
            [
                "Wait 30-60 seconds and retry - per-minute limits clear quickly.",
                "Check GET /agent/llm-status and confirm failover_armed is true.",
                "Add OPENROUTER_API_KEY or GEMINI_API_KEY in backend/.env for a"
                " second escape hatch, then restart uvicorn (.env is read once at"
                " startup).",
            ],
        )

    return (
        "Every configured model provider failed to answer.",
        [
            "Check your internet connection.",
            "Check GET /agent/llm-status to see which providers are configured.",
            "Confirm GOROUTER_PRIMARY_MODEL is a real model id for that gateway."
            " Ids there carry NO vendor prefix - 'claude-opus-5', not"
            " 'anthropic/claude-opus-5' - and a wrong id reads as 'no available"
            " channel', which is never retried.",
            "Retry in a few seconds - this is usually transient.",
        ],
    )


def unavailable_response(error: BaseException, *, endpoint: str) -> JSONResponse:
    """Build the 503 body. Never raises, so it is safe inside an except block."""
    message = str(error)
    attempts = _parse_attempts(message)
    cause, what_to_do = _diagnose(message)

    human = (
        "The negotiation model is unavailable, so no purchase was attempted."
        " Nothing was ordered and no payment was started. The policy enclave"
        " was never reached, so no money could have moved. Try again shortly."
    )

    return JSONResponse(
        status_code=503,
        headers={"Retry-After": "60"},
        content={
            # api.js reads .detail and shows it verbatim, so keep this a
            # plain readable string - not a nested object.
            "detail": human,
            "error": {
                "code": "llm_unavailable",
                "endpoint": endpoint,
                "failed_stage_index": FAILED_STAGE_INDEX,
                "failed_stage_name": FAILED_STAGE_NAME,
                "order_created": False,
                "razorpay_called": False,
                "money_moved": False,
                "provider_attempts": attempts,
                "likely_cause": cause,
                "what_to_do": what_to_do,
            },
        },
    )
