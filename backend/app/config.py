"""
Central configuration. Loads secrets from the .env file once and exposes
them as importable values. Nothing secret is hard-coded here.
"""

import os

from dotenv import load_dotenv

load_dotenv()

# --- Razorpay (Test Mode) ---
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET")
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET")


# attached to auto-recovery counter-offers
RAZORPAY_OFFER_ID = (os.getenv("RAZORPAY_OFFER_ID", "") or "").strip() or None

# --- Primary LLM provider: Google Gemini ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_MAX_ATTEMPTS = int(os.getenv("GEMINI_MAX_ATTEMPTS", "3"))

# --- Fallback LLM provider: OpenRouter (free models) ---
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_MODELS = os.getenv("OPENROUTER_MODELS", "")
OPENROUTER_MAX_CANDIDATES = int(os.getenv("OPENROUTER_MAX_CANDIDATES", "4"))

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