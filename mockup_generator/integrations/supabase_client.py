"""Supabase client factories.

- ``anon_client``: publishable-key client (RLS applies as the anonymous role).
- ``service_client``: secret-key client that bypasses RLS for trusted
  server-side writes. Only available when ``SUPABASE_SECRET_KEY`` is set.
- ``client_for_user``: a client acting as a specific logged-in user, used to
  read/write under that user's RLS policies given their access token.
"""

from __future__ import annotations

from functools import lru_cache

import httpx
from supabase import Client, ClientOptions, create_client

from mockup_generator.config import settings


class _ResilientClient(httpx.Client):
    """httpx client that retries once when a pooled connection is stale.

    Long-lived (lru_cached) Supabase clients keep connections alive between
    requests. After an idle period the server closes the connection (HTTP/2
    GOAWAY or a dropped HTTP/1.1 keep-alive); the next reuse then raises
    ``RemoteProtocolError``/``ConnectError`` *before the request is sent*, which
    surfaced as intermittent 500s. Retrying once reopens a fresh connection.
    The request never reached the server, so the retry is safe for writes too.
    """

    def send(self, request, **kwargs):  # type: ignore[override]
        try:
            return super().send(request, **kwargs)
        except (httpx.RemoteProtocolError, httpx.ConnectError):
            return super().send(request, **kwargs)


def _httpx_client() -> httpx.Client:
    # HTTP/1.1 (http2=False) avoids the GOAWAY churn seen on the Space; the
    # retry wrapper covers any remaining stale keep-alive reuse.
    return _ResilientClient(http2=False, timeout=httpx.Timeout(30.0))


def _require_url_and_key() -> tuple[str, str]:
    url = settings.supabase_url
    key = settings.supabase_publishable_key
    if not url or not key:
        raise RuntimeError("SUPABASE_PROJECT_ID and SUPABASE_PUBLISHABLE_KEY must be set")
    return url, key


@lru_cache(maxsize=1)
def anon_client() -> Client:
    url, key = _require_url_and_key()
    return create_client(url, key, options=ClientOptions(httpx_client=_httpx_client()))


@lru_cache(maxsize=1)
def service_client() -> Client | None:
    """Service-role client, or None when no secret key is configured."""
    url = settings.supabase_url
    secret = settings.supabase_secret_key
    if not url or not secret:
        return None
    return create_client(url, secret, options=ClientOptions(httpx_client=_httpx_client()))


def client_for_user(access_token: str) -> Client:
    """A fresh client whose PostgREST/Storage requests act as the given user."""
    url, key = _require_url_and_key()
    return create_client(
        url,
        key,
        options=ClientOptions(
            headers={"Authorization": f"Bearer {access_token}"},
            httpx_client=_httpx_client(),
        ),
    )
