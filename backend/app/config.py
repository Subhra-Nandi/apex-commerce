"""
Central configuration. Loads secrets from the .env file once and exposes
them as importable values. Nothing secret is hard-coded here.
"""

import os

from dotenv import load_dotenv

load_dotenv()


def _flag(name: str, default: str = "true") -> bool:
    """Read a yes/no setting from .env without crashing on odd spellings."""
    return (os.getenv(name, default) or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


# --- Razorpay (Test Mode) ---
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET")
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET")

# Optional. A Razorpay Offer created in the Dashboard (Test Mode). When set, it is
# attached to auto-recovery counter-offers. Everything works fine without it.
RAZORPAY_OFFER_ID = (os.getenv("RAZORPAY_OFFER_ID", "") or "").strip() or None

# --- Which provider leads the chain ---
# "gorouter" (the default now), "openrouter", or "gemini". This single word decides
# the order the router tries providers in; nothing else needs editing to swap them.
LLM_PRIMARY_PROVIDER = (
    os.getenv("LLM_PRIMARY_PROVIDER", "gorouter") or "gorouter"
).strip().lower()

# --- Primary LLM provider: gorouter.app (Claude Opus 5) ---
# gorouter.app is a "New API" gateway that speaks the OpenAI dialect, so the client
# code looks like the OpenRouter one. Two things are genuinely different and both
# will bite you if ignored:
#
#   1. MODEL IDS HAVE NO VENDOR PREFIX. It is "claude-opus-5", NOT
#      "anthropic/claude-opus-5". A prefixed id returns "no available channel",
#      which reads like an outage but is really a typo.
#   2. BILLING IS PER CALL, NOT PER TOKEN. Checked live on 2026-09-03 against the
#      public catalog at https://gorouter.app/api/pricing: both Opus 5 models report
#      quota_type 1 at model_price 0.3, i.e. a flat 0.3 credits per request. So a
#      long answer costs the same as a short one - but every retry is a fresh
#      charge. 50 credits is about 166 calls.
GOROUTER_API_KEY = os.getenv("GOROUTER_API_KEY")
GOROUTER_BASE_URL = os.getenv("GOROUTER_BASE_URL", "https://gorouter.app/v1")

# The model we actually want doing the negotiating.
GOROUTER_PRIMARY_MODEL = (
    os.getenv("GOROUTER_PRIMARY_MODEL", "claude-opus-5") or ""
).strip()

# Understudies on the same gateway, comma separated, tried after the pinned model.
# The thinking variant is the natural spare: same price, more deliberation.
GOROUTER_MODELS = os.getenv("GOROUTER_MODELS", "claude-opus-5-thinking")

# Retries for the pinned model when the gateway answers "busy" (429/5xx). This is 2
# rather than 3 on purpose: each attempt is another 0.3 credits. An out-of-credit or
# bad-key answer is never retried at all.
GOROUTER_MAX_ATTEMPTS = int(os.getenv("GOROUTER_MAX_ATTEMPTS", "2"))

# Total gorouter models the router may try in one call. Slot 0 - the model you
# pinned - can never be trimmed away.
GOROUTER_MAX_CANDIDATES = int(os.getenv("GOROUTER_MAX_CANDIDATES", "2"))

# --- Middle fallback: OpenRouter (optional) ---
# Leave OPENROUTER_API_KEY unset and this whole provider is skipped silently.
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

# The OpenRouter model to fall back to. PAID, so it is pinned by name here and
# deliberately skips the "is it free?" filter used for OpenRouter's backup chain.
OPENROUTER_PRIMARY_MODEL = (
    os.getenv("OPENROUTER_PRIMARY_MODEL", "anthropic/claude-sonnet-4.5") or ""
).strip()

# How many times to retry the pinned OpenRouter model when it answers "busy"
# (429/5xx). A refusal that will not fix itself - a bad key, an unknown model id,
# or an out-of-credit 402 - is never retried.
OPENROUTER_MAX_ATTEMPTS = int(os.getenv("OPENROUTER_MAX_ATTEMPTS", "3"))

# Optional manual chain, comma separated, tried after the pinned model and before
# free discovery. Example: OPENROUTER_MODELS=anthropic/claude-opus-4.5,openai/gpt-4o-mini
OPENROUTER_MODELS = os.getenv("OPENROUTER_MODELS", "")

# When the OpenRouter paid model is unreachable, fall back to whatever OpenRouter
# models are free right now. Set to false to forbid that.
OPENROUTER_FREE_FALLBACK = _flag("OPENROUTER_FREE_FALLBACK", "true")

# Total number of OpenRouter models the router may try in one call. The pinned
# model always occupies the first slot and can never be trimmed away.
OPENROUTER_MAX_CANDIDATES = int(os.getenv("OPENROUTER_MAX_CANDIDATES", "4"))

# --- Last-resort fallback: Google Gemini (optional) ---
# Keeping the key set costs nothing and buys a final escape hatch, but the API
# starts and runs perfectly with GEMINI_API_KEY absent.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_MAX_ATTEMPTS = int(os.getenv("GEMINI_MAX_ATTEMPTS", "3"))

# --- Policy defaults ---
STEP_UP_APPROVAL_THRESHOLD_INR = int(os.getenv("STEP_UP_APPROVAL_THRESHOLD", "2000"))
DEFAULT_MIN_MARGIN_PERCENTAGE = int(os.getenv("DEFAULT_MIN_MARGIN_PERCENTAGE", "15"))

# The largest discount the negotiator agent is *allowed to attempt*. The enclave
# still independently enforces the real margin floor - this is only a hint to the LLM.
MAX_AGENT_DISCOUNT_PERCENTAGE = int(os.getenv("MAX_AGENT_DISCOUNT_PERCENTAGE", "10"))

# Product categories treated as "accessories" during auto-recovery. These are the
# items the recovery planner is allowed to discount to their floor.
RECOVERY_ACCESSORY_CATEGORIES = tuple(
    category.strip()
    for category in os.getenv(
        "RECOVERY_ACCESSORY_CATEGORIES", "accessories,audio"
    ).split(",")
    if category.strip()
)
