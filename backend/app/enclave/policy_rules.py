"""
DETERMINISTIC POLICY ENCLAVE - the non-LLM safety core.

Nothing here talks to a database, the network, or an LLM. It takes plain data
in and returns a decision out, so it is fully predictable and easy to test -
exactly what you want guarding money.

Enforces:
  1. PRICE FLOOR: final >= cost x (1 + margin%). Under-priced offers are raised.
  2. SPENDING CAPS: per-transaction and per-day limits from the agent mandate.
  3. IDEMPOTENCY: identical intents hash to the same key.
"""

import hashlib
import json
import math

from app.enclave.schemas import (
    IntentItem,
    LineDecision,
    MandateData,
    PolicyDecision,
    ProductData,
)


def compute_floor_price_paise(cost_price_paise: int, min_margin_percentage: int) -> int:
    """
    Lowest legal unit price in paise: cost x (1 + margin/100), always rounded UP
    so rounding can never eat into the margin. Pure integer math.
    """
    numerator = cost_price_paise * (100 + min_margin_percentage)
    return math.ceil(numerator / 100)


def make_idempotency_key(agent_id: str, items: list[IntentItem]) -> str:
    """
    Deterministic fingerprint of 'who buys what at what price'. Same agent, same
    items and prices -> same key, regardless of item ordering.
    """
    canonical = {
        "agent_id": agent_id,
        "items": sorted(
            [
                {"sku": i.sku, "quantity": i.quantity, "price": i.proposed_unit_price_inr}
                for i in items
            ],
            key=lambda x: x["sku"],
        ),
    }
    blob = json.dumps(canonical, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()


def evaluate_intent(
    *,
    agent_id: str,
    items: list[IntentItem],
    products_by_sku: dict[str, ProductData],
    mandate: MandateData,
    already_spent_today_paise: int,
    step_up_threshold_paise: int,
) -> PolicyDecision:
    reasons: list[str] = []
    lines: list[LineDecision] = []

    for item in items:
        product = products_by_sku.get(item.sku)
        if product is None:
            reasons.append(f"Unknown SKU '{item.sku}'")
            continue

        if item.quantity > product.stock_quantity:
            reasons.append(
                f"Insufficient stock for {item.sku}: requested {item.quantity}, "
                f"available {product.stock_quantity}"
            )

        floor = compute_floor_price_paise(
            product.cost_price_paise, product.min_margin_percentage
        )

        if item.proposed_unit_price_inr is not None:
            requested = int(round(item.proposed_unit_price_inr * 100))
        else:
            requested = product.list_price_paise

        final = max(requested, floor)
        adjusted = requested < floor
        note = (
            f"Price raised to margin floor (requested {requested} < floor {floor})"
            if adjusted
            else "Within margin floor"
        )

        lines.append(
            LineDecision(
                sku=item.sku,
                quantity=item.quantity,
                unit_cost_paise=product.cost_price_paise,
                requested_unit_price_paise=requested,
                floor_unit_price_paise=floor,
                final_unit_price_paise=final,
                line_total_paise=final * item.quantity,
                price_adjusted=adjusted,
                note=note,
            )
        )

    subtotal = sum(line.line_total_paise for line in lines)

    if not mandate.is_active:
        reasons.append("Agent mandate is inactive")

    if mandate.allowed_categories:
        for item in items:
            product = products_by_sku.get(item.sku)
            if product and product.category not in mandate.allowed_categories:
                reasons.append(f"Category '{product.category}' not permitted by mandate")

    if subtotal > mandate.max_transaction_amount_paise:
        reasons.append(
            f"Exceeds per-transaction cap: {subtotal} > "
            f"{mandate.max_transaction_amount_paise} paise"
        )

    if already_spent_today_paise + subtotal > mandate.daily_cap_paise:
        reasons.append(
            f"Exceeds daily cap: {already_spent_today_paise + subtotal} > "
            f"{mandate.daily_cap_paise} paise"
        )

    requires_step_up = subtotal > step_up_threshold_paise
    approved = len(reasons) == 0

    return PolicyDecision(
        approved=approved,
        rejection_reasons=reasons,
        lines=lines,
        subtotal_paise=subtotal,
        requires_step_up=requires_step_up,
        idempotency_key=make_idempotency_key(agent_id, items),
    )