"""
Catalog engine: reads products from the database and renders them as
either a clean REST response or schema.org JSON-LD for buyer agents.
"""

from typing import Any

from sqlalchemy.orm import Session

from app.database.models import Merchant, Product


def paise_to_inr(paise: int) -> float:
    """Convert internal integer paise to a rupee float, e.g. 5999900 -> 59999.0"""
    return round(paise / 100, 2)


def product_to_rest(product: Product) -> dict[str, Any]:
    """Clean, human-friendly product dictionary."""
    return {
        "sku": product.sku,
        "name": product.name,
        "description": product.description,
        "category": product.category,
        "list_price_inr": paise_to_inr(product.list_price_paise),
        "currency": product.currency,
        "in_stock": product.stock_quantity > 0,
        "stock_quantity": product.stock_quantity,
        "specs": product.specs or {},
        "compatible_with": product.compatibility or [],
    }


def product_to_jsonld(product: Product) -> dict[str, Any]:
    """
    Render a product as schema.org JSON-LD so buyer agents can parse it.
    - additionalProperty carries the technical specs.
    - isRelatedTo carries the compatibility matrix (accessory SKUs).
    """
    availability = (
        "https://schema.org/InStock"
        if product.stock_quantity > 0
        else "https://schema.org/OutOfStock"
    )

    additional_properties = [
        {"@type": "PropertyValue", "name": key, "value": value}
        for key, value in (product.specs or {}).items()
    ]

    related = [
        {"@type": "Product", "sku": sku} for sku in (product.compatibility or [])
    ]

    return {
        "@context": "https://schema.org",
        "@type": "Product",
        "sku": product.sku,
        "name": product.name,
        "description": product.description or "",
        "category": product.category or "",
        "additionalProperty": additional_properties,
        "isRelatedTo": related,
        "offers": {
            "@type": "Offer",
            "priceCurrency": product.currency,
            "price": f"{paise_to_inr(product.list_price_paise):.2f}",
            "availability": availability,
            "inventoryLevel": product.stock_quantity,
        },
    }


def get_catalog_rest(db: Session) -> dict[str, Any]:
    """Return the whole catalog in the clean REST shape."""
    merchant = db.query(Merchant).first()
    products = db.query(Product).order_by(Product.category, Product.name).all()
    return {
        "merchant": merchant.name if merchant else "Unknown",
        "product_count": len(products),
        "products": [product_to_rest(p) for p in products],
    }


def get_catalog_jsonld(db: Session) -> dict[str, Any]:
    """Return the whole catalog as a schema.org ItemList of Products."""
    products = db.query(Product).order_by(Product.category, Product.name).all()
    return {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": "APEX-Commerce Product Catalog",
        "numberOfItems": len(products),
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": index + 1,
                "item": product_to_jsonld(product),
            }
            for index, product in enumerate(products)
        ],
    }


def get_product_jsonld(db: Session, sku: str) -> dict[str, Any] | None:
    """Return a single product as JSON-LD, or None if the SKU doesn't exist."""
    product = db.query(Product).filter_by(sku=sku).first()
    if product is None:
        return None
    return product_to_jsonld(product)