"""
Agent pipeline: Merchant Front Agent -> Growth Offer Negotiator -> PurchaseIntent.

The intent produced here is ONLY ever handed to the deterministic enclave
(app.enclave.orchestrator). The LLM output never reaches Razorpay directly.
"""

from typing import Any

from sqlalchemy.orm import Session

from app.agents import merchant_front_agent, offer_negotiator_agent
from app.agents.schemas import FrontAgentSelection, NegotiatorProposal
from app.database.models import AgentMandate, Product
from app.enclave.schemas import IntentItem, PurchaseIntent


def _build_catalog_context(db: Session) -> list[dict[str, Any]]:
    """
    Catalog snapshot for the LLM. NOTE: cost_price_paise and margin settings are
    deliberately EXCLUDED - the model must never see them.
    """
    products = db.query(Product).order_by(Product.sku).all()
    return [
        {
            "sku": p.sku,
            "name": p.name,
            "category": p.category,
            "list_price_inr": round(p.list_price_paise / 100, 2),
            "stock_quantity": p.stock_quantity,
            "specs": p.specs or {},
            "compatible_with": p.compatibility or [],
        }
        for p in products
    ]


def _sanitize(
    proposal: NegotiatorProposal, known_skus: set[str]
) -> tuple[list[IntentItem], list[str]]:
    """
    Defensive clean-up of LLM output before it reaches the enclave.
    We do NOT drop unknown SKUs - the enclave should reject them visibly - but we
    do flag them, and we fix impossible quantities/prices.
    """
    items: list[IntentItem] = []
    warnings: list[str] = []

    for line in proposal.lines:
        if line.sku not in known_skus:
            warnings.append(
                f"Negotiator proposed unknown SKU '{line.sku}' - the enclave will reject it."
            )

        quantity = line.quantity if line.quantity and line.quantity > 0 else 1
        if quantity != line.quantity:
            warnings.append(f"Quantity for '{line.sku}' corrected to {quantity}.")

        price = line.proposed_unit_price_inr
        if price is None or price <= 0:
            warnings.append(
                f"Invalid price for '{line.sku}' ignored; list price will be used."
            )
            price = None

        items.append(
            IntentItem(sku=line.sku, quantity=quantity, proposed_unit_price_inr=price)
        )

    if not items:
        warnings.append(
            "The agents produced an empty selection (they likely judged the budget "
            "too low). The enclave will reject this as an empty intent - no order "
            "and no Razorpay call."
        )

    return items, warnings


def run_negotiation(
    db: Session, *, agent_id: str, user_request: str, budget_inr: float
) -> tuple[PurchaseIntent, dict[str, Any]]:
    """
    Run both agents and build the PurchaseIntent. Executes nothing.
    Returns (intent, details) where details is a JSON-safe trace for the UI.
    """
    catalog = _build_catalog_context(db)
    known_skus = {p["sku"] for p in catalog}

    mandate = (
        db.query(AgentMandate).filter_by(agent_id=agent_id, is_active=True).first()
    )
    cap_inr = (
        round(mandate.max_transaction_amount_paise / 100, 2) if mandate else 0.0
    )

    # Records which provider/model actually served each agent call.
    llm_trace: list[dict[str, Any]] = []

    # --- Agent 1: pick the products ---
    selection: FrontAgentSelection = merchant_front_agent.select_products(
        user_request=user_request,
        budget_inr=budget_inr,
        catalog=catalog,
        trace=llm_trace,
    )

    # --- Agent 2: price them ---
    proposal: NegotiatorProposal = offer_negotiator_agent.negotiate(
        selection=selection,
        catalog=catalog,
        budget_inr=budget_inr,
        per_transaction_cap_inr=cap_inr,
        trace=llm_trace,
    )

    items, warnings = _sanitize(proposal, known_skus)

    served_by = [
        f"{entry['provider']}/{entry['model']}"
        for entry in llm_trace
        if entry["status"] == "success"
    ]

    reasoning = (
        f"[Front Agent] {selection.reasoning} "
        f"[Negotiator] {proposal.strategy} | {proposal.offer_note}"
    )

    intent = PurchaseIntent(agent_id=agent_id, reasoning=reasoning, items=items)

    details = {
        "front_agent": selection.model_dump(),
        "negotiator": proposal.model_dump(),
        "warnings": warnings,
        "llm_saw_cost_prices": False,
        "served_by": served_by,
        "llm_trace": llm_trace,
    }
    return intent, details