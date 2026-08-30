"""
Checkout and audit-trail API routes.

    POST /checkout            -> run an intent through the policy enclave
    GET  /orders              -> list all orders
    GET  /orders/{order_id}   -> one order with its full 6-stage audit trail
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.models import AuditEvent, Order
from app.database.session import get_db
from app.enclave import orchestrator
from app.enclave.schemas import PurchaseIntent

router = APIRouter(tags=["Checkout & Policy Enclave"])


@router.post("/checkout")
def checkout(intent: PurchaseIntent, db: Session = Depends(get_db)):
    """Run a buyer-agent purchase intent through the deterministic enclave."""
    return orchestrator.run_checkout(db, intent)


@router.get("/orders")
def list_orders(db: Session = Depends(get_db)):
    orders = db.query(Order).order_by(Order.id.desc()).all()
    return [
        {
            "order_id": o.id,
            "agent_id": o.buyer_agent_id,
            "status": o.status,
            "requires_step_up": o.requires_step_up,
            "subtotal_inr": round((o.final_price_paise or 0) / 100, 2),
            "razorpay_order_id": o.razorpay_order_id,
            "razorpay_payment_link_id": o.razorpay_payment_link_id,
        }
        for o in orders
    ]


@router.get("/orders/{order_id}")
def get_order(order_id: int, db: Session = Depends(get_db)):
    order = db.query(Order).filter_by(id=order_id).first()
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    events = (
        db.query(AuditEvent)
        .filter_by(order_id=order_id)
        .order_by(AuditEvent.stage_index, AuditEvent.id)
        .all()
    )
    return {
        "order_id": order.id,
        "agent_id": order.buyer_agent_id,
        "status": order.status,
        "requires_step_up": order.requires_step_up,
        "subtotal_inr": round((order.final_price_paise or 0) / 100, 2),
        "razorpay_order_id": order.razorpay_order_id,
        "razorpay_payment_link_id": order.razorpay_payment_link_id,
        "items": order.items,
        "audit_trail": [
            {
                "stage_index": e.stage_index,
                "stage": e.stage,
                "message": e.message,
                "payload": e.payload,
                "at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in events
        ],
    }