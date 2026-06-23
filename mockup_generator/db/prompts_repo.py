"""CRUD for per-category named prompt variants (Phase 2)."""

from __future__ import annotations

from dataclasses import dataclass

from supabase import Client

from mockup_generator.prompts.defaults import CATEGORY_PROMPTS

_COLS = "prompt_id, categoryid, label, body, is_default"


@dataclass
class Prompt:
    prompt_id: int
    categoryid: str
    label: str
    body: str
    is_default: bool


def _row(r: dict) -> Prompt:
    return Prompt(
        prompt_id=int(r["prompt_id"]),
        categoryid=r["categoryid"],
        label=r["label"],
        body=r["body"],
        is_default=bool(r["is_default"]),
    )


def list_by_category(client: Client, categoryid: str) -> list[Prompt]:
    resp = (
        client.table("prompts").select(_COLS)
        .eq("categoryid", categoryid)
        .order("is_default", desc=True).order("label").execute()
    )
    return [_row(r) for r in (resp.data or [])]


def _clear_defaults(client: Client, categoryid: str) -> None:
    client.table("prompts").update({"is_default": False}).eq("categoryid", categoryid).execute()


def create(client: Client, *, categoryid: str, label: str, body: str,
           is_default: bool = False, updated_by: str | None = None) -> Prompt:
    if is_default:
        _clear_defaults(client, categoryid)
    payload = {"categoryid": categoryid, "label": label, "body": body,
               "is_default": is_default, "updated_by": updated_by}
    resp = client.table("prompts").insert(payload).execute()
    return _row(resp.data[0])


def update(client: Client, prompt_id: int, *, label: str | None = None,
           body: str | None = None, is_default: bool | None = None,
           updated_by: str | None = None) -> Prompt:
    if is_default:
        cur = client.table("prompts").select("categoryid").eq("prompt_id", prompt_id).limit(1).execute()
        if cur.data:
            _clear_defaults(client, cur.data[0]["categoryid"])
    payload: dict = {"updated_at": "now()"}
    if label is not None:
        payload["label"] = label
    if body is not None:
        payload["body"] = body
    if is_default is not None:
        payload["is_default"] = is_default
    if updated_by is not None:
        payload["updated_by"] = updated_by
    resp = client.table("prompts").update(payload).eq("prompt_id", prompt_id).execute()
    return _row(resp.data[0])


def delete(client: Client, prompt_id: int) -> None:
    client.table("prompts").delete().eq("prompt_id", prompt_id).execute()


def seed_defaults(client: Client) -> int:
    inserted = 0
    for categoryid, body in CATEGORY_PROMPTS.items():
        existing = (
            client.table("prompts").select("prompt_id")
            .eq("categoryid", categoryid).eq("label", "Default").limit(1).execute()
        )
        if existing.data:
            continue
        client.table("prompts").insert(
            {"categoryid": categoryid, "label": "Default", "body": body, "is_default": True}
        ).execute()
        inserted += 1
    return inserted
