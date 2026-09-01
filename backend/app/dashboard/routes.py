"""
Read-only endpoints for the merchant dashboard.

    GET /dashboard/summary          -> merchant, order counts, today's committed spend
    GET /dashboard/products         -> catalog INCLUDING cost price and margin floor
    GET /dashboard/orders           -> most recent orders
    GET /dashboard/orders/{id}      -> one order plus its full six-stage audit trail

Nothing here writes, charges, or decides. The agent-facing catalog deliberately
hides cost prices; this is the merchant's own screen, so it shows them.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config import DEFAULT_MIN_MARGIN_PERCENTAGE, STEP_UP_APPROVAL_THRESHOLD_INR
from app.database import audit as audit_module
from app.database import models as db_models
from app.database.models import AgentMandate, Merchant, Order, Product
from app.database.session import get_db
from app.enclave import policy_rules
from app.enclave.orchestrator import COUNTED_STATUSES

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

# The audit table's ORM class is looked up by name so this file works whatever
# you called it in models.py. If it ever returns None, the trail endpoint says so
# plainly instead of crashing.
_AUDIT_CLASS_NAMES = (
    "AuditEvent", "AuditLog", "AuditTrail", "AuditRecord",
    "OrderAuditEvent", "AuditStage", "AuditEntry",
)


def _audit_model():
    for module in (db_models, audit_module):
        for name in _AUDIT_CLASS_NAMES:
            model = getattr(module, name, None)
            if model is not None and hasattr(model, "__table__"):
                return model
    return None


def _stage_names() -> dict[int, str]:
    names = getattr(audit_module, "STAGE_NAMES", None)
    result: dict[int, str] = {}
    if isinstance(names, dict):
        for key, value in names.items():
            try:
                result[int(key)] = str(value)
            except (TypeError, ValueError):
                continue
    elif isinstance(names, (list, tuple)):
        for index, value in enumerate(names, start=1):
            result[index] = str(value)
    for index in range(1, 7):
        result.setdefault(index, f"Stage {index}")
    return result


def _inr(paise) -> float:
    return round((paise or 0) / 100, 2)


def _resolved_margin(product: Product, merchant: Merchant | None) -> int:
    if product.min_margin_percentage is not None:
        return product.min_margin_percentage
    if merchant is not None and merchant.min_margin_percentage is not None:
        return merchant.min_margin_percentage
    return DEFAULT_MIN_MARGIN_PERCENTAGE


def _is_today(value) -> bool:
    if value is None:
        return False
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).date() == datetime.now(timezone.utc).date()


def _order_row(order: Order) -> dict:
    return {
        "id": order.id,
        "agent_id": order.buyer_agent_id,
        "status": order.status,
        "requires_step_up": bool(order.requires_step_up),
        "subtotal_inr": _inr(order.final_price_paise),
        "quoted_inr": _inr(getattr(order, "quoted_price_paise", None)),
        "items": order.items or [],
        "razorpay_order_id": order.razorpay_order_id,
        "razorpay_payment_link_id": order.razorpay_payment_link_id,
        "razorpay_payment_id": getattr(order, "razorpay_payment_id", None),
        "idempotency_key": order.idempotency_key,
        "created_at": order.created_at.isoformat() if order.created_at else None,
    }



@router.get("/summary")
def dashboard_summary(
    agent_id: str = "agent-buyer-01", db: Session = Depends(get_db)
) -> dict:
    merchant = db.query(Merchant).first()
    orders = db.query(Order).all()

    counts: dict[str, int] = {}
    committed_today_paise = 0
    for order in orders:
        counts[order.status] = counts.get(order.status, 0) + 1
        if order.status in COUNTED_STATUSES and _is_today(order.created_at):
            committed_today_paise += order.final_price_paise or 0

    mandate = (
        db.query(AgentMandate).filter(AgentMandate.agent_id == agent_id).first()
    )
    mandate_view = None
    if mandate is not None:
        daily_cap = mandate.daily_cap_paise or 0
        mandate_view = {
            "agent_id": mandate.agent_id,
            "is_active": bool(mandate.is_active),
            "per_transaction_cap_inr": _inr(mandate.max_transaction_amount_paise),
            "daily_cap_inr": _inr(daily_cap),
            "spent_today_inr": _inr(committed_today_paise),
            "remaining_today_inr": _inr(max(0, daily_cap - committed_today_paise)),
            "allowed_categories": mandate.allowed_categories,
        }

    return {
        "merchant": (
            {
                "name": merchant.name,
                "min_margin_percentage": merchant.min_margin_percentage,
            }
            if merchant
            else None
        ),
        "step_up_threshold_inr": STEP_UP_APPROVAL_THRESHOLD_INR,
        "order_count": len(orders),
        "status_counts": counts,
        "committed_today_inr": _inr(committed_today_paise),
        "mandate": mandate_view,
    }


@router.get("/products")
def dashboard_products(db: Session = Depends(get_db)) -> dict:
    merchant = db.query(Merchant).first()
    rows = db.query(Product).order_by(Product.id).all()
    products = []
    for product in rows:
        margin = _resolved_margin(product, merchant)
        floor_paise = policy_rules.compute_floor_price_paise(
            product.cost_price_paise, margin
        )
        products.append(
            {
                "sku": product.sku,
                "name": product.name,
                "category": product.category,
                "cost_inr": _inr(product.cost_price_paise),
                "floor_inr": _inr(floor_paise),
                "list_inr": _inr(product.list_price_paise),
                "min_margin_percentage": margin,
                "stock_quantity": product.stock_quantity,
            }
        )
    return {"count": len(products), "products": products}


@router.get("/orders")
def dashboard_orders(limit: int = 30, db: Session = Depends(get_db)) -> dict:
    limit = max(1, min(limit, 200))
    rows = db.query(Order).order_by(Order.id.desc()).limit(limit).all()
    return {"count": len(rows), "orders": [_order_row(order) for order in rows]}


@router.get("/orders/{order_id}")
def dashboard_order_detail(order_id: int, db: Session = Depends(get_db)) -> dict:
    order = db.get(Order, order_id)
    if order is None:
        return {"error": f"Order {order_id} not found."}

    stage_names = _stage_names()
    model = _audit_model()
    if model is None:
        return {
            **_order_row(order),
            "stage_names": stage_names,
            "trail": [],
            "trail_error": (
                "Could not find the audit-log class in app/database/models.py. "
                "Add its name to _AUDIT_CLASS_NAMES in app/dashboard/routes.py."
            ),
        }

    try:
        query = db.query(model).filter(model.order_id == order_id)
        if hasattr(model, "stage_index"):
            query = query.order_by(model.stage_index.asc(), model.id.asc())
        else:
            query = query.order_by(model.id.asc())
        rows = query.all()
    except Exception as error:  # noqa: BLE001 - degrade, never 500 the dashboard
        return {
            **_order_row(order),
            "stage_names": stage_names,
            "trail": [],
            "trail_error": f"Could not read the audit trail: {error}",
        }

    trail = []
    for row in rows:
        index = getattr(row, "stage_index", None) or 0
        created = getattr(row, "created_at", None)
        trail.append(
            {
                "id": getattr(row, "id", None),
                "stage_index": index,
                "stage_name": stage_names.get(index, f"Stage {index}"),
                "message": getattr(row, "message", "") or "",
                "payload": getattr(row, "payload", None),
                "created_at": created.isoformat() if created else None,
            }
        )

    return {**_order_row(order), "stage_names": stage_names, "trail": trail}