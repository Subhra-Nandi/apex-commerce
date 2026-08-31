"""
All Razorpay input/output lives here, plus both HMAC signature verifiers.
Nothing in this file makes a policy decision - it only talks to Razorpay.
"""

import hashlib
import hmac

import requests
from requests.auth import HTTPBasicAuth

from app.config import RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, RAZORPAY_WEBHOOK_SECRET
from app.payments.razorpay_client import client


def create_order(
    amount_paise: int,
    currency: str = "INR",
    receipt: str | None = None,
    notes: dict | None = None,
    offer_id: str | None = None,
) -> dict:
    """
    Create a Razorpay Order. `offer_id` is optional and only used by auto-recovery
    counter-offers; it must be an offer you created in the Razorpay Dashboard.
    """
    payload: dict = {
        "amount": amount_paise,
        "currency": currency,
        "payment_capture": 1,
    }
    if receipt:
        payload["receipt"] = receipt[:40]   # Razorpay caps receipt at 40 chars
    if notes:
        payload["notes"] = notes
    if offer_id:
        payload["offer_id"] = offer_id
    return client.order.create(data=payload)


def create_payment_link(
    amount_paise: int,
    description: str,
    currency: str = "INR",
    customer_name: str = "Demo Buyer",
    customer_email: str = "demo@apexcommerce.test",
    customer_contact: str = "+919812345670",
    notes: dict | None = None,
) -> dict:
    """
    Create a Razorpay Payment Link. This is our step-up human approval channel:
    the human opens the link and confirms with UPI, so the agent alone cannot
    complete a high-value purchase.
    """
    payload: dict = {
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


def list_offers() -> list[dict]:
    """
    Fetch offers configured in the Razorpay Dashboard. Offers cannot be created
    through the API on test accounts, so this is read-only. Returns an empty list
    on any error - a missing offer must never break a checkout.
    """
    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        return []
    try:
        response = requests.get(
            "https://api.razorpay.com/v1/offers",
            auth=HTTPBasicAuth(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET),
            timeout=20,
        )
        if response.status_code >= 400:
            return []
        return response.json().get("items", []) or []
    except Exception:  # noqa: BLE001 - diagnostics only, never fatal
        return []


def verify_payment_signature(order_id: str, payment_id: str, signature: str) -> bool:
    """Verifies a Checkout callback: HMAC-SHA256 over 'order_id|payment_id'."""
    message = f"{order_id}|{payment_id}".encode()
    expected = hmac.new(
        RAZORPAY_KEY_SECRET.encode(), message, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_webhook_signature(raw_body: bytes, signature: str) -> bool:
    """
    Verifies a webhook: HMAC-SHA256 over the RAW request body using the webhook
    secret. The raw bytes matter - re-serialised JSON will not match.
    """
    if not RAZORPAY_WEBHOOK_SECRET:
        raise RuntimeError(
            "RAZORPAY_WEBHOOK_SECRET is not set. Add it to backend/.env and paste "
            "the identical value into the Razorpay Dashboard webhook config."
        )
    if not signature:
        return False
    expected = hmac.new(
        RAZORPAY_WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)