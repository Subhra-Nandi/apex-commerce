"""
Deterministic enclave self-test. No pytest, no database, no network needed.

Run from the backend/ folder with the venv active:
    python -m app.enclave.selftest
"""

from app.enclave import policy_rules
from app.enclave.schemas import IntentItem, MandateData, ProductData

_passed = 0
_failed = 0


def check(label: str, condition: bool) -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  PASS   {label}")
    else:
        _failed += 1
        print(f"  FAIL   {label}")


# --- Fixtures mirroring the seeded catalog (all money in paise) ---
MOUSE = ProductData(
    sku="MOU-WL-01",
    name="Apex Silent Wireless Mouse",
    category="accessories",
    cost_price_paise=60000,
    list_price_paise=129900,
    stock_quantity=50,
    min_margin_percentage=12,
)
KEYBOARD = ProductData(
    sku="KBD-MECH-01",
    name="Apex Mechanical Keyboard",
    category="accessories",
    cost_price_paise=250000,
    list_price_paise=449900,
    stock_quantity=30,
    min_margin_percentage=12,
)
HEADPHONES = ProductData(
    sku="HDP-ANC-01",
    name="Apex QuietBuds ANC",
    category="audio",
    cost_price_paise=350000,
    list_price_paise=699900,
    stock_quantity=25,
    min_margin_percentage=12,
)

PRODUCTS = {p.sku: p for p in (MOUSE, KEYBOARD, HEADPHONES)}

MANDATE = MandateData(
    agent_id="agent-buyer-01",
    max_transaction_amount_paise=500000,   # Rs.5,000 per transaction
    daily_cap_paise=1500000,               # Rs.15,000 per day
    allowed_categories=None,
    is_active=True,
)

STEP_UP_THRESHOLD_PAISE = 200000           # Rs.2,000


def evaluate(items: list[IntentItem]):
    return policy_rules.evaluate_intent(
        agent_id="agent-buyer-01",
        items=items,
        products_by_sku=PRODUCTS,
        mandate=MANDATE,
        already_spent_today_paise=0,
        step_up_threshold_paise=STEP_UP_THRESHOLD_PAISE,
    )


def main() -> None:
    print("\nAPEX-Commerce deterministic enclave self-test\n")

    # 1. Floor maths: Rs.600 cost at 12% margin -> Rs.672.00
    floor = policy_rules.compute_floor_price_paise(60000, 12)
    check(f"Floor for Rs.600 @ 12% is Rs.672.00 (got {floor} paise)", floor == 67200)

    # 2. A below-floor proposal is raised to the floor
    decision = evaluate([IntentItem(sku="MOU-WL-01", quantity=1, proposed_unit_price_inr=500.0)])
    line = decision.lines[0]
    check(
        "Below-floor Rs.500 proposal raised to Rs.672.00 and flagged",
        line.final_unit_price_paise == 67200 and line.price_adjusted is True,
    )

    # 3. Per-transaction cap rejects an over-cap order
    decision = evaluate([IntentItem(sku="HDP-ANC-01", quantity=1, proposed_unit_price_inr=6999.0)])
    check(
        "Rs.6,999 order rejected by the Rs.5,000 per-transaction cap",
        decision.approved is False and len(decision.rejection_reasons) > 0,
    )

    # 4. Step-up approval triggers above Rs.2,000
    decision = evaluate([IntentItem(sku="KBD-MECH-01", quantity=1, proposed_unit_price_inr=4499.0)])
    check(
        "Rs.4,499 order requires step-up human approval",
        decision.approved is True and decision.requires_step_up is True,
    )

    # 5. Idempotency key ignores item ordering
    key_a = policy_rules.make_idempotency_key(
        "agent-buyer-01",
        [
            IntentItem(sku="MOU-WL-01", quantity=1, proposed_unit_price_inr=700.0),
            IntentItem(sku="KBD-MECH-01", quantity=1, proposed_unit_price_inr=4000.0),
        ],
    )
    key_b = policy_rules.make_idempotency_key(
        "agent-buyer-01",
        [
            IntentItem(sku="KBD-MECH-01", quantity=1, proposed_unit_price_inr=4000.0),
            IntentItem(sku="MOU-WL-01", quantity=1, proposed_unit_price_inr=700.0),
        ],
    )
    check("Idempotency key is stable regardless of item order", key_a == key_b)

    # 6. REGRESSION: an empty intent must be rejected, never silently approved
    decision = evaluate([])
    check(
        "Empty intent rejected (cannot produce a Rs.0 Razorpay order)",
        decision.approved is False
        and decision.subtotal_paise == 0
        and any("Empty intent" in r for r in decision.rejection_reasons),
    )

    # 7. REGRESSION: an intent of only hallucinated SKUs must be rejected
    decision = evaluate([IntentItem(sku="FAKE-SKU-999", quantity=1, proposed_unit_price_inr=100.0)])
    check(
        "Hallucinated-SKU-only intent rejected with a Rs.0 subtotal",
        decision.approved is False and decision.subtotal_paise == 0,
    )

    print(f"\n{_passed} passed, {_failed} failed\n")


if __name__ == "__main__":
    main()