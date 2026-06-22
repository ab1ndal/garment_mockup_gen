"""Supabase client factories.

- ``anon_client``: publishable-key client (RLS applies as the anonymous role).
- ``service_client``: secret-key client that bypasses RLS for trusted
  server-side writes. Only available when ``SUPABASE_SECRET_KEY`` is set.
- ``client_for_user``: a client acting as a specific logged-in user, used to
  read/write under that user's RLS policies given their access token.
"""

from __future__ import annotations

from functools import lru_cache

from supabase import Client, ClientOptions, create_client

from mockup_generator.config import settings


def _require_url_and_key() -> tuple[str, str]:
    url = settings.supabase_url
    key = settings.supabase_publishable_key
    if not url or not key:
        raise RuntimeError("SUPABASE_PROJECT_ID and SUPABASE_PUBLISHABLE_KEY must be set")
    return url, key


@lru_cache(maxsize=1)
def anon_client() -> Client:
    url, key = _require_url_and_key()
    return create_client(url, key)


@lru_cache(maxsize=1)
def service_client() -> Client | None:
    """Service-role client, or None when no secret key is configured."""
    url = settings.supabase_url
    secret = settings.supabase_secret_key
    if not url or not secret:
        return None
    return create_client(url, secret)


def client_for_user(access_token: str) -> Client:
    """A fresh client whose PostgREST/Storage requests act as the given user."""
    url, key = _require_url_and_key()
    return create_client(
        url,
        key,
        options=ClientOptions(headers={"Authorization": f"Bearer {access_token}"}),
    )
