"""
Checkout orchestrator - turns a buyer-agent PurchaseIntent into a policy-checked,
audited order. This is the ONLY path toward a Razorpay call, and it must pass the
deterministic enclave first.

6-stage audit trail recorded along the way:
    1 Trigger, 2 Agent Reasoning, 3 Policy Evaluation, 4 Razorpay Call,
    5 Webhook Verification (added later by the webhook handler), 6 Final State.
"""

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import DEFAULT_MIN_MARGIN_PERCENTAGE, STEP_UP_APPROVAL_THRESHOLD_INR
from app.database import audit
from app.database.models import AgentMandate, Merchant, Order, Product
from app.enclave import policy_rules
from app.enclave.schemas import MandateData, PolicyDecision, ProductData, PurchaseIntent
from app.payments import payment_service

COUNTED_STATUSES = ["paid", "awaiting_approval", "pending_payment"]


def _paise_to_inr(paise: int) -> float:
    return round((paise or 0) / 100, 2)


def _load_products(db: Session, merchant: Merchant, skus: list[str]) -> dict[str, ProductData]:
    products = db.query(Product).filter(Product.sku.in_(skus)).all()
    result: dict[str, ProductData] = {}
    for p in products:
        margin = p.min_margin_percentage
        if margin is None:
            margin = merchant.min_margin_percentage or DEFAULT_MIN_MARGIN_PERCENTAGE
        result[p.sku] = ProductData(
            sku=p.sku,
            name=p.name,
            category=p.category,
            cost_price_paise=p.cost_price_paise,
            list_price_paise=p.list_price_paise,
            stock_quantity=p.stock_quantity,
            min_margin_percentage=margin,
        )
    return result


def _spent_today(db: Session, agent_id: str) -> int:
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    total = (
        db.query(func.coalesce(func.sum(Order.final_price_paise), 0))
        .filter(
            Order.buyer_agent_id == agent_id,
            Order.status.in_(COUNTED_STATUSES),
            Order.created_at >= start,
        )
        .scalar()
    )
    return int(total or 0)


def _summary(order: Order, decision: Optional[PolicyDecision] = None,
             pay_here: Optional[str] = None) -> dict[str, Any]:
    return {
        "order_id": order.id,
        "status": order.status,
        "requires_step_up": order.requires_step_up,
        "subtotal_inr": _paise_to_inr(order.final_price_paise or 0),
        "razorpay_order_id": order.razorpay_order_id,
        "razorpay_payment_link_id": order.razorpay_payment_link_id,
        "pay_here": pay_here,
        "idempotency_key": order.idempotency_key,
        "decision": decision.model_dump() if decision else None,
    }


def run_checkout(db: Session, intent: PurchaseIntent) -> dict[str, Any]:
    merchant = db.query(Merchant).first()
    if merchant is None:
        return {"error": "No merchant found. Run 'python -m app.catalog.seed_data' first."}

    skus = [item.sku for item in intent.items]

    # --- Idempotency: identical intent already processed? Return the original. ---
    idem_key = policy_rules.make_idempotency_key(intent.agent_id, intent.items)
    existing = db.query(Order).filter_by(idempotency_key=idem_key).first()
    if existing is not None:
        return {"idempotent_replay": True, **_summary(existing)}

    # --- Create the order shell so audit events can attach to it. ---
    order = Order(
        merchant_id=merchant.id,
        buyer_agent_id=intent.agent_id,
        idempotency_key=idem_key,
        status="pending",
        items=[item.model_dump() for item in intent.items],
        currency="INR",
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    audit.record_event(db, order_id=order.id, stage_index=1,
                       message="Purchase intent received",
                       payload={"agent_id": intent.agent_id, "items": order.items})
    audit.record_event(db, order_id=order.id, stage_index=2,
                       message="Agent reasoning recorded",
                       payload={"reasoning": intent.reasoning or "(none provided)"})

    # --- The agent's mandate (its spending permission slip). ---
    mandate_row = (
        db.query(AgentMandate).filter_by(agent_id=intent.agent_id, is_active=True).first()
    )
    if mandate_row is None:
        order.status = "failed"
        db.commit()
        audit.record_event(db, order_id=order.id, stage_index=6,
                           message="Rejected: no active mandate for agent",
                           payload={"agent_id": intent.agent_id})
        return _summary(order)

    order.mandate_id = mandate_row.id
    db.commit()

    mandate = MandateData(
        agent_id=mandate_row.agent_id,
        max_transaction_amount_paise=mandate_row.max_transaction_amount_paise,
        daily_cap_paise=mandate_row.daily_cap_paise,
        allowed_categories=mandate_row.allowed_categories,
        is_active=mandate_row.is_active,
    )

    products_by_sku = _load_products(db, merchant, skus)
    spent = _spent_today(db, intent.agent_id)
    step_up_threshold_paise = STEP_UP_APPROVAL_THRESHOLD_INR * 100

    # --- Stage 3: deterministic policy evaluation. ---
    decision = policy_rules.evaluate_intent(
        agent_id=intent.agent_id,
        items=intent.items,
        products_by_sku=products_by_sku,
        mandate=mandate,
        already_spent_today_paise=spent,
        step_up_threshold_paise=step_up_threshold_paise,
    )
    audit.record_event(
        db, order_id=order.id, stage_index=3,
        message="Approved by enclave" if decision.approved else "Rejected by enclave",
        payload=decision.model_dump(),
    )

    order.quoted_price_paise = decision.subtotal_paise
    order.negotiated_price_paise = decision.subtotal_paise
    order.final_price_paise = decision.subtotal_paise
    order.requires_step_up = decision.requires_step_up
    db.commit()

    if not decision.approved:
        order.status = "failed"
        db.commit()
        audit.record_event(db, order_id=order.id, stage_index=6,
                           message="Final state: REJECTED",
                           payload={"reasons": decision.rejection_reasons})
        return _summary(order, decision)

    # --- Stage 4: Razorpay call (only after approval). ---
    pay_here = None
    if decision.requires_step_up:
        link = payment_service.create_payment_link(
            amount_paise=decision.subtotal_paise,
            description=f"APEX-Commerce order #{order.id} (step-up approval)",
            notes={"apex_order_id": str(order.id)},
        )
        order.razorpay_payment_link_id = link.get("id")
        order.status = "awaiting_approval"
        pay_here = link.get("short_url")
        db.commit()
        audit.record_event(db, order_id=order.id, stage_index=4,
                           message="Razorpay payment link created (step-up required)",
                           payload={"payment_link_id": link.get("id"),
                                    "short_url": pay_here,
                                    "amount_paise": decision.subtotal_paise})
    else:
        rzp_order = payment_service.create_order(
            amount_paise=decision.subtotal_paise,
            receipt=f"apex_{order.id}",
            notes={"apex_order_id": str(order.id)},
        )
        order.razorpay_order_id = rzp_order.get("id")
        order.status = "pending_payment"
        db.commit()
        audit.record_event(db, order_id=order.id, stage_index=4,
                           message="Razorpay order created (no step-up needed)",
                           payload={"razorpay_order_id": rzp_order.get("id"),
                                    "amount_paise": decision.subtotal_paise})

    # Stages 5 & 6 are appended by the webhook handler once payment lands.
    return _summary(order, decision, pay_here)