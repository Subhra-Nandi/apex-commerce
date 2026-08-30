"""
GROWTH OFFER NEGOTIATOR

Takes the Front Agent's selection and proposes per-unit prices to maximise
conversion. It is deliberately NOT told the cost price or the margin floor, so
it cannot reverse-engineer them. The deterministic enclave independently
validates and may override every price it proposes.
Runs on whichever LLM provider is healthy (see app/agents/llm_router.py).
"""

import json
from typing import Any

from app.agents.llm_router import generate_structured
from app.agents.schemas import FrontAgentSelection, NegotiatorProposal
from app.config import MAX_AGENT_DISCOUNT_PERCENTAGE

SYSTEM_INSTRUCTION = """
You are the Growth Offer Negotiator for APEX-Commerce.

Your job is to propose a per-unit price for each selected item to maximise the
chance the customer completes the purchase, while keeping the deal commercially
sensible.

Hard rules:
- You do NOT know the merchant's cost price or minimum margin, and you must not
  attempt to guess or ask for them.
- A deterministic, non-AI policy enclave will independently validate every price
  you propose. If a price is below the merchant's protected floor, the enclave
  will automatically raise it. Your proposal is advisory, not final.
- You have NO ability to create orders, charge cards, or move money.
- Keep discounts modest and justified. Bundle discounts are preferable to deep
  single-item discounts.
- Include every item the Front Agent selected. Do not silently drop items.
- Respond with JSON only, matching the required schema.
""".strip()


def negotiate(
    *,
    selection: FrontAgentSelection,
    catalog: list[dict[str, Any]],
    budget_inr: float,
    per_transaction_cap_inr: float,
    trace: list[dict[str, Any]] | None = None,
) -> NegotiatorProposal:
    budget_text = f"{budget_inr:.2f} INR" if budget_inr and budget_inr > 0 else "not specified"
    cap_text = (
        f"{per_transaction_cap_inr:.2f} INR"
        if per_transaction_cap_inr and per_transaction_cap_inr > 0
        else "unknown"
    )

    prompt = f"""
The Merchant Front Agent selected these items:
{json.dumps(selection.model_dump(), indent=2)}

Catalog reference (list prices, stock, specs):
{json.dumps(catalog, indent=2)}

Customer budget: {budget_text}
Buyer agent's per-transaction spending cap: {cap_text}
Maximum discount you may attempt: {MAX_AGENT_DISCOUNT_PERCENTAGE}% off list price.

Propose a final per-unit price in rupees for each item. Describe your overall
approach in 'strategy', a customer-facing sentence in 'offer_note', and a short
'justification' per line.
""".strip()

    return generate_structured(
        system_instruction=SYSTEM_INSTRUCTION,
        prompt=prompt,
        schema=NegotiatorProposal,
        temperature=0.3,
        trace=trace,
    )