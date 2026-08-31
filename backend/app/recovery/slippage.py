"""
SLIPPAGE SIMULATOR (demo tooling).

Lets you change reality mid-flight so the recovery pipeline has something real to
recover from: drop stock to zero, or raise a cost price so the margin floor moves
above the price the agent already quoted.

BASELINE mirrors app/catalog/seed_data.py so a reset always works, even after a
server restart.
"""

from sqlalchemy.orm import Session

from app.database.models import Product

BASELINE: dict[str, dict[str, int]] = {
    "LAP-14-PRO": {"cost_price_paise": 4500000, "list_price_paise": 5999900, "stock_quantity": 12},
    "MOU-WL-01": {"cost_price_paise": 60000, "list_price_paise": 129900, "stock_quantity": 50},
    "KBD-MECH-01": {"cost_price_paise": 250000, "list_price_paise": 449900, "stock_quantity": 30},
    "HDP-ANC-01": {"cost_price_paise": 350000, "list_price_paise": 699900, "stock_quantity": 25},
    "HUB-USBC-7": {"cost_price_paise": 90000, "list_price_paise": 199900, "stock_quantity": 40},
}


def _describe(product: Product) -> dict:
    return {
        "sku": product.sku,
        "name": product.name,
        "cost_price_inr": round(product.cost_price_paise / 100, 2),
        "list_price_inr": round(product.list_price_paise / 100, 2),
        "stock_quantity": product.stock_quantity,
    }


def set_stock(db: Session, sku: str, stock_quantity: int) -> dict:
    """Simulate inventory slippage."""
    if stock_quantity < 0:
        return {"error": "stock_quantity cannot be negative."}
    product = db.query(Product).filter(Product.sku == sku).first()
    if product is None:
        return {"error": f"Unknown SKU '{sku}'."}
    before = _describe(product)
    product.stock_quantity = stock_quantity
    db.commit()
    db.refresh(product)
    return {"changed": "stock_quantity", "before": before, "after": _describe(product)}


def set_cost_price(db: Session, sku: str, cost_price_inr: float) -> dict:
    """
    Simulate supplier price slippage. Raising cost raises the margin floor, which
    is what forces the enclave to override a previously-agreed price.
    """
    if cost_price_inr <= 0:
        return {"error": "cost_price_inr must be greater than zero."}
    product = db.query(Product).filter(Product.sku == sku).first()
    if product is None:
        return {"error": f"Unknown SKU '{sku}'."}
    before = _describe(product)
    product.cost_price_paise = int(round(cost_price_inr * 100))
    db.commit()
    db.refresh(product)
    return {"changed": "cost_price_paise", "before": before, "after": _describe(product)}


def reset_all(db: Session) -> dict:
    """Restore every seeded product to its original cost, list price, and stock."""
    restored = []
    for sku, values in BASELINE.items():
        product = db.query(Product).filter(Product.sku == sku).first()
        if product is None:
            continue
        product.cost_price_paise = values["cost_price_paise"]
        product.list_price_paise = values["list_price_paise"]
        product.stock_quantity = values["stock_quantity"]
        restored.append(sku)
    db.commit()
    return {"reset": restored, "count": len(restored)}