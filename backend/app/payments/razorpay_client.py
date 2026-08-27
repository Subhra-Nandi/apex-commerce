"""
Creates a single, authenticated Razorpay client used across the app.
Fails loudly at startup if the keys are missing, so you never send a
half-configured request.
"""

import razorpay

from app.config import RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET

if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
    raise RuntimeError(
        "Razorpay keys missing. Add RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET "
        "to your backend/.env file (use your rzp_test_ keys)."
    )

# The shared client. auth is a (key_id, key_secret) tuple.
client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
client.set_app_details({"title": "APEX-Commerce", "version": "0.1.0"})