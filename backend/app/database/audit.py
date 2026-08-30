"""Writes immutable audit-trail events for the 6-stage pipeline."""

from typing import Any, Optional

from sqlalchemy.orm import Session

from app.database.models import AuditEvent

STAGE_NAMES = {
    1: "trigger",
    2: "agent_reasoning",
    3: "policy_evaluation",
    4: "razorpay_call",
    5: "webhook_verification",
    6: "final_state",
}


def record_event(
    db: Session,
    *,
    order_id: Optional[int],
    stage_index: int,
    message: str,
    payload: Optional[dict[str, Any]] = None,
) -> AuditEvent:
    event = AuditEvent(
        order_id=order_id,
        stage_index=stage_index,
        stage=STAGE_NAMES.get(stage_index, "unknown"),
        message=message,
        payload=payload,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event