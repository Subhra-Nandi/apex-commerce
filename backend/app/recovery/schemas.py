"""Contracts for the deterministic auto-recovery planner."""

from pydantic import BaseModel

from app.enclave.schemas import IntentItem


class RecoveryAction(BaseModel):
    """One repair the planner made, in plain English, for the audit trail."""

    step: str
    detail: str


class RecoveryPlan(BaseModel):
    possible: bool
    reason: str
    actions: list[RecoveryAction]
    items: list[IntentItem]
    projected_subtotal_paise: int
    accessory_discount_applied: bool