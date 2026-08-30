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

# --- Google Gemini ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# --- Policy defaults ---
STEP_UP_APPROVAL_THRESHOLD_INR = int(os.getenv("STEP_UP_APPROVAL_THRESHOLD", "2000"))
DEFAULT_MIN_MARGIN_PERCENTAGE = int(os.getenv("DEFAULT_MIN_MARGIN_PERCENTAGE", "15"))

# The largest discount the negotiator agent is *allowed to attempt*. The enclave
# still independently enforces the real margin floor - this is only a hint to the LLM.
MAX_AGENT_DISCOUNT_PERCENTAGE = int(os.getenv("MAX_AGENT_DISCOUNT_PERCENTAGE", "10"))