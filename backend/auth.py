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
# (GoTrue) plus a `profiles` read — which makes even a light fetch like
# /api/prompts feel slow. Re-verifying the same token every few seconds adds no
# safety, so cache the result briefly. Tradeoff: a deactivated or expired user
# stays valid up to _AUTH_TTL seconds. Keep the window short.
_AUTH_TTL = 30.0  # seconds
_AUTH_CACHE: dict[str, tuple[float, "CurrentUser"]] = {}


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
    if cached is not None and now - cached[0] < _AUTH_TTL:
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
    _AUTH_CACHE[token] = (now, current)
    if len(_AUTH_CACHE) > 1:
        for k, (ts, _) in list(_AUTH_CACHE.items()):
            if now - ts >= _AUTH_TTL:
                _AUTH_CACHE.pop(k, None)

    return current


def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if user.role not in {"admin", "superadmin"}:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin role required")
    return user
