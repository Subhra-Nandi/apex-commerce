"""
DETERMINISTIC AUTO-RECOVERY PLANNER.

No LLM, no database, no network. Given a rejected basket and the current facts,
it repairs the basket in a fixed, auditable order and uses the enclave itself as
the acceptance test - so recovery can never approve something the enclave would
refuse, and there is no second copy of the pricing rules to drift out of sync.

Repair order:
  1. Remove SKUs that do not exist.
  2. Clamp quantities to real stock; remove out-of-stock items.
  3. Apply the deepest LEGAL accessory discount (down to the margin floor, never
     below it).
  4. Keep the largest affordable subset of what remains.
"""

from app.config import RECOVERY_ACCESSORY_CATEGORIES
from app.enclave import policy_rules
from app.enclave.schemas import IntentItem, MandateData, ProductData
from app.recovery.schemas import RecoveryAction, RecoveryPlan


def _line_total_paise(item: IntentItem, product: ProductData) -> int:
    """What this line would actually cost after the floor guard is applied."""
    floor = policy_rules.compute_floor_price_paise(
        product.cost_price_paise, product.min_margin_percentage
    )
    if item.proposed_unit_price_inr is not None:
        requested = int(round(item.proposed_unit_price_inr * 100))
    else:
        requested = product.list_price_paise
    return max(requested, floor) * item.quantity


def plan_recovery(
    *,
    agent_id: str,
    items: list[IntentItem],
    products_by_sku: dict[str, ProductData],
    mandate: MandateData,
    already_spent_today_paise: int,
    step_up_threshold_paise: int,
) -> RecoveryPlan:
    actions: list[RecoveryAction] = []

    def evaluate(candidate: list[IntentItem]):
        return policy_rules.evaluate_intent(
            agent_id=agent_id,
            items=candidate,
            products_by_sku=products_by_sku,
            mandate=mandate,
            already_spent_today_paise=already_spent_today_paise,
            step_up_threshold_paise=step_up_threshold_paise,
        )

    def fail(reason: str) -> RecoveryPlan:
        return RecoveryPlan(
            possible=False,
            reason=reason,
            actions=actions,
            items=[],
            projected_subtotal_paise=0,
            accessory_discount_applied=False,
        )

    def succeed(
        reason: str, basket: list[IntentItem], discounted: bool
    ) -> RecoveryPlan:
        return RecoveryPlan(
            possible=True,
            reason=reason,
            actions=actions,
            items=basket,
            projected_subtotal_paise=evaluate(basket).subtotal_paise,
            accessory_discount_applied=discounted,
        )

    # --- Step 1: remove SKUs that do not exist ---
    working: list[IntentItem] = []
    for item in items:
        if item.sku not in products_by_sku:
            actions.append(
                RecoveryAction(
                    step="drop_unknown_sku",
                    detail=f"Removed '{item.sku}' - not present in the catalog.",
                )
            )
            continue
        working.append(item.model_copy(deep=True))

    # --- Step 2: clamp quantities to real stock ---
    clamped: list[IntentItem] = []
    for item in working:
        product = products_by_sku[item.sku]
        if product.stock_quantity <= 0:
            actions.append(
                RecoveryAction(
                    step="drop_out_of_stock",
                    detail=f"Removed '{item.sku}' - now out of stock.",
                )
            )
            continue
        if item.quantity > product.stock_quantity:
            actions.append(
                RecoveryAction(
                    step="clamp_quantity",
                    detail=(
                        f"Reduced '{item.sku}' from {item.quantity} to "
                        f"{product.stock_quantity} - that is all the stock left."
                    ),
                )
            )
            item = item.model_copy(update={"quantity": product.stock_quantity})
        clamped.append(item)
    working = clamped

    if not working:
        return fail("Nothing purchasable remains after removing unavailable items.")

    if evaluate(working).approved:
        return succeed("Repaired stock and availability problems.", working, False)

    # --- Step 3: deepest LEGAL accessory discount (floor, never below) ---
    discounted_basket: list[IntentItem] = []
    discount_applied = False
    for item in working:
        product = products_by_sku[item.sku]
        if product.category in RECOVERY_ACCESSORY_CATEGORIES:
            floor_paise = policy_rules.compute_floor_price_paise(
                product.cost_price_paise, product.min_margin_percentage
            )
            floor_inr = floor_paise / 100
            current = item.proposed_unit_price_inr
            if current is None or current > floor_inr:
                actions.append(
                    RecoveryAction(
                        step="accessory_discount",
                        detail=(
                            f"Repriced accessory '{item.sku}' down to its protected "
                            f"floor of Rs.{floor_inr:.2f} - the deepest discount "
                            f"policy allows."
                        ),
                    )
                )
                item = item.model_copy(
                    update={"proposed_unit_price_inr": round(floor_inr, 2)}
                )
                discount_applied = True
        discounted_basket.append(item)

    if discount_applied:
        working = discounted_basket
        if evaluate(working).approved:
            return succeed(
                "Applied the maximum legal accessory discount to fit the budget.",
                working,
                True,
            )

    # --- Step 4: keep the largest affordable subset (cheapest first) ---
    ordered = sorted(
        working, key=lambda item: _line_total_paise(item, products_by_sku[item.sku])
    )
    kept: list[IntentItem] = []
    dropped: list[IntentItem] = []
    for item in ordered:
        if evaluate(kept + [item]).approved:
            kept = kept + [item]
        else:
            dropped.append(item)

    if not kept:
        return fail(
            "Even the cheapest single item exceeds this agent's remaining "
            "spending limit, so no counter-offer is possible."
        )

    for item in dropped:
        actions.append(
            RecoveryAction(
                step="drop_item",
                detail=(
                    f"Removed '{item.sku}' - it does not fit the agent's remaining "
                    f"spending limit."
                ),
            )
        )

    return succeed(
        "Trimmed the bundle to the largest combination that fits the spending limit.",
        kept,
        discount_applied,
    )