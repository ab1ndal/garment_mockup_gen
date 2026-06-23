# backend/deps.py
"""Shared FastAPI dependencies for DB access."""

from __future__ import annotations

from fastapi import Header
from supabase import Client

from backend.auth import _bearer_token
from mockup_generator.integrations.supabase_client import client_for_user, service_client


def get_db(authorization: str | None = Header(default=None)) -> Client:
    """Service-role client when a secret key is configured, else act as the user.

    Routes that use this also depend on get_current_user, so the request is
    already gated; this only chooses which Supabase client performs the query.
    """
    svc = service_client()
    if svc is not None:
        return svc
    return client_for_user(_bearer_token(authorization))
