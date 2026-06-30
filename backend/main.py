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
    logger.info("Starting BEL backend…")
    yield
    logger.info("Shutting down BEL backend.")


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
