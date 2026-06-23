"""FastAPI application entrypoint.

Run locally:  poetry run uvicorn backend.main:app --reload --port 8000
"""

from __future__ import annotations

import os

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.auth import CurrentUser, get_current_user
from backend.routers import products as products_router
from backend.routers import prompts as prompts_router

app = FastAPI(title="Bindal's Creation — Mockup Generator API")

app.include_router(products_router.router)
app.include_router(prompts_router.router)

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
