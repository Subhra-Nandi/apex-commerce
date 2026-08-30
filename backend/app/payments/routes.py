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
from app.database import audit
from app.database.models import Order
from app.database.session import get_db
from app.payments import payment_service

router = APIRouter(tags=["Payments"])


class OrderRequest(BaseModel):
    amount_inr: float = Field(..., gt=0)
    description: str = "APEX-Commerce test order"


class PaymentLinkRequest(BaseModel):
    amount_inr: float = Field(..., gt=0)
    description: str = "APEX-Commerce step-up approval"


def _inr_to_paise(amount_inr: float) -> int:
    return int(round(amount_inr * 100))


@router.post("/payments/test/order")
def create_test_order(body: OrderRequest):
    order = payment_service.create_order(
        amount_paise=_inr_to_paise(body.amount_inr),
        receipt="apex_test_order",
        notes={"source": "apex-commerce", "purpose": body.description},
    )
    return {"razorpay_key_id": RAZORPAY_KEY_ID, "order": order}


@router.post("/payments/test/payment-link")
def create_test_payment_link(body: PaymentLinkRequest):
    link = payment_service.create_payment_link(
        amount_paise=_inr_to_paise(body.amount_inr),
        description=body.description,
        notes={"source": "apex-commerce"},
    )
    return {"payment_link": link, "pay_here": link.get("short_url")}


@router.post("/webhooks/razorpay")
async def razorpay_webhook(request: Request, db: Session = Depends(get_db)):
    raw_body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")

    if not payment_service.verify_webhook_signature(raw_body, signature):
        print("[WEBHOOK] REJECTED - signature verification failed")
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    event = json.loads(raw_body)
    event_type = event.get("event", "unknown")
    print(f"[WEBHOOK] VERIFIED - event: {event_type}")

    _handle_event(db, event)
    return {"status": "received", "event": event_type}


def _handle_event(db: Session, event: dict) -> None:
    event_type = event.get("event", "")
    payload = event.get("payload", {})

    razorpay_order_id = None
    payment_id = None
    payment_link_id = None

    if "payment" in payload:
        entity = payload["payment"].get("entity", {})
        razorpay_order_id = entity.get("order_id")
        payment_id = entity.get("id")
    if "payment_link" in payload:
        payment_link_id = payload["payment_link"].get("entity", {}).get("id")

    order = None
    if razorpay_order_id:
        order = db.query(Order).filter_by(razorpay_order_id=razorpay_order_id).first()
    if order is None and payment_link_id:
        order = db.query(Order).filter_by(razorpay_payment_link_id=payment_link_id).first()

    if order is None:
        print(f"[WEBHOOK] No local order matched for event '{event_type}'")
        return

    # Stage 5: Webhook Verification
    audit.record_event(
        db, order_id=order.id, stage_index=5,
        message=f"Webhook verified: {event_type}",
        payload={"razorpay_order_id": razorpay_order_id,
                 "payment_id": payment_id,
                 "payment_link_id": payment_link_id},
    )

    if event_type in ("payment.captured", "order.paid", "payment_link.paid"):
        order.status = "paid"
        if payment_id:
            order.razorpay_payment_id = payment_id
        db.commit()
        # Stage 6: Final State
        audit.record_event(
            db, order_id=order.id, stage_index=6,
            message="Final state: PAID",
            payload={"status": "paid", "payment_id": payment_id},
        )
        print(f"[WEBHOOK] Order id={order.id} marked PAID (6-stage trail complete)")