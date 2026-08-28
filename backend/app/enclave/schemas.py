"""
Data contracts for the policy enclave.

PurchaseIntent  = what a buyer agent asks for (the input).
PolicyDecision  = what the deterministic enclave decides (the output).
ProductData / MandateData = plain snapshots the enclave reasons over, so the
enclave never needs to touch the database directly.
"""

from typing import Optional

from pydantic import BaseModel, Field


class IntentItem(BaseModel):
    sku: str
    quantity: int = Field(..., gt=0)
    proposed_unit_price_inr: Optional[float] = Field(
        default=None,
        description="Agent's negotiated unit price in rupees. Omit to use list price.",
    )


class PurchaseIntent(BaseModel):
    agent_id: str = Field(default="agent-buyer-01")
    reasoning: Optional[str] = Field(
        default=None, description="Agent's explanation, stored in the audit trail."
    )
    items: list[IntentItem]


class ProductData(BaseModel):
    sku: str
    name: str
    category: Optional[str] = None
    cost_price_paise: int
    list_price_paise: int
    stock_quantity: int
    min_margin_percentage: int  # already resolved (product override or merchant default)


class MandateData(BaseModel):
    agent_id: str
    max_transaction_amount_paise: int
    daily_cap_paise: int
    allowed_categories: Optional[list[str]] = None
    is_active: bool = True


class LineDecision(BaseModel):
    sku: str
    quantity: int
    unit_cost_paise: int
    requested_unit_price_paise: int
    floor_unit_price_paise: int
    final_unit_price_paise: int
    line_total_paise: int
    price_adjusted: bool
    note: str


class PolicyDecision(BaseModel):
    approved: bool
    rejection_reasons: list[str]
    lines: list[LineDecision]
    subtotal_paise: int
    requires_step_up: bool
    idempotency_key: str