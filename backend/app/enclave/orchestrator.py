"""
The ONLY path from an intent to a Razorpay call.

Nothing here decides pricing or limits - it loads data, asks the deterministic
enclave (app/enclave/policy_rules.py), and acts on the answer. Audit stages 1-4
are written here; stages 5-6 are appended by the webhook handler.
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import DEFAULT_MIN_MARGIN_PERCENTAGE, STEP_UP_APPROVAL_THRESHOLD_INR
from app.database import audit
from app.database.models import AgentMandate, Merchant, Order, Product
from app.enclave import policy_rules
from app.enclave.schemas import MandateData, PolicyDecision, ProductData, PurchaseIntent
from app.payments import payment_service

# Order statuses that count against the agent's daily spending cap.
COUNTED_STATUSES = ["paid", "awaiting_approval", "pending_payment"]


def _paise_to_inr(paise: int) -> float:
    return round((paise or 0) / 100, 2)


def _load_products(
    db: Session, merchant: Merchant, skus: list[str]
) -> dict[str, ProductData]:
    """
    Load products as plain enclave data. Margin resolution order:
    product override -> merchant default -> global default.
    """
    products = (
        db.query(Product)
        .filter(Product.merchant_id == merchant.id, Product.sku.in_(skus))
        .all()
    )
    result: dict[str, ProductData] = {}
    for product in products:
        margin = product.min_margin_percentage
        if margin is None:
            margin = merchant.min_margin_percentage
        if margin is None:
            margin = DEFAULT_MIN_MARGIN_PERCENTAGE
        result[product.sku] = ProductData(
            sku=product.sku,
            name=product.name,
            category=product.category,
            cost_price_paise=product.cost_price_paise,
            list_price_paise=product.list_price_paise,
            stock_quantity=product.stock_quantity,
            min_margin_percentage=margin,
        )
    return result


def _spent_today(db: Session, agent_id: str) -> int:
    """
    Total already committed by this agent today (UTC). Date filtering happens in
    Python so the code works whether or not created_at is timezone-aware.
    """
    orders = (
        db.query(Order)
        .filter(Order.buyer_agent_id == agent_id, Order.status.in_(COUNTED_STATUSES))
        .all()
    )
    today = datetime.now(timezone.utc).date()
    total = 0
    for order in orders:
        created = order.created_at
        if created is None:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if created.astimezone(timezone.utc).date() == today:
            total += order.final_price_paise or 0
    return total


def _summary(
    order: Order,
    decision: PolicyDecision | None = None,
    pay_here: str | None = None,
) -> dict:
    data = {
        "order_id": order.id,
        "status": order.status,
        "requires_step_up": order.requires_step_up,
        "subtotal_inr": _paise_to_inr(order.final_price_paise or 0),
        "razorpay_order_id": order.razorpay_order_id,
        "razorpay_payment_link_id": order.razorpay_payment_link_id,
        "pay_here": pay_here,
        "idempotency_key": order.idempotency_key,
    }
    if decision is not None:
        data["decision"] = decision.model_dump()
    return data



def run_checkout(
    db: Session,
    intent: PurchaseIntent,
    *,
    offer_id: str | None = None,
    recovery_of_order_id: int | None = None,
) -> dict:
    """
    Run one purchase intent through the enclave.

    offer_id             - optional Razorpay Dashboard offer to attach (recovery only).
    recovery_of_order_id - set when this order is an auto-recovery counter-offer,
                           purely so the audit trail records the lineage.
    """
    merchant = db.query(Merchant).first()
    if merchant is None:
        return {
            "error": "No merchant found. Run 'python -m app.catalog.seed_data' first."
        }

    idem_key = policy_rules.make_idempotency_key(intent.agent_id, intent.items)

    # --- Idempotency: never charge the same basket twice ---
    existing = db.query(Order).filter(Order.idempotency_key == idem_key).first()
    if existing is not None:
        return {"idempotent_replay": True, **_summary(existing)}

    order = Order(
        merchant_id=merchant.id,
        buyer_agent_id=intent.agent_id,
        idempotency_key=idem_key,
        status="pending",
        items=[item.model_dump() for item in intent.items],
        currency="INR",
        requires_step_up=False,
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    # --- STAGE 1: Trigger ---
    trigger_message = "Checkout triggered by buyer agent."
    if recovery_of_order_id is not None:
        trigger_message = (
            f"Auto-recovery counter-offer for rejected order {recovery_of_order_id}."
        )
    audit.record_event(
        db,
        order_id=order.id,
        stage_index=1,
        message=trigger_message,
        payload={
            "agent_id": intent.agent_id,
            "items": [item.model_dump() for item in intent.items],
            "recovery_of_order_id": recovery_of_order_id,
        },
    )

    # --- STAGE 2: Agent reasoning ---
    audit.record_event(
        db,
        order_id=order.id,
        stage_index=2,
        message=intent.reasoning or "No reasoning supplied.",
        payload={"reasoning": intent.reasoning},
    )

    # --- Mandate is required. An agent may not grant itself permission. ---
    mandate_row = (
        db.query(AgentMandate)
        .filter(AgentMandate.agent_id == intent.agent_id, AgentMandate.is_active.is_(True))
        .first()
    )
    if mandate_row is None:
        order.status = "failed"
        db.commit()
        audit.record_event(
            db,
            order_id=order.id,
            stage_index=6,
            message=f"Rejected: no active mandate for agent '{intent.agent_id}'.",
        )
        return _summary(order)

    mandate = MandateData(
        agent_id=mandate_row.agent_id,
        max_transaction_amount_paise=mandate_row.max_transaction_amount_paise,
        daily_cap_paise=mandate_row.daily_cap_paise,
        allowed_categories=mandate_row.allowed_categories,
        is_active=mandate_row.is_active,
    )

    products_by_sku = _load_products(
        db, merchant, [item.sku for item in intent.items]
    )
    spent = _spent_today(db, intent.agent_id)

    # --- STAGE 3: Policy evaluation (the deterministic enclave decides) ---
    decision = policy_rules.evaluate_intent(
        agent_id=intent.agent_id,
        items=intent.items,
        products_by_sku=products_by_sku,
        mandate=mandate,
        already_spent_today_paise=spent,
        step_up_threshold_paise=STEP_UP_APPROVAL_THRESHOLD_INR * 100,
    )
    audit.record_event(
        db,
        order_id=order.id,
        stage_index=3,
        message=(
            "Policy evaluation: APPROVED"
            if decision.approved
            else "Policy evaluation: REJECTED"
        ),
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
        audit.record_event(
            db,
            order_id=order.id,
            stage_index=6,
            message="Final state: REJECTED",
            payload={"rejection_reasons": decision.rejection_reasons},
        )
        return _summary(order, decision=decision)

    # --- STAGE 4: Razorpay call ---
    pay_here = None
    if decision.requires_step_up:
        link = payment_service.create_payment_link(
            amount_paise=decision.subtotal_paise,
            description=f"APEX-Commerce order {order.id} - human approval required",
            notes={"order_id": str(order.id), "agent_id": intent.agent_id},
        )
        order.razorpay_payment_link_id = link.get("id")
        order.status = "awaiting_approval"
        pay_here = link.get("short_url")
        db.commit()
        audit.record_event(
            db,
            order_id=order.id,
            stage_index=4,
            message="Razorpay Payment Link created for step-up human approval.",
            payload={
                "payment_link_id": link.get("id"),
                "short_url": pay_here,
                "amount_paise": decision.subtotal_paise,
            },
        )
    else:
        rzp_order = payment_service.create_order(
            amount_paise=decision.subtotal_paise,
            receipt=f"apex-{order.id}",
            notes={"order_id": str(order.id), "agent_id": intent.agent_id},
            offer_id=offer_id,
        )
        order.razorpay_order_id = rzp_order.get("id")
        order.status = "pending_payment"
        db.commit()
        audit.record_event(
            db,
            order_id=order.id,
            stage_index=4,
            message="Razorpay Order created.",
            payload={
                "razorpay_order_id": rzp_order.get("id"),
                "amount_paise": decision.subtotal_paise,
                "offer_id": offer_id,
            },
        )

    # Stages 5 (webhook verification) and 6 (final state) are appended by
    # app/payments/routes.py when Razorpay calls us back.
    return _summary(order, decision=decision, pay_here=pay_here)