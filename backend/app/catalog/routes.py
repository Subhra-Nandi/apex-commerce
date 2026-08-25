"""
Catalog API routes — the Agent Commerce Interface (ACI) endpoints.

Endpoints:
    GET /.well-known/aci.json   -> discovery manifest (agent entry point)
    GET /aci/catalog            -> full catalog (clean REST JSON)
    GET /aci/catalog/jsonld     -> full catalog as schema.org JSON-LD
    GET /aci/products/{sku}     -> single product as JSON-LD
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.catalog import catalog_service
from app.catalog.schemas import CatalogOut
from app.database.session import get_db

router = APIRouter(tags=["Agent Commerce Interface"])


@router.get("/.well-known/aci.json")
def discovery_manifest():
    """
    The first thing a buyer agent fetches. It advertises what this
    server can do and where the catalog lives.
    """
    return {
        "aci_version": "0.1",
        "service": "APEX-Commerce",
        "description": "Policy-gated agentic commerce gateway.",
        "capabilities": ["catalog_discovery", "negotiation", "policy_gated_checkout"],
        "endpoints": {
            "catalog": "/aci/catalog",
            "catalog_jsonld": "/aci/catalog/jsonld",
            "product": "/aci/products/{sku}",
        },
        "currency": "INR",
        "notes": "All purchases pass through a deterministic policy enclave.",
    }


@router.get("/aci/catalog", response_model=CatalogOut)
def get_catalog(db: Session = Depends(get_db)):
    """Full catalog in a clean, human-friendly shape."""
    return catalog_service.get_catalog_rest(db)


@router.get("/aci/catalog/jsonld")
def get_catalog_jsonld(db: Session = Depends(get_db)):
    """Full catalog as schema.org JSON-LD for buyer agents."""
    return catalog_service.get_catalog_jsonld(db)


@router.get("/aci/products/{sku}")
def get_product(sku: str, db: Session = Depends(get_db)):
    """A single product as JSON-LD. Returns 404 if the SKU is unknown."""
    product = catalog_service.get_product_jsonld(db, sku)
    if product is None:
        raise HTTPException(status_code=404, detail=f"Product '{sku}' not found")
    return product