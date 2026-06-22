"""Read access to the existing ``profiles`` table (the team allowlist)."""

from __future__ import annotations

from dataclasses import dataclass

from supabase import Client


@dataclass
class Profile:
    id: str
    email: str
    role: str | None
    is_active: bool


def get_profile_by_id(client: Client, user_id: str) -> Profile | None:
    resp = (
        client.table("profiles")
        .select("id, email, role, is_active")
        .eq("id", user_id)
        .limit(1)
        .execute()
    )
    rows = resp.data or []
    if not rows:
        return None
    r = rows[0]
    return Profile(id=r["id"], email=r["email"], role=r.get("role"), is_active=bool(r["is_active"]))
