"""
Input and output contracts for the two Gemini agents. Keeping these as strict
Pydantic models is what lets us force JSON-only output from the LLM.
"""

from pydantic import BaseModel, Field


# ---------- API request ----------
class AgentRequest(BaseModel):
    agent_id: str = Field(default="agent-buyer-01")
    request: str = Field(
        ..., description="Natural-language shopping request from the buyer agent."
    )
    budget_inr: float = Field(
        default=0, description="Optional budget in rupees. 0 means unspecified."
    )


# ---------- Merchant Front Agent output ----------
class SelectedItem(BaseModel):
    sku: str
    quantity: int
    why: str


class FrontAgentSelection(BaseModel):
    reasoning: str
    items: list[SelectedItem]


# ---------- Growth Offer Negotiator output ----------
class NegotiatedLine(BaseModel):
    sku: str
    quantity: int
    proposed_unit_price_inr: float
    justification: str


class NegotiatorProposal(BaseModel):
    strategy: str
    offer_note: str
    lines: list[NegotiatedLine]