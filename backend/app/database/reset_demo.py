"""
Clear transaction history so a rehearsed demo starts from a clean slate.

    python -m app.database.reset_demo          # asks for confirmation
    python -m app.database.reset_demo --yes    # no prompt

Deletes every order and every audit-ledger row. LEAVES the merchant, the
product catalog and the agent mandate intact, so there is nothing to re-seed
afterwards.

WHY YOU NEED THIS
-----------------
Idempotency protection means an identical agent request returns the STORED
order instead of re-evaluating it. In production that is exactly right: it is
what stops a retried request from charging a customer twice.

During a rehearsed demo it works against you. Once you have run
"keyboard + hub at Rs.7,000" a single time, every later run of that same
basket replays the saved result, so the dashboard shows no fresh policy
verdict and the rejection story disappears from the screen.

Clearing order history makes every request fresh again. Run this immediately
before recording, and before e2e_check.py if you want full test coverage.
"""

import sys

from sqlalchemy import text

from app.database import audit as audit_module
from app.database import models as db_models
from app.database.models import Order
from app.database.session import get_db

# Same introspection the dashboard read model uses: the audit ORM class has
# been named a few different things during development, so find it rather
# than hard-coding a name that may not exist.
_AUDIT_CLASS_NAMES = (
    "AuditEvent",
    "AuditLog",
    "AuditTrail",
    "AuditRecord",
    "OrderAuditEvent",
    "AuditStage",
    "AuditEntry",
)


def find_audit_model():
    for module in (db_models, audit_module):
        for name in _AUDIT_CLASS_NAMES:
            model = getattr(module, name, None)
            if model is not None and hasattr(model, "__table__"):
                return model
    return None


def main():
    skip_prompt = "--yes" in sys.argv or "-y" in sys.argv

    audit_model = find_audit_model()

    db_generator = get_db()
    session = next(db_generator)

    try:
        order_count = session.query(Order).count()
        audit_count = session.query(audit_model).count() if audit_model else 0

        print("")
        print("  This will permanently delete:")
        print(f"    {order_count} orders")
        print(f"    {audit_count} audit-ledger rows"
              f"{'' if audit_model else '  (audit model not found - skipping)'}")
        print("")
        print("  It will NOT touch your merchant, catalog, or agent mandate.")
        print("")

        if order_count == 0 and audit_count == 0:
            print("  Nothing to delete. Already clean.\n")
            return 0

        if not skip_prompt:
            answer = input("  Type 'yes' to proceed: ").strip().lower()
            if answer != "yes":
                print("\n  Cancelled. Nothing was deleted.\n")
                return 1

        # Audit rows reference orders, so they must go first or the foreign
        # key will refuse the delete.
        if audit_model is not None:
            session.query(audit_model).delete(synchronize_session=False)

        # A recovered order points at the counter-offer order that replaced
        # it. That self-reference has to be broken before a bulk delete.
        for column in ("recovery_of_order_id", "recovered_by_order_id",
                       "parent_order_id"):
            if hasattr(Order, column):
                session.query(Order).update(
                    {getattr(Order, column): None}, synchronize_session=False
                )

        session.query(Order).delete(synchronize_session=False)
        session.commit()

        print(f"\n  Deleted {order_count} orders and {audit_count} audit rows.")

        # Restarting the sequence is cosmetic, but order ids beginning at 1
        # look far better on camera than ids beginning at 12. If the sequence
        # is named something else this fails harmlessly.
        try:
            session.execute(text("ALTER SEQUENCE orders_id_seq RESTART WITH 1"))
            session.commit()
            print("  Order ids will restart from 1.")
        except Exception:
            session.rollback()
            print("  (Could not restart the id sequence - harmless, ids just"
                  " continue upward.)")

        print("\n  Clean. Your catalog and mandate are untouched, so there is"
              "\n  nothing to re-seed. Reload the dashboard to confirm.\n")
        return 0

    except Exception as error:
        session.rollback()
        print(f"\n  FAILED: {error}\n")
        print("  Most likely another table still references orders. Check for a"
              "\n  payments or transactions table in app/database/models.py and"
              "\n  delete its rows before the orders.\n")
        return 1

    finally:
        db_generator.close()


if __name__ == "__main__":
    sys.exit(main())
