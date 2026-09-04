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
    """Pull the per-provider attempt lines out of the router's message.

    llm_router joins its failures with "\\n- ", so that - and NOT every newline - is
    the real delimiter. It matters: when a provider fails with a web page instead of
    JSON, that page brings its own newlines, and splitting on all of them shredded
    one failed provider into thirty fragments of markup that buried the other
    providers' reasons. Each attempt is flattened to a single line here.
    """
    _, _, tail = message.partition("Attempts:")
    attempts = []
    for chunk in tail.split("\n- "):
        flat = " ".join(chunk.split()).lstrip("-").strip()
        if flat:
            attempts.append(flat[:300])
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


def _looks_like_cloudflare_block(message: str) -> bool:
    """The request never reached the gateway - an edge proxy answered instead.

    Three ways to spot it, because relying on the first alone let a live block
    through on 2026-09-04 and the 503 then advised "wait 30-60 seconds":

      1. the phrase this codebase writes on purpose (gorouter_client);
      2. Cloudflare's own branding, wherever in the page it lands;
      3. failing both - a refusal status carrying MARKUP. gorouter answers JSON to
         everything, so markup means the gateway never saw the request. This also
         catches an unbranded corporate proxy, and an older client build whose
         message is just "gorouter HTTP 403: <!DOCTYPE html>...".
    """
    lowered = message.lower()
    if "blocked by cloudflare" in lowered:
        return True
    if any(
        needle in lowered
        for needle in ("cloudflare", "attention required", "just a moment", "cf-ray")
    ):
        return True
    markup = any(
        needle in lowered for needle in ("<html", "<!doctype", "<head", "<title")
    )
    return markup and any(code in lowered for code in ("401", "403", "429"))


# Gemini's free tier is capped per DAY, not per minute, so "wait 30-60 seconds" is
# actively wrong advice for it. The quotaId in Google's own 429 names the window:
#   quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier
_DAILY_QUOTA_NEEDLES = ("perdayper", "per day", "requests per day", "freetier")


def _looks_like_daily_quota(message: str) -> bool:
    """A quota that resets on a CLOCK, not in a few seconds."""
    lowered = message.lower().replace(" ", "")
    return any(
        needle.replace(" ", "") in lowered for needle in _DAILY_QUOTA_NEEDLES
    ) or ("resource_exhausted" in lowered and "gemini" in lowered)


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

    The Cloudflare check comes first of all, because a request that died at the edge
    tells us NOTHING about credit, keys or model ids - and diagnosing it as any of
    those sends you looking in the wrong place.
    """
    if _looks_like_cloudflare_block(message):
        cause = (
            "The request never reached gorouter.app. Cloudflare, which sits in front"
            " of that gateway, answered with a web page instead of JSON. Nothing was"
            " billed and no model was asked. This is a network or request-header"
            " problem, NOT a credit, key or model-id problem."
        )
        actions = [
            "Open https://gorouter.app/api/pricing in your browser. If it shows JSON,"
            " your network is fine and the block is aimed at the Python client; if it"
            " shows a Cloudflare page, the block is aimed at this IP address.",
            "If a corporate proxy, VPN or antivirus is inspecting HTTPS on this"
            " machine, turn it off for the demo or switch networks (a phone hotspot is"
            " the quickest test).",
            "Confirm backend/app/agents/gorouter_client.py still sends a browser"
            " User-Agent (see _USER_AGENT near the top of that file) - the default"
            " python-requests one is refused with HTTP 403.",
        ]

        # The escape hatch is only an escape hatch if the backup can actually answer.
        # When Gemini has ALSO refused - and its free tier is a DAILY cap, not a
        # per-minute one - saying "switch to Gemini" would send you round in a circle.
        if _looks_like_daily_quota(message):
            cause += (
                " The Gemini fallback then refused too, for a completely different"
                " reason: its free tier allows only 20 requests per DAY per model and"
                " today's allowance is spent. So both ends of the chain are down at"
                " once, which is why nothing answered."
            )
            actions.append(
                "Gemini's daily allowance resets at midnight Pacific = 12:30 PM IST,"
                " so the backup provider will start answering again by itself. Until"
                " then, fixing the network above is the only way to get a model."
            )
        else:
            actions.append(
                "Or set LLM_PRIMARY_PROVIDER=gemini in backend/.env and restart"
                " uvicorn to finish the demo on the backup provider."
            )

        return cause, actions

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
        if _looks_like_daily_quota(message):
            # A DAILY cap does not clear in 30 seconds, so it gets its own advice.
            # Telling someone to "wait a minute and retry" for a quota that resets at
            # midnight is how a demo gets retried into the ground.
            return (
                "Every provider refused with a quota error, and at least one of them"
                " was a DAILY allowance rather than a per-minute rate limit. Gemini's"
                " free tier allows only 20 requests per day per model, and daily"
                " quotas reset at midnight Pacific = 12:30 PM IST.",
                [
                    "Retrying now will not help: this allowance resets on a clock, at"
                    " 12:30 PM IST.",
                    "Use the paid primary instead - set LLM_PRIMARY_PROVIDER=gorouter"
                    " in backend/.env, confirm credit at GET /agent/llm-status, and"
                    " restart uvicorn (.env is read once at startup).",
                    "Or add a billing account to the Gemini key at"
                    " https://aistudio.google.com to lift the free-tier cap.",
                ],
            )
        return (
            "Every provider refused with a rate-limit or quota error. Claude Opus 5 on"
            " gorouter.app is rate limited per minute, OpenRouter's free models far"
            " more tightly, and Gemini's free tier allows only 20 requests per day per"
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
