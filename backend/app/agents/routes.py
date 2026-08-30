"""
Agent intelligence routes.

    GET  /agent/llm-status -> which LLM providers are configured and reachable
    POST /agent/negotiate  -> run both agents, return the proposal. EXECUTES NOTHING.
    POST /agent/purchase   -> run both agents, then push the intent through the
                              deterministic enclave (which may override or reject).
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.agents import gemini_client, openrouter_client, pipeline
from app.agents.schemas import AgentRequest
from app.database.session import get_db
from app.enclave import orchestrator

router = APIRouter(tags=["Agent Intelligence"])


@router.get("/agent/llm-status")
def llm_status():
    """
    Diagnostic. Shows the primary provider and the live OpenRouter free-model
    fallback chain, so you can confirm failover is ready before a demo.
    """
    status = {
        "primary": {
            "provider": "gemini",
            "model": gemini_client.model_name(),
            "configured": gemini_client.is_configured(),
        },
        "fallback": {
            "provider": "openrouter",
            "configured": openrouter_client.is_configured(),
            "will_try_in_order": [],
            "free_models_discovered": 0,
            "discovery_error": None,
        },
    }

    if openrouter_client.is_configured():
        try:
            all_free = openrouter_client.discover_free_models(force_refresh=True)
            status["fallback"]["free_models_discovered"] = len(all_free)
            status["fallback"]["will_try_in_order"] = openrouter_client.candidate_models()
        except Exception as error:  # noqa: BLE001
            status["fallback"]["discovery_error"] = str(error)[:300]

    return status


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