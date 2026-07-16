"""
FastAPI entrypoint for the BEL Business Intelligence Copilot.

Run locally:
    uvicorn main:app --reload --port 8000

CORS is configured via the `CORS_ORIGINS` env var (comma-separated). For
production, ALWAYS restrict to the deployed frontend origin; the default is
"http://localhost:5173,http://localhost:3000" for local dev only.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("bel")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting VisionIQ backend…")
    _force = os.getenv("FORCE_LLM_ALWAYS", "").lower() in ("1", "true", "yes")
    logger.info("FORCE_LLM_ALWAYS = %s  (env raw value: %r)",
                 "ENABLED" if _force else "disabled",
                 os.getenv("FORCE_LLM_ALWAYS"))
    # Warm the query classifier at startup so the first user request
    # doesn't pay a 3-5 second DistilBERT cold-start penalty.
    try:
        from .services import query_classifier
        query_classifier._try_load_bert()
        query_classifier._ensure_trained()  # sklearn fallback too
        # Prime the model with a dummy query so first inference is warm
        query_classifier.classify("warmup")
        logger.info("Query classifier ready (bert=%s).",
                     query_classifier._bert_status)
    except Exception as e:
        logger.warning("Classifier warmup failed (non-fatal): %s", e)
    yield
    logger.info("Shutting down VisionIQ backend.")


app = FastAPI(
    title="BEL — Business Intelligence Copilot",
    description=(
        "Backend for the BEL Enterprise BI Copilot. Upload structured data "
        "or connect a database, then ask questions in natural language to get "
        "auto-generated dashboards, KPIs, forecasts, and executive insights."
    ),
    version="0.2.0",
    lifespan=lifespan,
)

# --- CORS ---
origins_env = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:3000")
allowed_origins = [o.strip() for o in origins_env.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


# --- Routers ---
from .routers import dashboard, db, export, insights, upload  # noqa: E402

app.include_router(upload.router)
app.include_router(dashboard.router)
app.include_router(insights.router)
app.include_router(db.router)
app.include_router(export.router)


@app.get("/health", tags=["health"])
def health():
    return {"status": "ok", "service": "bel-backend", "version": app.version}


@app.get("/", tags=["health"])
def root():
    return {
        "name": "BEL Business Intelligence Copilot",
        "docs": "/docs",
        "health": "/health",
    }


@app.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception):
    # Last-resort handler — never leak stack traces to the client.
    logger.exception("Unhandled error", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error. Check server logs for details."},
    )
