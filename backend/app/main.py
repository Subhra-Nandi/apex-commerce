"""
APEX-Commerce FastAPI application entry point.
Run from the backend/ folder (venv active):
    uvicorn app.main:app --reload
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agents.routes import router as agents_router
from app.catalog.routes import router as catalog_router
from app.dashboard.routes import router as dashboard_router
from app.enclave.routes import router as enclave_router
from app.payments.routes import router as payments_router
from app.recovery.routes import router as recovery_router

app = FastAPI(
    title="APEX-Commerce",
    description="Agentic Policy & Negotiated Execution Exchange",
    version="1.0.0",
)

# Wide open so the Next.js dev server on :3000 can call this. Restrict to your
# real frontend origin before deploying anywhere public.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(catalog_router)
app.include_router(payments_router)
app.include_router(enclave_router)
app.include_router(agents_router)
app.include_router(recovery_router)
app.include_router(dashboard_router)


@app.get("/")
def health_check():
    return {"status": "ok", "service": "APEX-Commerce", "version": "1.0.0"}