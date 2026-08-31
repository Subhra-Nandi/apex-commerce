"""
Auto-recovery and slippage-simulation routes.

    POST /recovery/checkout        -> direct intent, with auto-recovery on rejection
    POST /recovery/agent-purchase  -> natural language -> agents -> auto-recovery
    POST /recovery/slippage/stock  -> simulate inventory slippage
    POST /recovery/slippage/cost   -> simulate supplier cost slippage
    POST /recovery/slippage/reset  -> restore the seeded catalog
    GET  /recovery/offers          -> Razorpay Dashboard offers (read-only)
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agents import pipeline as agent_pipeline
from app.agents.schemas import AgentRequest
from app.config import RAZORPAY_OFFER_ID
from app.database.session import get_db
from app.enclave.schemas import PurchaseIntent
from app.payments import payment_service
from app.recovery import pipeline as recovery_pipeline
from app.recovery import slippage

router = APIRouter(tags=["Failure Recovery"])


class StockSlippageRequest(BaseModel):
    sku: str
    stock_quantity: int = Field(..., ge=0)


class CostSlippageRequest(BaseModel):
    sku: str
    cost_price_inr: float = Field(..., gt=0)


@router.post("/recovery/checkout")
def recovery_checkout(intent: PurchaseIntent, db: Session = Depends(get_db)):
    """Checkout that auto-negotiates a counter-offer if the enclave rejects."""
    return recovery_pipeline.run_checkout_with_recovery(db, intent)


@router.post("/recovery/agent-purchase")
def recovery_agent_purchase(body: AgentRequest, db: Session = Depends(get_db)):
    """
    The full resilient loop: Gemini agents propose -> enclave decides -> if it
    rejects, deterministic recovery builds a counter-offer and resubmits.
    """
    intent, details = agent_pipeline.run_negotiation(
        db,
        agent_id=body.agent_id,
        user_request=body.request,
        budget_inr=body.budget_inr,
    )
    result = recovery_pipeline.run_checkout_with_recovery(db, intent)
    return {
        "ai_proposal": details,
        "proposed_intent": intent.model_dump(),
        **result,
    }


@router.post("/recovery/slippage/stock")
def simulate_stock_slippage(body: StockSlippageRequest, db: Session = Depends(get_db)):
    return slippage.set_stock(db, body.sku, body.stock_quantity)


@router.post("/recovery/slippage/cost")
def simulate_cost_slippage(body: CostSlippageRequest, db: Session = Depends(get_db)):
    return slippage.set_cost_price(db, body.sku, body.cost_price_inr)


@router.post("/recovery/slippage/reset")
def reset_slippage(db: Session = Depends(get_db)):
    return slippage.reset_all(db)


@router.get("/recovery/offers")
def razorpay_offers():
    """
    Offers configured in your Razorpay Dashboard. Offers cannot be created via
    API on test accounts, so paste one of these IDs into RAZORPAY_OFFER_ID if you
    want counter-offers to carry a real Razorpay offer.
    """
    offers = payment_service.list_offers()
    return {
        "configured_offer_id": RAZORPAY_OFFER_ID,
        "count": len(offers),
        "offers": offers,
    }