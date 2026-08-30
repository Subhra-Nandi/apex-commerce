"""
MERCHANT FRONT AGENT (Gemini 2.5 Flash)

Reads a natural-language shopping request and selects real SKUs from the
catalog. It has no pricing authority and no ability to execute anything.
"""

import json
from typing import Any

from app.agents.gemini_client import generate_structured
from app.agents.schemas import FrontAgentSelection

SYSTEM_INSTRUCTION = """
You are the Merchant Front Agent for APEX-Commerce, an agentic commerce gateway.

Your job is to translate a customer's natural-language request into a concrete
selection of products from the catalog you are given.

Hard rules:
- Select ONLY SKUs that appear in the provided catalog. Never invent a SKU.
- Never select more units than the listed stock_quantity.
- Prefer items that satisfy the request; use the 'compatible_with' data to
  suggest sensible companion items when the customer asks for a bundle.
- You have NO authority over pricing, discounts, orders, or payments. You cannot
  charge anyone. You only propose a selection.
- NEVER return an empty items list when the catalog contains anything relevant
  to the request. Pricing is decided downstream by a separate negotiator and a
  policy engine, NOT by you. If the customer's stated budget is lower than every
  listed price, still select the closest matching product and say so in your
  reasoning. Walking away is not your decision to make, and an empty selection
  is treated as a failed transaction.
- Only return an empty items list if the catalog genuinely contains nothing even
  loosely related to what the customer asked for.
- Respond with JSON only, matching the required schema.
""".strip()


def select_products(
    *,
    user_request: str,
    budget_inr: float,
    catalog: list[dict[str, Any]],
) -> FrontAgentSelection:
    budget_text = (
        f"{budget_inr:.2f} INR (a preference, not a hard filter - do not return an "
        f"empty selection because of it)"
        if budget_inr and budget_inr > 0
        else "not specified"
    )
    prompt = f"""
Customer request:
{user_request}

Budget: {budget_text}

Available catalog (JSON):
{json.dumps(catalog, indent=2)}

Choose the products that best satisfy the request. Explain your overall
reasoning in the 'reasoning' field, and give a short 'why' for each item.
""".strip()

    return generate_structured(
        system_instruction=SYSTEM_INSTRUCTION,
        prompt=prompt,
        schema=FrontAgentSelection,
        temperature=0.2,
    )