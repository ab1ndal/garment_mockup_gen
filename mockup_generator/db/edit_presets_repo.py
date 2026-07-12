"""CRUD for global edit presets (docs/migrations/2026-07-12-edit-presets.sql)."""

from __future__ import annotations

from supabase import Client

_TABLE = "edit_presets"


def list_all(client: Client) -> list[dict]:
    resp = client.table(_TABLE).select("*").order("created_at").execute()
    return resp.data or []


def get_default(client: Client) -> dict | None:
    resp = client.table(_TABLE).select("*").eq("is_default", True).limit(1).execute()
    rows = resp.data or []
    return rows[0] if rows else None


def set_default(client: Client, preset_id: int) -> None:
    """Exactly-one-default: clear the current default, then set the target."""
    client.table(_TABLE).update({"is_default": False}).eq("is_default", True).execute()
    client.table(_TABLE).update({"is_default": True}).eq("preset_id", preset_id).execute()


def insert(client: Client, *, name: str, params: dict, is_default: bool,
           created_by: str | None) -> dict:
    if is_default:
        client.table(_TABLE).update({"is_default": False}).eq("is_default", True).execute()
    payload = {"name": name, "params": params, "is_default": is_default,
               "created_by": created_by}
    resp = client.table(_TABLE).insert(payload).execute()
    return (resp.data or [{}])[0]


def delete(client: Client, preset_id: int) -> None:
    client.table(_TABLE).delete().eq("preset_id", preset_id).execute()
