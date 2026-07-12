"""FastAPI application entrypoint.

Run locally:  poetry run uvicorn backend.main:app --reload --port 8000
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from postgrest.exceptions import APIError

from backend.auth import CurrentUser, get_current_user
from backend.routers import backfill as backfill_router
from backend.routers import generate as generate_router
from backend.routers import import_shots as import_router
from backend.routers import products as products_router
from backend.routers import prompts as prompts_router
from mockup_generator.integrations.supabase_client import anon_client, service_client

log = logging.getLogger("mockup.api")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Log a clear verdict on Supabase connectivity at boot so a bad key or
    missing config is obvious in the Space logs instead of per-request 500s."""
    try:
        client = service_client() or anon_client()
        client.table("categories").select("categoryid").limit(1).execute()
        log.info("Supabase connectivity OK (mode=%s).", "service" if service_client() else "anon")
    except Exception as exc:  # noqa: BLE001 - never block startup; just report
        log.error("Supabase connectivity FAILED at startup: %s", exc)
    yield


app = FastAPI(title="Bindal's Creation — Mockup Generator API", lifespan=lifespan)

app.include_router(generate_router.router)
app.include_router(products_router.router)
app.include_router(prompts_router.router)
app.include_router(backfill_router.router)
app.include_router(import_router.router)


# Turn raw Supabase/PostgREST errors into clear, non-opaque responses.
# A misconfigured key (e.g. wrong SUPABASE_SECRET_KEY) otherwise surfaces as a
# blank 500; here it becomes an explicit 503 the frontend can display.
@app.exception_handler(APIError)
async def _supabase_error_handler(_req: Request, exc: APIError) -> JSONResponse:
    code = str(getattr(exc, "code", "") or "")
    message = str(getattr(exc, "message", "") or exc)
    is_auth = code in {"401", "403"} or "api key" in message.lower()
    log.error("Supabase APIError (code=%s): %s", code, message)
    if is_auth:
        return JSONResponse(
            status_code=503,
            content={"detail": "Database authentication failed — check server configuration."},
        )
    return JSONResponse(status_code=502, content={"detail": "Database request failed."})


# Last-resort handler so an unexpected error becomes a readable message with a
# 500 status instead of an empty body (which the frontend renders as a bare
# "500:"). The class name is safe to surface; details stay in the Space logs.
@app.exception_handler(Exception)
async def _unhandled_error_handler(_req: Request, exc: Exception) -> JSONResponse:
    log.exception("Unhandled error on %s %s", _req.method, _req.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": f"Unexpected server error ({type(exc).__name__}). See server logs."},
    )

# Allow the React dev server (Vite) by default; override via FRONTEND_ORIGINS.
_origins = os.getenv("FRONTEND_ORIGINS", "http://localhost:5173").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/me")
def me(user: CurrentUser = Depends(get_current_user)) -> dict:
    """Return the authenticated, active user's identity + role."""
    return {"id": user.id, "email": user.email, "role": user.role}
