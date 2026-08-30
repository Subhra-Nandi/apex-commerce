"""
Deterministic self-test of the policy enclave. No database, no network.
Run from backend/ (venv active):
    python -m app.enclave.selftest
"""

from app.enclave import policy_rules
from app.enclave.schemas import IntentItem, MandateData, ProductData


def _mandate(**kw):
    base = dict(agent_id="a", max_transaction_amount_paise=500000,
                daily_cap_paise=1500000, allowed_categories=None, is_active=True)
    base.update(kw)
    return MandateData(**base)


def run():
    passed = failed = 0

    def check(name, cond):
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"  PASS  {name}")
        else:
            failed += 1
            print(f"  FAIL  {name}")

    # 1. Floor rounds up and enforces the margin.
    floor = policy_rules.compute_floor_price_paise(60000, 12)  # 600 x 1.12 = 672.00
    check("floor of Rs.600 @12% == 67200 paise", floor == 67200)

    products = {
        "MOU-WL-01": ProductData(sku="MOU-WL-01", name="Mouse", category="accessories",
                                 cost_price_paise=60000, list_price_paise=129900,
                                 stock_quantity=50, min_margin_percentage=12),
        "HDP-ANC-01": ProductData(sku="HDP-ANC-01", name="Headphones", category="audio",
                                  cost_price_paise=350000, list_price_paise=699900,
                                  stock_quantity=25, min_margin_percentage=12),
    }

    # 2. Under-floor offer is raised to the floor.
    d = policy_rules.evaluate_intent(
        agent_id="a",
        items=[IntentItem(sku="MOU-WL-01", quantity=1, proposed_unit_price_inr=500.0)],
        products_by_sku=products, mandate=_mandate(),
        already_spent_today_paise=0, step_up_threshold_paise=200000)
    check("under-floor price raised to floor (67200)",
          d.lines[0].final_unit_price_paise == 67200 and d.lines[0].price_adjusted)

    # 3. Per-transaction cap rejects an over-cap order.
    d2 = policy_rules.evaluate_intent(
        agent_id="a",
        items=[IntentItem(sku="HDP-ANC-01", quantity=1)],   # Rs.6,999 > Rs.5,000 cap
        products_by_sku=products, mandate=_mandate(),
        already_spent_today_paise=0, step_up_threshold_paise=200000)
    check("over per-transaction cap is rejected", d2.approved is False)

    # 4. Step-up flag triggers above the threshold.
    check("step-up required above Rs.2,000 threshold", d2.requires_step_up is True)

    # 5. Idempotency key is stable regardless of item ordering.
    k1 = policy_rules.make_idempotency_key(
        "a", [IntentItem(sku="X", quantity=1), IntentItem(sku="Y", quantity=2)])
    k2 = policy_rules.make_idempotency_key(
        "a", [IntentItem(sku="Y", quantity=2), IntentItem(sku="X", quantity=1)])
    check("idempotency key stable regardless of item order", k1 == k2)

    print(f"\n{passed} passed, {failed} failed")


if __name__ == "__main__":
    run()