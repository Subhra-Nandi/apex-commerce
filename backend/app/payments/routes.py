"""
Payment and webhook API routes.

    POST /payments/test/order         -> create a test Razorpay order
    POST /payments/test/payment-link  -> create a hosted payment link
    POST /webhooks/razorpay           -> receive & verify inbound webhooks
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import RAZORPAY_KEY_ID
from app.database.models import Order
from app.database.session import get_db
from app.payments import payment_service

router = APIRouter(tags=["Payments"])


# ---------- Request body shapes ----------
class OrderRequest(BaseModel):
    amount_inr: float = Field(..., gt=0, description="Amount in rupees, e.g. 1499.00")
    description: str = "APEX-Commerce test order"


class PaymentLinkRequest(BaseModel):
    amount_inr: float = Field(..., gt=0)
    description: str = "APEX-Commerce step-up approval"


def _inr_to_paise(amount_inr: float) -> int:
    """Convert rupees to integer paise safely, e.g. 1499.0 -> 149900."""
    return int(round(amount_inr * 100))


# ---------- Outbound: create order ----------
@router.post("/payments/test/order")
def create_test_order(body: OrderRequest):
    """Create a Razorpay order. Returns the order plus the public key_id
    (a frontend checkout needs the key_id to open the payment popup)."""
    order = payment_service.create_order(
        amount_paise=_inr_to_paise(body.amount_inr),
        receipt="apex_test_order",
        notes={"source": "apex-commerce", "purpose": body.description},
    )
    return {"razorpay_key_id": RAZORPAY_KEY_ID, "order": order}


# ---------- Outbound: create payment link ----------
@router.post("/payments/test/payment-link")
def create_test_payment_link(body: PaymentLinkRequest):
    """Create a hosted payment link. Open the returned short_url in a browser
    and pay with a Razorpay test card to trigger a real webhook."""
    link = payment_service.create_payment_link(
        amount_paise=_inr_to_paise(body.amount_inr),
        description=body.description,
        notes={"source": "apex-commerce"},
    )
    return {"payment_link": link, "pay_here": link.get("short_url")}


# ---------- Inbound: webhook receiver ----------
@router.post("/webhooks/razorpay")
async def razorpay_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Receive a Razorpay webhook. We verify the HMAC signature on the RAW body
    before trusting anything inside it.
    """
    raw_body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")

    if not payment_service.verify_webhook_signature(raw_body, signature):
        # Reject anything we can't cryptographically trust.
        print("[WEBHOOK] REJECTED - signature verification failed")
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    event = json.loads(raw_body)
    event_type = event.get("event", "unknown")
    print(f"[WEBHOOK] VERIFIED - event: {event_type}")

    _handle_event(db, event)
    # Always return 200 quickly so Razorpay knows we received it.
    return {"status": "received", "event": event_type}


def _handle_event(db: Session, event: dict) -> None:
    """
    Best-effort update of a matching local order. Full order-lifecycle wiring
    (with the 6-stage audit trail) arrives in Phase 4; for now we log and, if
    we can match a Razorpay order_id we already stored, mark it paid.
    """
    event_type = event.get("event", "")
    payload = event.get("payload", {})

    razorpay_order_id = None
    payment_id = None

    if "payment" in payload:
        entity = payload["payment"].get("entity", {})
        razorpay_order_id = entity.get("order_id")
        payment_id = entity.get("id")

    if not razorpay_order_id:
        print(f"[WEBHOOK] No local order to match for event '{event_type}'")
        return

    order = db.query(Order).filter_by(razorpay_order_id=razorpay_order_id).first()
    if order is None:
        print(f"[WEBHOOK] No stored order with razorpay_order_id={razorpay_order_id}")
        return

    if event_type in ("payment.captured", "order.paid"):
        order.status = "paid"
        order.razorpay_payment_id = payment_id
        db.commit()
        print(f"[WEBHOOK] Order id={order.id} marked PAID")