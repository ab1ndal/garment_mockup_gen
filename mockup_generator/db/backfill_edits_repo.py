"""Writes to ``backfill_edits`` — one row per mockup sent back for manual editing.

Reviewers flag a generated mockup "for edits": the Drive original moves to the
``edit/`` worklist subfolder and a pending row is recorded here so a future pass
can pick up what needs fixing. ``file_id`` is stable across the Drive move.
"""

from __future__ import annotations

from supabase import Client


def insert(
    client: Client,
    *,
    file_id: str,
    productid: str | None = None,
    comment: str | None = None,
    created_by: str | None = None,
) -> dict:
    """Insert one pending edit row and return it. Omits unset nullable columns."""
    payload: dict = {"file_id": file_id}
    if productid is not None:
        payload["productid"] = productid
    if comment is not None:
        payload["comment"] = comment
    if created_by is not None:
        payload["created_by"] = created_by

    resp = client.table("backfill_edits").insert(payload).execute()
    return (resp.data or [{}])[0]
