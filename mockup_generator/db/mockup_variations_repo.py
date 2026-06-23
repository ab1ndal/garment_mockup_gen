"""Writes to ``mockup_variations`` — one row per generation (audit + listing)."""

from __future__ import annotations

from supabase import Client


def insert(
    client: Client,
    *,
    productid: str,
    prompt_text: str,
    image_url: str,
    kind: str = "image",
    prompt_id: int | None = None,
    created_by: str | None = None,
) -> dict:
    """Insert one variation row and return it. Omits unset nullable columns."""
    payload: dict = {
        "productid": productid,
        "prompt_text": prompt_text,
        "image_url": image_url,
        "kind": kind,
    }
    if prompt_id is not None:
        payload["prompt_id"] = prompt_id
    if created_by is not None:
        payload["created_by"] = created_by

    resp = client.table("mockup_variations").insert(payload).execute()
    return (resp.data or [{}])[0]
