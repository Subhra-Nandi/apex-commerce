"""
Pydantic response models for the catalog API. These define the exact
JSON shape returned to clients and give us automatic validation.
"""

from typing import Any, Optional

from pydantic import BaseModel


class ProductOut(BaseModel):
    """A single product in a clean, human-friendly REST shape."""

    sku: str
    name: str
    description: Optional[str] = None
    category: Optional[str] = None
    # Prices exposed in rupees (converted from internal paise).
    list_price_inr: float
    currency: str
    in_stock: bool
    stock_quantity: int
    specs: Optional[dict[str, Any]] = None
    compatible_with: list[str] = []


class CatalogOut(BaseModel):
    """The full catalog listing."""

    merchant: str
    product_count: int
    products: list[ProductOut]