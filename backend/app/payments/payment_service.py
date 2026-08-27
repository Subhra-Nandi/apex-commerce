"""
All Razorpay interactions live here:
  - create_order:           make a Razorpay Order (amount in paise)
  - create_payment_link:    make a hosted checkout link (for step-up approval)
  - verify_payment_signature:  verify a Checkout success callback (HMAC)
  - verify_webhook_signature:  verify an inbound webhook (HMAC)

Both verification functions use HMAC-SHA256 exactly as Razorpay documents,
and compare with hmac.compare_digest to prevent timing attacks.
"""

import hashlib
import hmac
from typing import Any, Optional

from app.config import RAZORPAY_KEY_SECRET, RAZORPAY_WEBHOOK_SECRET
from app.payments.razorpay_client import client


def create_order(
    amount_paise: int,
    currency: str = "INR",
    receipt: Optional[str] = None,
    notes: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    Create a Razorpay Order. This does NOT charge anyone; it registers an
    intended payment that a checkout can later fulfil.
    """
    payload: dict[str, Any] = {
        "amount": amount_paise,     # Razorpay expects paise
        "currency": currency,
        "payment_capture": 1,       # auto-capture once paid
    }
    if receipt:
        payload["receipt"] = receipt[:40]   # Razorpay caps receipt at 40 chars
    if notes:
        payload["notes"] = notes
    return client.order.create(data=payload)


def create_payment_link(
    amount_paise: int,
    description: str,
    currency: str = "INR",
    customer_name: str = "Demo Buyer",
    customer_email: str = "demo@apexcommerce.test",
    customer_contact: str = "+919812345670",
    notes: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    Create a hosted Razorpay Payment Link. The returned 'short_url' is a page
    the human opens to pay. We use this for the >Rs.2,000 step-up approval flow.
    """
    payload: dict[str, Any] = {
        "amount": amount_paise,
        "currency": currency,
        "accept_partial": False,
        "description": description[:2048],
        "customer": {
            "name": customer_name,
            "email": customer_email,
            "contact": customer_contact,
        },
        "notify": {"sms": False, "email": False},
        "reminder_enable": False,
    }
    if notes:
        payload["notes"] = notes
    return client.payment_link.create(payload)


def verify_payment_signature(order_id: str, payment_id: str, signature: str) -> bool:
    """
    Verify a Razorpay Checkout success callback.
    Razorpay computes: HMAC_SHA256(order_id + "|" + payment_id, key_secret).
    """
    message = f"{order_id}|{payment_id}".encode()
    expected = hmac.new(
        RAZORPAY_KEY_SECRET.encode(), message, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_webhook_signature(raw_body: bytes, signature: str) -> bool:
    """
    Verify an inbound webhook.
    Razorpay computes: HMAC_SHA256(raw_request_body, webhook_secret).
    IMPORTANT: we must hash the EXACT raw bytes of the body, not a re-serialized
    version, or the signature will never match.
    """
    if not RAZORPAY_WEBHOOK_SECRET:
        raise RuntimeError(
            "RAZORPAY_WEBHOOK_SECRET is not set in backend/.env. It must match "
            "the secret you enter in the Razorpay dashboard webhook settings."
        )
    if not signature:
        return False
    expected = hmac.new(
        RAZORPAY_WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)