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

from collections import Counter
from dataclasses import dataclass

from postgrest.exceptions import APIError
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

# Rows per request when scanning the worklist; PostgREST caps a response at 1000.
_SCAN_PAGE = 1000

# PostgREST's reply to an offset past the last row (HTTP 416).
_RANGE_NOT_SATISFIABLE = "PGRST103"


@dataclass
class BackfillRow:
    file_id: str
    productid: str | None
    alpha: str | None
    filename: str
    thumbnail_link: str | None
    status: str
    # Filled only by ``page`` (embedded from ``products``); ``product_found`` is
    # False when productid is NULL or names no product.
    product_name: str | None = None
    product_found: bool = False
    categoryid: str | None = None


def _row(r: dict) -> BackfillRow:
    product = r.get("products")
    return BackfillRow(
        file_id=r["file_id"],
        productid=r.get("productid"),
        alpha=r.get("alpha"),
        filename=r["filename"],
        thumbnail_link=r.get("thumbnail_link"),
        status=r["status"],
        product_name=product.get("name") if product else None,
        product_found=product is not None,
        categoryid=product.get("categoryid") if product else None,
    )


def page(client: Client, *, status: str, offset: int, limit: int,
         category: str | None = None) -> tuple[list[BackfillRow], int]:
    """Return ``(rows, total)`` for one page of a status, ordered by filename.

    ``total`` is the full count for the status (for the pager), fetched in the same
    request via Supabase's exact count. Product names are embedded through the
    ``productid`` foreign key, so the page needs no second lookup.

    ``category`` narrows both the page and ``total`` to one ``categoryid``. Only
    then is the embed an inner join: without it, rows with no product must stay.

    An offset past the end (another reviewer shrank the list) returns no rows and
    the real total, not an error, so the caller can step back to the last page.
    """
    embed = "products!inner" if category else "products"
    query = (
        client.table("backfill_items").select(f"{_COLS}, {embed}(name, categoryid)", count="exact")
        .eq("status", status)
    )
    if category:
        query = query.eq("products.categoryid", category)
    query = query.order("filename")
    try:
        resp = query.range(offset, offset + limit - 1).execute()
    except APIError as exc:
        if exc.code != _RANGE_NOT_SATISFIABLE:
            raise
        return [], query.range(0, 0).execute().count or 0
    rows = [_row(r) for r in (resp.data or [])]
    total = resp.count if resp.count is not None else len(rows)
    return rows, total


def counts(client: Client) -> dict[str, int]:
    """Return ``{status: count}`` for each sub-tab status.

    One grouped round-trip via the backfill_status_counts() SQL function instead
    of an exact-count query per status (4 x ~650ms to the Mumbai database).
    Statuses with no rows are absent from the result, so fill them with 0.
    """
    resp = client.rpc("backfill_status_counts").execute()
    seen = {r["status"]: int(r["n"]) for r in (resp.data or [])}
    return {s: seen.get(s, 0) for s in TAB_STATUSES}


def category_counts(client: Client) -> dict[str, dict[str, int]]:
    """Return ``{status: {categoryid: count}}`` for each sub-tab status.

    Scans the open worklist in ranged pages and tallies here: PostgREST aggregates
    are disabled on this project, and one exact-count query per category would be
    far more round-trips than the three or so pages this takes. Published rows are
    excluded to keep the scan bounded. Rows with no category are not tallied.
    """
    tally: Counter[tuple[str, str]] = Counter()
    offset = 0
    while True:
        resp = (
            client.table("backfill_items").select("status, products(categoryid)", count="exact")
            .neq("status", PUBLISHED)
            .order("file_id")
            .range(offset, offset + _SCAN_PAGE - 1)
            .execute()
        )
        rows = resp.data or []
        for r in rows:
            categoryid = (r.get("products") or {}).get("categoryid")
            if categoryid:
                tally[(r["status"], categoryid)] += 1
        offset += len(rows)
        if not rows or offset >= (resp.count or 0):
            break
    out: dict[str, dict[str, int]] = {s: {} for s in TAB_STATUSES}
    for (status, categoryid), n in tally.items():
        if status in out:
            out[status][categoryid] = n
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
