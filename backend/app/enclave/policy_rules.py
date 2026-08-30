"""
THE DETERMINISTIC POLICY ENCLAVE - the safety core of APEX-Commerce.

There is deliberately NO LLM, NO database, and NO network call in this file.
Plain data goes in, a decision comes out. That is what makes it auditable,
repeatable, and unit-testable without any external service.
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

# Razorpay rejects any order below 100 paise (Rs.1.00). We enforce this ourselves
# so an invalid amount can never leave our system in the first place.
MIN_ORDER_AMOUNT_PAISE = 100


def compute_floor_price_paise(cost_price_paise: int, min_margin_percentage: int) -> int:
    """
    The lowest price we will ever sell at:
        floor = cost * (1 + margin%)
    Pure integer maths, and we always round UP with math.ceil so that rounding
    can never shave a single paisa off the merchant's protected margin.
    """
    numerator = cost_price_paise * (100 + min_margin_percentage)
    return math.ceil(numerator / 100)


def make_idempotency_key(agent_id: str, items: list[IntentItem]) -> str:
    """
    A stable fingerprint of "who is buying what at what price". Items are sorted
    by SKU first, so the same basket in a different order produces the SAME key
    and therefore cannot be charged twice.
    """
    canonical = {
        "agent_id": agent_id,
        "items": sorted(
            [
                {
                    "sku": item.sku,
                    "quantity": item.quantity,
                    "price": item.proposed_unit_price_inr,
                }
                for item in items
            ],
            key=lambda entry: entry["sku"],
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
    """
    The single decision function. Nothing may call Razorpay unless this returns
    approved=True.
    """
    rejection_reasons: list[str] = []
    lines: list[LineDecision] = []

    # --- GUARD 0: a vacuous intent is NOT an approvable intent ---
    # "No items to object to" must never be mistaken for "this deal is fine".
    if not items:
        rejection_reasons.append(
            "Empty intent: the agent proposed no items, so there is nothing to purchase."
        )

    # --- Per-item evaluation ---
    for item in items:
        product = products_by_sku.get(item.sku)

        if product is None:
            rejection_reasons.append(
                f"Unknown SKU '{item.sku}' is not in this merchant's catalog."
            )
            continue

        if item.quantity > product.stock_quantity:
            rejection_reasons.append(
                f"Insufficient stock for '{item.sku}': requested {item.quantity}, "
                f"available {product.stock_quantity}."
            )
            continue

        if mandate.allowed_categories and product.category not in mandate.allowed_categories:
            rejection_reasons.append(
                f"Category '{product.category}' for SKU '{item.sku}' is not permitted "
                f"by this agent's mandate."
            )
            continue

        floor_unit_price_paise = compute_floor_price_paise(
            product.cost_price_paise, product.min_margin_percentage
        )

        if item.proposed_unit_price_inr is not None:
            requested_unit_price_paise = int(round(item.proposed_unit_price_inr * 100))
        else:
            requested_unit_price_paise = product.list_price_paise

        # THE PRICE FLOOR GUARD. The agent may propose anything; it gets clamped.
        final_unit_price_paise = max(requested_unit_price_paise, floor_unit_price_paise)
        price_adjusted = requested_unit_price_paise < floor_unit_price_paise

        if price_adjusted:
            note = (
                "Price raised to margin floor. The agent's proposal would have "
                "broken the merchant's minimum margin."
            )
        else:
            note = "Price accepted as proposed."

        lines.append(
            LineDecision(
                sku=product.sku,
                quantity=item.quantity,
                unit_cost_paise=product.cost_price_paise,
                requested_unit_price_paise=requested_unit_price_paise,
                floor_unit_price_paise=floor_unit_price_paise,
                final_unit_price_paise=final_unit_price_paise,
                line_total_paise=final_unit_price_paise * item.quantity,
                price_adjusted=price_adjusted,
                note=note,
            )
        )

    subtotal_paise = sum(line.line_total_paise for line in lines)

    # --- Mandate-level checks ---
    if not mandate.is_active:
        rejection_reasons.append(
            f"Mandate for agent '{mandate.agent_id}' is not active."
        )

    if subtotal_paise > mandate.max_transaction_amount_paise:
        rejection_reasons.append(
            f"Subtotal Rs.{subtotal_paise / 100:.2f} exceeds the per-transaction cap "
            f"of Rs.{mandate.max_transaction_amount_paise / 100:.2f}."
        )

    if already_spent_today_paise + subtotal_paise > mandate.daily_cap_paise:
        rejection_reasons.append(
            f"Daily cap exceeded: already spent Rs.{already_spent_today_paise / 100:.2f} "
            f"plus Rs.{subtotal_paise / 100:.2f} would pass the daily limit of "
            f"Rs.{mandate.daily_cap_paise / 100:.2f}."
        )

    # --- GUARD: never hand a payment gateway an unpayable amount ---
    # Only reported when nothing else is already wrong, to keep reasons readable.
    if not rejection_reasons and subtotal_paise < MIN_ORDER_AMOUNT_PAISE:
        rejection_reasons.append(
            f"Subtotal Rs.{subtotal_paise / 100:.2f} is below the minimum payable "
            f"amount of Rs.{MIN_ORDER_AMOUNT_PAISE / 100:.2f}."
        )

    requires_step_up = subtotal_paise > step_up_threshold_paise
    approved = len(rejection_reasons) == 0

    return PolicyDecision(
        approved=approved,
        rejection_reasons=rejection_reasons,
        lines=lines,
        subtotal_paise=subtotal_paise,
        requires_step_up=requires_step_up,
        idempotency_key=make_idempotency_key(agent_id, items),
    )