"""Read/write access to ``backfill_items`` — the DB-backed review worklist.

Review state lives here instead of being inferred from Google Drive folder
location, so listing a page of cards is a single indexed query rather than a full
Drive scan. ``file_id`` (the Drive id) is the primary key and is stable across the
Drive folder moves the review flow still performs as a mirror.

Status transitions are optimistic: ``transition`` issues a conditional update
guarded on the expected current status and reports whether it won the row, so two
reviewers acting on the same card can't both succeed.
"""

from __future__ import annotations

from dataclasses import dataclass

from supabase import Client

# Status values mirror the reserved Drive subfolders (see the migration).
PENDING = "pending"
SKIPPED = "skipped"
EDIT = "edit"
REGENERATE = "regenerate"
PUBLISHED = "published"

# Statuses surfaced as sub-tabs (published is archived/hidden).
TAB_STATUSES = [PENDING, SKIPPED, EDIT, REGENERATE]

_COLS = "file_id, productid, alpha, filename, thumbnail_link, status, created_at, updated_at"


@dataclass
class BackfillRow:
    file_id: str
    productid: str | None
    alpha: str | None
    filename: str
    thumbnail_link: str | None
    status: str


def _row(r: dict) -> BackfillRow:
    return BackfillRow(
        file_id=r["file_id"],
        productid=r.get("productid"),
        alpha=r.get("alpha"),
        filename=r["filename"],
        thumbnail_link=r.get("thumbnail_link"),
        status=r["status"],
    )


def page(client: Client, *, status: str, offset: int, limit: int) -> tuple[list[BackfillRow], int]:
    """Return ``(rows, total)`` for one page of a status, ordered by filename.

    ``total`` is the full count for the status (for the pager), fetched in the same
    request via Supabase's exact count.
    """
    resp = (
        client.table("backfill_items").select(_COLS, count="exact")
        .eq("status", status)
        .order("filename")
        .range(offset, offset + limit - 1)
        .execute()
    )
    rows = [_row(r) for r in (resp.data or [])]
    total = resp.count if resp.count is not None else len(rows)
    return rows, total


def counts(client: Client) -> dict[str, int]:
    """Return ``{status: count}`` for each sub-tab status (one head query each)."""
    out: dict[str, int] = {}
    for s in TAB_STATUSES:
        resp = (
            client.table("backfill_items").select("file_id", count="exact")
            .eq("status", s).limit(1).execute()
        )
        out[s] = resp.count or 0
    return out


def get(client: Client, file_id: str) -> BackfillRow | None:
    resp = (
        client.table("backfill_items").select(_COLS)
        .eq("file_id", file_id).limit(1).execute()
    )
    rows = resp.data or []
    return _row(rows[0]) if rows else None


def transition(client: Client, *, file_id: str, expect: str, to: str) -> bool:
    """Conditionally move a row ``expect -> to``. Returns ``True`` iff this call won
    the row (it was still in ``expect``). The guard makes concurrent reviewers safe:
    only one update matches a given pending row."""
    resp = (
        client.table("backfill_items")
        .update({"status": to, "updated_at": "now()"})
        .eq("file_id", file_id).eq("status", expect)
        .execute()
    )
    return bool(resp.data)


def upsert_many(client: Client, rows: list[dict]) -> int:
    """Upsert seed/rescan rows by ``file_id``. ``status`` reflects the Drive folder
    the file currently sits in, so the Drive folder wins on reconcile. Returns the
    number of rows written."""
    if not rows:
        return 0
    resp = client.table("backfill_items").upsert(rows, on_conflict="file_id").execute()
    return len(resp.data or [])
