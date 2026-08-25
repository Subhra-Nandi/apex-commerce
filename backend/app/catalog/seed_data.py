"""
Seeds the database with one demo merchant and a set of products
(a laptop + compatible accessories). Safe to run multiple times:
it skips anything that already exists.

Run from the backend/ folder (venv active):
    python -m app.catalog.seed_data
"""

from app.database.models import Merchant, Product
from app.database.session import SessionLocal


def seed():
    db = SessionLocal()
    try:
        # --- 1. Create the demo merchant (if not already there) ---
        merchant = db.query(Merchant).filter_by(email="demo@apexcommerce.test").first()
        if merchant is None:
            merchant = Merchant(
                name="Apex Electronics",
                email="demo@apexcommerce.test",
                min_margin_percentage=12,        # default 12% floor
                daily_spend_cap_paise=5000000,   # Rs.50,000/day
            )
            db.add(merchant)
            db.commit()
            db.refresh(merchant)
            print(f"Created merchant '{merchant.name}' (id={merchant.id})")
        else:
            print(f"Merchant already exists (id={merchant.id}) - reusing it")

        # --- 2. Define the products (all money values in integer paise) ---
        products = [
            {
                "sku": "LAP-14-PRO",
                "name": "ApexBook 14 Pro",
                "description": "14-inch productivity laptop for professionals.",
                "category": "laptops",
                "cost_price_paise": 4500000,   # Rs.45,000
                "list_price_paise": 5999900,   # Rs.59,999
                "stock_quantity": 12,
                "min_margin_percentage": 15,   # override: 15% floor on the laptop
                "specs": {
                    "cpu": "Intel Core i5-13420H",
                    "ram": "16GB",
                    "storage": "512GB SSD",
                    "display": "14-inch 2.2K",
                    "weight_kg": 1.4,
                },
                "compatibility": ["HUB-USBC-7", "MOU-WL-01", "KBD-MECH-01", "HDP-ANC-01"],
            },
            {
                "sku": "MOU-WL-01",
                "name": "Apex Silent Wireless Mouse",
                "description": "Ergonomic 2.4GHz wireless mouse.",
                "category": "accessories",
                "cost_price_paise": 60000,     # Rs.600
                "list_price_paise": 129900,    # Rs.1,299
                "stock_quantity": 50,
                "min_margin_percentage": None, # use merchant default (12%)
                "specs": {"dpi": 1600, "battery": "AA x1", "connection": "2.4GHz USB"},
                "compatibility": ["LAP-14-PRO"],
            },
            {
                "sku": "KBD-MECH-01",
                "name": "Apex Mechanical Keyboard",
                "description": "Compact 75% mechanical keyboard, brown switches.",
                "category": "accessories",
                "cost_price_paise": 250000,    # Rs.2,500
                "list_price_paise": 449900,    # Rs.4,499
                "stock_quantity": 30,
                "min_margin_percentage": None,
                "specs": {"layout": "75%", "switch": "Brown", "backlight": "White LED"},
                "compatibility": ["LAP-14-PRO"],
            },
            {
                "sku": "HDP-ANC-01",
                "name": "Apex QuietBuds ANC",
                "description": "Over-ear active noise cancelling headphones.",
                "category": "audio",
                "cost_price_paise": 350000,    # Rs.3,500
                "list_price_paise": 699900,    # Rs.6,999
                "stock_quantity": 25,
                "min_margin_percentage": None,
                "specs": {"anc": True, "battery_hours": 40, "bluetooth": "5.3"},
                "compatibility": ["LAP-14-PRO"],
            },
            {
                "sku": "HUB-USBC-7",
                "name": "Apex 7-in-1 USB-C Hub",
                "description": "USB-C hub with HDMI, USB-A, SD, and PD charging.",
                "category": "accessories",
                "cost_price_paise": 90000,     # Rs.900
                "list_price_paise": 199900,    # Rs.1,999
                "stock_quantity": 40,
                "min_margin_percentage": None,
                "specs": {"ports": 7, "hdmi": "4K@30Hz", "power_delivery_w": 100},
                "compatibility": ["LAP-14-PRO"],
            },
        ]

        # --- 3. Insert each product only if its SKU isn't already present ---
        for data in products:
            existing = db.query(Product).filter_by(sku=data["sku"]).first()
            if existing:
                print(f"  Product {data['sku']} already exists - skipping")
                continue
            product = Product(merchant_id=merchant.id, **data)
            db.add(product)
            print(f"  Added product {data['sku']} - {data['name']}")

        db.commit()
        print("Seeding complete.")
    finally:
        db.close()


if __name__ == "__main__":
    seed()