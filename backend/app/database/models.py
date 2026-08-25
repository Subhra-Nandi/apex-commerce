"""
APEX-Commerce database models.

Each class below maps to one table in our Neon PostgreSQL database.
Money is ALWAYS stored as integer paise (e.g. 10000 = Rs.100.00) to
avoid floating-point rounding errors in financial calculations.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base, relationship

# Base is the parent class that all our table models inherit from.
# SQLAlchemy uses it to keep track of every table we define.
Base = declarative_base()


class Merchant(Base):
    """A seller on the platform. Owns products and sets policy defaults."""

    __tablename__ = "merchants"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    email = Column(String(255), unique=True, nullable=False)

    # Razorpay account reference (test mode). Optional for now.
    razorpay_account_id = Column(String(255), nullable=True)

    # Policy defaults for this merchant (can be overridden per product).
    # min_margin_percentage is stored as a whole number, e.g. 15 = 15%.
    min_margin_percentage = Column(Integer, nullable=False, default=15)
    # Maximum total spend (in paise) any agent may transact per day.
    daily_spend_cap_paise = Column(BigInteger, nullable=False, default=5000000)  # Rs.50,000

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships let us do merchant.products in Python.
    products = relationship("Product", back_populates="merchant", cascade="all, delete-orphan")
    orders = relationship("Order", back_populates="merchant")
    mandates = relationship("AgentMandate", back_populates="merchant")


class Product(Base):
    """A sellable item, with pricing, stock, and machine-readable specs."""

    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    merchant_id = Column(Integer, ForeignKey("merchants.id"), nullable=False, index=True)

    sku = Column(String(100), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    category = Column(String(100), nullable=True, index=True)

    # All amounts are integer paise.
    cost_price_paise = Column(BigInteger, nullable=False)   # what it costs the merchant
    list_price_paise = Column(BigInteger, nullable=False)   # sticker price shown to agents
    currency = Column(String(3), nullable=False, default="INR")

    stock_quantity = Column(Integer, nullable=False, default=0)

    # Optional per-product margin override (whole number percent). If NULL,
    # the merchant's default min_margin_percentage applies.
    min_margin_percentage = Column(Integer, nullable=True)

    # Machine-readable structured data for the ACI catalog (Phase 2).
    # specs: technical attributes; compatibility: list of compatible SKUs.
    specs = Column(JSONB, nullable=True)
    compatibility = Column(JSONB, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    merchant = relationship("Merchant", back_populates="products")


class AgentMandate(Base):
    """
    A spending 'permission slip' issued to an external buyer agent.
    The Policy Enclave (Phase 4) checks every order against the mandate.
    """

    __tablename__ = "agent_mandates"

    id = Column(Integer, primary_key=True, index=True)
    merchant_id = Column(Integer, ForeignKey("merchants.id"), nullable=False, index=True)

    # A human-friendly identifier for the buyer agent (e.g. "chatgpt-buyer-01").
    agent_id = Column(String(255), nullable=False, index=True)
    agent_name = Column(String(255), nullable=True)

    # Hard spending limits, in integer paise.
    max_transaction_amount_paise = Column(BigInteger, nullable=False, default=200000)  # Rs.2,000
    daily_cap_paise = Column(BigInteger, nullable=False, default=1000000)              # Rs.10,000
    currency = Column(String(3), nullable=False, default="INR")

    # Optional list of category names this agent is allowed to buy from.
    allowed_categories = Column(JSONB, nullable=True)

    is_active = Column(Boolean, nullable=False, default=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    merchant = relationship("Merchant", back_populates="mandates")
    orders = relationship("Order", back_populates="mandate")


class Order(Base):
    """
    A single negotiated purchase attempt and its lifecycle state.

    status values (kept as plain strings for simple migrations):
        pending           - created, not yet validated
        floor_adjusted    - price was raised to the margin floor by the Enclave
        awaiting_approval - > threshold, waiting on human step-up (payment link)
        paid              - payment confirmed via webhook
        failed            - rejected (policy or payment failure)
        recovered         - original failed but a counter-offer succeeded
        cancelled         - abandoned
    """

    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    merchant_id = Column(Integer, ForeignKey("merchants.id"), nullable=False, index=True)
    mandate_id = Column(Integer, ForeignKey("agent_mandates.id"), nullable=True, index=True)

    buyer_agent_id = Column(String(255), nullable=False, index=True)

    # Idempotency key: guarantees the same request is never charged twice.
    idempotency_key = Column(String(255), unique=True, nullable=False, index=True)

    status = Column(String(50), nullable=False, default="pending", index=True)

    # The line items being purchased, e.g.
    # [{"sku": "ABC", "qty": 2, "unit_price_paise": 50000}]
    items = Column(JSONB, nullable=False)

    # All amounts in integer paise.
    quoted_price_paise = Column(BigInteger, nullable=True)      # AI's proposed price
    negotiated_price_paise = Column(BigInteger, nullable=True)  # after negotiation
    final_price_paise = Column(BigInteger, nullable=True)       # actually charged
    currency = Column(String(3), nullable=False, default="INR")

    # True if the order exceeded the step-up threshold (Rs.2,000).
    requires_step_up = Column(Boolean, nullable=False, default=False)

    # Razorpay references (populated in Phase 3).
    razorpay_order_id = Column(String(255), nullable=True, index=True)
    razorpay_payment_link_id = Column(String(255), nullable=True)
    razorpay_payment_id = Column(String(255), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    merchant = relationship("Merchant", back_populates="orders")
    mandate = relationship("AgentMandate", back_populates="orders")
    audit_events = relationship(
        "AuditEvent", back_populates="order", cascade="all, delete-orphan"
    )


class AuditEvent(Base):
    """
    One immutable step in the 6-Stage Audit Trail.

    stage_index maps to the required pipeline:
        1 = Trigger
        2 = Agent Reasoning
        3 = Policy Evaluation
        4 = Razorpay Call Payload
        5 = Webhook Verification
        6 = Final State
    """

    __tablename__ = "audit_events"

    id = Column(BigInteger, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True, index=True)

    stage_index = Column(Integer, nullable=False)          # 1..6
    stage = Column(String(100), nullable=False)            # e.g. "policy_evaluation"
    message = Column(Text, nullable=True)                  # short human-readable summary

    # Full structured detail for the dashboard timeline (request/response bodies etc.)
    payload = Column(JSONB, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    order = relationship("Order", back_populates="audit_events")