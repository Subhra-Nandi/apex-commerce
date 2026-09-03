"""
Agent intelligence routes.

    GET  /agent/llm-status -> which LLM providers are configured, in try order
    POST /agent/negotiate  -> run both agents, return the proposal. EXECUTES NOTHING.
    POST /agent/purchase   -> run both agents, then push the intent through the
                              deterministic enclave (which may override or reject).

WHAT CHANGED IN THIS VERSION
----------------------------
1. gorouter.app is now the PRIMARY provider, running Claude Opus 5. OpenRouter has
   been demoted to an optional middle fallback and Gemini is the last resort.
   /agent/llm-status does not hardcode who leads - it asks
   llm_router.provider_order(), so the report can never drift out of step with what
   the code actually does.

2. /agent/llm-status now proves the pinned gorouter model EXISTS before you spend a
   credit, using that gateway's public keyless catalog, and reports how many
   billable calls this uvicorn process has made. Billing there is per CALL, so that
   meter is the difference between "we have credit" and "we had credit".

3. Both POST routes wrap the model call in try/except. When every LLM provider is
   down or out of credit, `llm_router` raises RuntimeError("All LLM providers
   failed...") and FastAPI used to answer a bare 500 with a traceback. That was a
   lie by omission: it looked like the payment system broke, when in fact the
   request never reached the policy enclave, so no order was written and no money
   moved. We now answer 503 with a sentence that says exactly that.

Any OTHER RuntimeError is deliberately re-raised, so genuine bugs still show up
as loud 500s instead of being disguised as an outage.
"""

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.agents import (
    gemini_client,
    gorouter_client,
    llm_guard,
    llm_router,
    openrouter_client,
    pipeline,
)
from app.agents.schemas import AgentRequest
from app.database.session import get_db
from app.enclave import orchestrator

router = APIRouter(tags=["Agent Intelligence"])


@router.get("/agent/llm-status")
def llm_status() -> dict[str, Any]:
    """
    Diagnostic. Shows who leads, the exact model chain each provider will walk, and
    whether anything is behind the primary - so you can confirm failover is armed
    before a demo instead of discovering it on camera.

    Costs nothing to call. The gorouter catalog endpoint is public and needs no API
    key, and the billing endpoints are not model calls, so nothing here is billable.
    """
    order = llm_router.provider_order()

    # 1. gorouter.app - the primary brain. describe() makes no network call.
    #    catalog_check() does, but it is the PUBLIC pricing endpoint: no key, no
    #    charge. It is how you find out a model id is wrong without paying to learn.
    gorouter_info: dict[str, Any] = gorouter_client.describe()
    gorouter_info["catalog"] = gorouter_client.catalog_check()
    gorouter_info["credit"] = gorouter_client.fetch_balance()

    # 2. OpenRouter - optional middle fallback. Config snapshot first (no network),
    #    then the live free chain (one GET /models).
    openrouter_info: dict[str, Any] = openrouter_client.describe()
    openrouter_info["free_models_discovered"] = 0
    openrouter_info["will_try_in_order"] = []
    openrouter_info["discovery_error"] = None

    if openrouter_client.is_configured():
        try:
            if openrouter_info["free_fallback_enabled"]:
                openrouter_info["free_models_discovered"] = len(
                    openrouter_client.discover_free_models(force_refresh=True)
                )
            openrouter_info["will_try_in_order"] = openrouter_client.candidate_models()
        except Exception as error:  # noqa: BLE001 - diagnostics must never 500
            openrouter_info["discovery_error"] = str(error)[:300]
            # Discovery is optional; the pinned model still works without it.
            pinned = openrouter_client.primary_model()
            openrouter_info["will_try_in_order"] = [pinned] if pinned else []

    # 3. Gemini - the last resort.
    gemini_info: dict[str, Any] = {
        "provider": "gemini",
        "model": gemini_client.model_name(),
        "configured": gemini_client.is_configured(),
    }

    models = {
        "gorouter": gorouter_info["pinned_model"],
        "openrouter": openrouter_info["pinned_model"],
        "gemini": gemini_info["model"],
    }
    configured = {
        "gorouter": gorouter_info["configured"],
        "openrouter": openrouter_info["configured"],
        "gemini": gemini_info["configured"],
    }
    chains = {
        "gorouter": gorouter_info["will_try_in_order"],
        "openrouter": openrouter_info["will_try_in_order"],
        "gemini": [gemini_info["model"]] if gemini_info["configured"] else [],
    }

    def snapshot(provider: str) -> dict[str, Any]:
        return {
            "provider": provider,
            "model": models[provider],
            "configured": configured[provider],
        }

    primary = snapshot(order[0])

    # What would ACTUALLY be tried next if the primary refused? The first provider
    # behind the leader that has a key. If nothing else has a key but the leader's
    # own chain still holds spare model ids, those spares ARE the real fallback -
    # say so rather than reporting "not armed", because e2e_check.py reads this
    # field as a pre-flight warning and a false alarm trains you to ignore it.
    fallback = next((snapshot(name) for name in order[1:] if configured[name]), None)
    if fallback is None:
        spares = chains.get(order[0]) or []
        if len(spares) > 1:
            fallback = {
                "provider": order[0],
                "model": spares[1],
                "configured": True,
                "note": f"next model in the {order[0]} chain",
            }
    if fallback is None:
        fallback = {"provider": None, "model": None, "configured": False}

    return {
        "primary": primary,
        "fallback": fallback,
        "failover_armed": bool(fallback["configured"]),
        "provider_order": order,
        "gorouter": gorouter_info,
        "openrouter": openrouter_info,
        "gemini": gemini_info,
    }


@router.post("/agent/negotiate")
def negotiate(body: AgentRequest, db: Session = Depends(get_db)):
    """AI proposal only. No order is created and no money can move."""
    try:
        intent, details = pipeline.run_negotiation(
            db,
            agent_id=body.agent_id,
            user_request=body.request,
            budget_inr=body.budget_inr,
        )
    except RuntimeError as error:
        if not llm_guard.is_llm_failure(error):
            raise
        return llm_guard.unavailable_response(error, endpoint="/agent/negotiate")

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
    try:
        intent, details = pipeline.run_negotiation(
            db,
            agent_id=body.agent_id,
            user_request=body.request,
            budget_inr=body.budget_inr,
        )
    except RuntimeError as error:
        if not llm_guard.is_llm_failure(error):
            raise
        # Nothing below this line ran: no enclave evaluation, no order row,
        # no Razorpay call. The 503 body states that explicitly.
        return llm_guard.unavailable_response(error, endpoint="/agent/purchase")

    result = orchestrator.run_checkout(db, intent)
    return {
        "ai_proposal": details,
        "proposed_intent": intent.model_dump(),
        "enclave_result": result,
    }
