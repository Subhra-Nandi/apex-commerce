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

# --- Policy defaults (used from Phase 4 onward) ---
STEP_UP_APPROVAL_THRESHOLD_INR = int(os.getenv("STEP_UP_APPROVAL_THRESHOLD", "2000"))
DEFAULT_MIN_MARGIN_PERCENTAGE = int(os.getenv("DEFAULT_MIN_MARGIN_PERCENTAGE", "15"))