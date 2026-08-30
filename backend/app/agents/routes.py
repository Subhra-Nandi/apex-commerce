"""
Agent intelligence routes.

    POST /agent/negotiate  -> run both agents, return the proposal. EXECUTES NOTHING.
    POST /agent/purchase   -> run both agents, then push the intent through the
                              deterministic enclave (which may override or reject).
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.agents import pipeline
from app.agents.schemas import AgentRequest
from app.database.session import get_db
from app.enclave import orchestrator

router = APIRouter(tags=["Agent Intelligence"])


@router.post("/agent/negotiate")
def negotiate(body: AgentRequest, db: Session = Depends(get_db)):
    """AI proposal only. No order is created and no money can move."""
    intent, details = pipeline.run_negotiation(
        db,
        agent_id=body.agent_id,
        user_request=body.request,
        budget_inr=body.budget_inr,
    )
    return {
        "ai_proposal": details,
        "proposed_intent": intent.model_dump(),
        "note": "Proposal only - nothing was executed.",
    }


@router.post("/agent/purchase")
def purchase(body: AgentRequest, db: Session = Depends(get_db)):
    """
    Full loop: AI proposes -> deterministic enclave decides -> Razorpay call.
    The enclave may raise prices to the margin floor or reject the order entirely.
    """
    intent, details = pipeline.run_negotiation(
        db,
        agent_id=body.agent_id,
        user_request=body.request,
        budget_inr=body.budget_inr,
    )
    result = orchestrator.run_checkout(db, intent)
    return {
        "ai_proposal": details,
        "proposed_intent": intent.model_dump(),
        "enclave_result": result,
    }