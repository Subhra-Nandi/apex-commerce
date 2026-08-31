"""
Deterministic recovery-planner self-test. No pytest, no database, no network.

Run from the backend/ folder with the venv active:
    python -m app.recovery.selftest
"""

from app.enclave.schemas import IntentItem, MandateData, ProductData
from app.recovery import recovery_rules

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


LAPTOP = ProductData(
    sku="LAP-14-PRO", name="ApexBook 14 Pro", category="laptops",
    cost_price_paise=4500000, list_price_paise=5999900,
    stock_quantity=12, min_margin_percentage=15,
)
KEYBOARD = ProductData(
    sku="KBD-MECH-01", name="Apex Mechanical Keyboard", category="accessories",
    cost_price_paise=250000, list_price_paise=449900,
    stock_quantity=30, min_margin_percentage=12,
)
HUB = ProductData(
    sku="HUB-USBC-7", name="Apex 7-in-1 USB-C Hub", category="accessories",
    cost_price_paise=90000, list_price_paise=199900,
    stock_quantity=40, min_margin_percentage=12,
)
MOUSE_LOW_STOCK = ProductData(
    sku="MOU-WL-01", name="Apex Silent Wireless Mouse", category="accessories",
    cost_price_paise=60000, list_price_paise=129900,
    stock_quantity=1, min_margin_percentage=12,
)

PRODUCTS = {p.sku: p for p in (LAPTOP, KEYBOARD, HUB, MOUSE_LOW_STOCK)}

MANDATE = MandateData(
    agent_id="agent-buyer-01",
    max_transaction_amount_paise=500000,   # Rs.5,000
    daily_cap_paise=1500000,               # Rs.15,000
    allowed_categories=None,
    is_active=True,
)


def plan(items: list[IntentItem]):
    return recovery_rules.plan_recovery(
        agent_id="agent-buyer-01",
        items=items,
        products_by_sku=PRODUCTS,
        mandate=MANDATE,
        already_spent_today_paise=0,
        step_up_threshold_paise=200000,
    )


def main() -> None:
    print("\nAPEX-Commerce auto-recovery planner self-test\n")

    # 1. Quantity clamped down to real stock
    result = plan([IntentItem(sku="MOU-WL-01", quantity=5, proposed_unit_price_inr=1169.0)])
    check(
        "Over-stock quantity clamped to 1 and recovered",
        result.possible
        and result.items[0].quantity == 1
        and any(a.step == "clamp_quantity" for a in result.actions),
    )

    # 2. Hallucinated SKU removed, real item survives
    result = plan([
        IntentItem(sku="FAKE-SKU-999", quantity=1, proposed_unit_price_inr=100.0),
        IntentItem(sku="HUB-USBC-7", quantity=1, proposed_unit_price_inr=1799.0),
    ])
    check(
        "Unknown SKU dropped, valid item retained",
        result.possible
        and [i.sku for i in result.items] == ["HUB-USBC-7"]
        and any(a.step == "drop_unknown_sku" for a in result.actions),
    )

    # 3. THE HEADLINE CASE: Rs.5,848 bundle over a Rs.5,000 cap is rescued by
    #    discounting accessories to their floors (Rs.2,800 + Rs.1,008 = Rs.3,808).
    result = plan([
        IntentItem(sku="KBD-MECH-01", quantity=1, proposed_unit_price_inr=4049.1),
        IntentItem(sku="HUB-USBC-7", quantity=1, proposed_unit_price_inr=1799.1),
    ])
    check(
        "Over-cap bundle rescued by accessory discount, both items kept",
        result.possible
        and result.accessory_discount_applied
        and len(result.items) == 2
        and result.projected_subtotal_paise == 380800,
    )

    # 4. Discounts never breach the floor
    check(
        "Discounted keyboard sits exactly on its Rs.2,800 floor, never below",
        all(
            item.proposed_unit_price_inr >= 2800.0
            for item in result.items
            if item.sku == "KBD-MECH-01"
        ),
    )

    # 5. A laptop far over the cap cannot be recovered - and says so
    result = plan([IntentItem(sku="LAP-14-PRO", quantity=1, proposed_unit_price_inr=59999.0)])
    check(
        "Unaffordable laptop reported as unrecoverable, no basket invented",
        result.possible is False and result.items == [],
    )

    # 6. Laptop plus hub: laptop shed, affordable hub kept as the counter-offer
    result = plan([
        IntentItem(sku="LAP-14-PRO", quantity=1, proposed_unit_price_inr=59999.0),
        IntentItem(sku="HUB-USBC-7", quantity=1, proposed_unit_price_inr=1799.0),
    ])
    check(
        "Unaffordable item shed, affordable remainder offered",
        result.possible
        and [i.sku for i in result.items] == ["HUB-USBC-7"]
        and any(a.step == "drop_item" for a in result.actions),
    )

    print(f"\n{_passed} passed, {_failed} failed\n")


if __name__ == "__main__":
    main()