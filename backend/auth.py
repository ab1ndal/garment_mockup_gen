"""Authentication dependency for the API.

Flow:
1. Read the ``Authorization: Bearer <token>`` header (a Supabase access token
   obtained by the React app via Google OAuth).
2. Verify it with Supabase Auth (``auth.get_user``) → the authenticated user.
3. Look up the matching ``profiles`` row and require ``is_active = true``.
   The profile read uses the service client when a secret key is configured,
   otherwise the user's own session (subject to RLS).

Any failure raises 401 (bad/absent token) or 403 (no active profile).
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status

from mockup_generator.db.profiles_repo import Profile, get_profile_by_id
from mockup_generator.integrations.supabase_client import (
    anon_client,
    client_for_user,
    service_client,
)


@dataclass
class CurrentUser:
    id: str
    email: str
    role: str | None
    profile: Profile


# Per-token cache of the resolved user. Every authed request otherwise pays two
# serial Supabase round-trips before its real query — a remote `auth.get_user`
# (GoTrue) plus a `profiles` read. The database is in Mumbai and the backend is
# not, so that is ~1.5-2s; at a 30s window nearly every click after a pause paid
# it. Tradeoff: a user deactivated in the Inventory portal keeps access for up to
# _AUTH_TTL seconds. An entry never outlives the token's own ``exp``, so an
# expired token is never accepted from the cache.
_AUTH_TTL = 300.0  # seconds
_AUTH_CACHE: dict[str, tuple[float, "CurrentUser"]] = {}  # token -> (valid_until, user)


def _seconds_until_expiry(token: str) -> float | None:
    """Seconds until the JWT's ``exp`` claim, or None if it can't be read.

    Only called after Supabase has verified the token, so the payload is trusted;
    no signature check is needed here.
    """
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return float(claims["exp"]) - time.time()
    except (IndexError, KeyError, TypeError, ValueError):
        return None


def _bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return authorization.split(" ", 1)[1].strip()


def get_current_user(authorization: str | None = Header(default=None)) -> CurrentUser:
    token = _bearer_token(authorization)

    now = time.monotonic()
    cached = _AUTH_CACHE.get(token)
    if cached is not None and now < cached[0]:
        return cached[1]

    # 1) Verify the token with Supabase Auth.
    try:
        user_resp = anon_client().auth.get_user(token)
    except Exception as exc:  # network / malformed token
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token verification failed") from exc

    user = getattr(user_resp, "user", None)
    if user is None or not getattr(user, "id", None):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")

    # 2) Load the profile (service client preferred; else act as the user).
    reader = service_client() or client_for_user(token)
    profile = get_profile_by_id(reader, user.id)

    # 3) Gate on active membership.
    if profile is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No profile for this account")
    if not profile.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is not active")

    current = CurrentUser(id=profile.id, email=profile.email, role=profile.role, profile=profile)

    # Cache + drop expired entries (tokens rotate; don't let the dict grow).
    # An unreadable exp falls back to the full TTL: Supabase just accepted the
    # token, so it is valid now.
    remaining = _seconds_until_expiry(token)
    ttl = _AUTH_TTL if remaining is None else min(_AUTH_TTL, remaining)
    _AUTH_CACHE[token] = (now + ttl, current)
    if len(_AUTH_CACHE) > 1:
        for k, (valid_until, _) in list(_AUTH_CACHE.items()):
            if now >= valid_until:
                _AUTH_CACHE.pop(k, None)

    return current


def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if user.role not in {"admin", "superadmin"}:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin role required")
    return user
