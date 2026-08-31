"""
AUTO-RECOVERY PIPELINE.

  1. Run the intent through the enclave as normal.
  2. If it was rejected, build a deterministic recovery plan.
  3. Submit the counter-offer through the SAME enclave - recovery gets no
     shortcut and no special privileges.
  4. Mark the original order 'recovered' and record the lineage in both trails.
"""

from typing import Any

from sqlalchemy.orm import Session

from app.config import (
    DEFAULT_MIN_MARGIN_PERCENTAGE,
    RAZORPAY_OFFER_ID,
    STEP_UP_APPROVAL_THRESHOLD_INR,
)
from app.database import audit
from app.database.models import AgentMandate, Merchant, Order
from app.enclave import orchestrator
from app.enclave.schemas import MandateData, PurchaseIntent
from app.recovery import recovery_rules
from app.recovery.schemas import RecoveryPlan

_LIVE_STATUSES = ("pending_payment", "awaiting_approval", "paid")


def run_checkout_with_recovery(db: Session, intent: PurchaseIntent) -> dict[str, Any]:
    first = orchestrator.run_checkout(db, intent)

    if first.get("error"):
        return {"attempt": first, "recovery": {"attempted": False, "reason": first["error"]}}

    if first.get("idempotent_replay"):
        return {
            "attempt": first,
            "recovery": {
                "attempted": False,
                "reason": "Idempotent replay - the existing order was returned.",
            },
        }

    if first.get("status") != "failed":
        return {
            "attempt": first,
            "recovery": {
                "attempted": False,
                "reason": "First attempt succeeded; no recovery needed.",
            },
        }

    original_order_id = first.get("order_id")

    # --- Gather the same facts the enclave used, then plan repairs ---
    merchant = db.query(Merchant).first()
    mandate_row = (
        db.query(AgentMandate)
        .filter(AgentMandate.agent_id == intent.agent_id, AgentMandate.is_active.is_(True))
        .first()
    )
    if merchant is None or mandate_row is None:
        reason = "No active mandate for this agent, so no counter-offer can be made."
        audit.record_event(
            db, order_id=original_order_id, stage_index=6,
            message=f"Auto-recovery not possible: {reason}",
        )
        return {
            "attempt": first,
            "recovery": {"attempted": True, "succeeded": False, "reason": reason},
        }

    mandate = MandateData(
        agent_id=mandate_row.agent_id,
        max_transaction_amount_paise=mandate_row.max_transaction_amount_paise,
        daily_cap_paise=mandate_row.daily_cap_paise,
        allowed_categories=mandate_row.allowed_categories,
        is_active=mandate_row.is_active,
    )
    products_by_sku = orchestrator._load_products(
        db, merchant, [item.sku for item in intent.items]
    )
    spent = orchestrator._spent_today(db, intent.agent_id)

    plan: RecoveryPlan = recovery_rules.plan_recovery(
        agent_id=intent.agent_id,
        items=intent.items,
        products_by_sku=products_by_sku,
        mandate=mandate,
        already_spent_today_paise=spent,
        step_up_threshold_paise=STEP_UP_APPROVAL_THRESHOLD_INR * 100,
    )

    audit.record_event(
        db,
        order_id=original_order_id,
        stage_index=3,
        message=(
            "Auto-recovery plan built." if plan.possible else "Auto-recovery not possible."
        ),
        payload=plan.model_dump(),
    )

    if not plan.possible:
        audit.record_event(
            db, order_id=original_order_id, stage_index=6,
            message=f"Auto-recovery failed: {plan.reason}",
        )
        return {
            "attempt": first,
            "recovery": {
                "attempted": True,
                "succeeded": False,
                "reason": plan.reason,
                "actions": [action.model_dump() for action in plan.actions],
            },
        }

    # --- Submit the counter-offer through the same enclave ---
    offer_id = RAZORPAY_OFFER_ID if plan.accessory_discount_applied else None
    counter_intent = PurchaseIntent(
        agent_id=intent.agent_id,
        reasoning=f"[Auto-Recovery] {plan.reason}",
        items=plan.items,
    )
    second = orchestrator.run_checkout(
        db,
        counter_intent,
        offer_id=offer_id,
        recovery_of_order_id=original_order_id,
    )

    succeeded = second.get("status") in _LIVE_STATUSES
    if succeeded:
        original = db.get(Order, original_order_id)
        if original is not None:
            original.status = "recovered"
            db.commit()
        audit.record_event(
            db,
            order_id=original_order_id,
            stage_index=6,
            message=(
                f"Final state: RECOVERED - counter-offer issued as order "
                f"{second.get('order_id')}."
            ),
            payload={
                "counter_offer_order_id": second.get("order_id"),
                "counter_offer_subtotal_inr": second.get("subtotal_inr"),
                "razorpay_offer_id": offer_id,
            },
        )

    return {
        "attempt": first,
        "recovery": {
            "attempted": True,
            "succeeded": succeeded,
            "reason": plan.reason,
            "actions": [action.model_dump() for action in plan.actions],
            "accessory_discount_applied": plan.accessory_discount_applied,
            "razorpay_offer_id": offer_id,
            "projected_subtotal_inr": round(plan.projected_subtotal_paise / 100, 2),
            "counter_offer": second,
        },
    }