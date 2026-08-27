"""
APEX-Commerce FastAPI application entry point.
Run from the backend/ folder (venv active):
    uvicorn app.main:app --reload
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.catalog.routes import router as catalog_router
from app.payments.routes import router as payments_router

app = FastAPI(
    title="APEX-Commerce",
    description="Agentic Policy & Negotiated Execution Exchange",
    version="0.1.0",
)

# Allow the frontend dashboard (Phase 7) to call this API from the browser.
# For the hackathon we allow all origins; tighten this before any real deployment.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routes.
app.include_router(catalog_router)
app.include_router(payments_router)


@app.get("/")
def health_check():
    """Simple check to confirm the server is alive."""
    return {"status": "ok", "service": "APEX-Commerce", "version": "0.1.0"}