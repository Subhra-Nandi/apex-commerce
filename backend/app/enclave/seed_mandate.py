"""
Seeds a demo agent mandate (the buyer agent's spending permission slip).
Run from backend/ (venv active):
    python -m app.enclave.seed_mandate
"""

from app.database.models import AgentMandate, Merchant
from app.database.session import SessionLocal

AGENT_ID = "agent-buyer-01"


def seed():
    db = SessionLocal()
    try:
        merchant = db.query(Merchant).first()
        if merchant is None:
            print("No merchant found. Run 'python -m app.catalog.seed_data' first.")
            return
        existing = db.query(AgentMandate).filter_by(agent_id=AGENT_ID).first()
        if existing:
            print(f"Mandate for '{AGENT_ID}' already exists (id={existing.id}).")
            return
        mandate = AgentMandate(
            merchant_id=merchant.id,
            agent_id=AGENT_ID,
            agent_name="Demo Buyer Agent",
            max_transaction_amount_paise=500000,   # Rs.5,000 per transaction
            daily_cap_paise=1500000,               # Rs.15,000 per day
            allowed_categories=None,               # None = all categories allowed
            is_active=True,
        )
        db.add(mandate)
        db.commit()
        print(f"Created mandate for '{AGENT_ID}' (max Rs.5,000/txn, Rs.15,000/day).")
    finally:
        db.close()


if __name__ == "__main__":
    seed()