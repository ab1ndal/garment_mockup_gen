"""Read/write access to ``batch_items`` — the Batch Generate worklist.

One row per (product, color) card. Status transitions are optimistic:
``transition`` issues a conditional update guarded on the expected current
status and reports whether it won the row, so concurrent reviewers (and the
worker) can't both act on the same card.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from supabase import Client

from .product_ids import product_key

QUEUED = "queued"
GENERATING = "generating"
READY = "ready"
FAILED = "failed"
PUBLISHED = "published"
REJECTED = "rejected"

ALL_STATUSES = [QUEUED, GENERATING, READY, FAILED, PUBLISHED, REJECTED]

# A product with a card in one of these has an un-reviewed generation — queued,
# running, or waiting in Ready to be accepted — so re-enqueuing it would just
# duplicate the work. Failed is a resolved attempt (retried on its own card), and
# reviewed outcomes don't block: accepted products drop out via the base_mockup
# filter, and rejected ones are meant to be regenerated.
ACTIVE_STATUSES = [QUEUED, GENERATING, READY]

_COLS = (
    "id, batch_id, productid, color, image_ids, prompt_text, status, "
    "storage_path, error, model, resolution, aspect_ratio, attempts"
)


@dataclass
class BatchRow:
    id: int
    batch_id: str
    productid: str
    color: str | None
    image_ids: list[str]
    prompt_text: str
    status: str
    storage_path: str | None
    error: str | None
    model: str
    resolution: str
    aspect_ratio: str
    attempts: int = 0


def _row(r: dict) -> BatchRow:
    return BatchRow(
        id=int(r["id"]),
        batch_id=r["batch_id"],
        productid=r["productid"],
        color=r.get("color"),
        image_ids=list(r.get("image_ids") or []),
        prompt_text=r["prompt_text"],
        status=r["status"],
        storage_path=r.get("storage_path"),
        error=r.get("error"),
        model=r["model"],
        resolution=r["resolution"],
        aspect_ratio=r["aspect_ratio"],
        attempts=int(r.get("attempts") or 0),
    )


def insert_many(client: Client, rows: list[dict]) -> int:
    if not rows:
        return 0
    resp = client.table("batch_items").insert(rows).execute()
    return len(resp.data or [])


_FETCH_CHUNK = 1000


def _product_sort_key(r: dict) -> tuple[bool, int, str, int]:
    """Order rows by product id (BC25s before BC26s, then by sequence), keeping a
    product's colour variants adjacent. Equivalent to stripping ``BC``, padding
    the sequence to a fixed width, and sorting numerically. Malformed ids sort
    last; colour then row id break ties for a stable order."""
    key = product_key(r.get("productid"))
    return (key is None, key or 0, r.get("color") or "", int(r["id"]))


def _apply_filters(q, categoryids: list[str] | None, productid: str | None):
    """Shared category + product-id-prefix filters for the list queries.
    ``categoryids`` OR-matches any of the given categories; ``productid`` is a
    prefix (already sanitised by the caller to the product-id charset, so it
    carries no LIKE wildcards) matched case-insensitively."""
    if categoryids:
        q = q.in_("categoryid", categoryids)
    if productid:
        q = q.ilike("productid", f"{productid}%")
    return q


def _fetch_all(client: Client, statuses: list[str],
               categoryids: list[str] | None = None, productid: str | None = None) -> list[dict]:
    """Every row in ``statuses``, walked in chunks past PostgREST's per-request
    row cap so in-memory sorting sees the whole set."""
    out: list[dict] = []
    start = 0
    while True:
        q = client.table("batch_items").select(_COLS).in_("status", statuses)
        q = _apply_filters(q, categoryids, productid)
        resp = q.range(start, start + _FETCH_CHUNK - 1).execute()
        batch = resp.data or []
        out.extend(batch)
        if len(batch) < _FETCH_CHUNK:
            return out
        start += _FETCH_CHUNK


def page(
    client: Client, *, statuses: list[str], offset: int, limit: int,
    sort_by_product: bool = False, categoryids: list[str] | None = None,
    productid: str | None = None,
) -> tuple[list[BatchRow], int]:
    # Product-key order can't be expressed in the query: it's a parsed expression,
    # and lexical id order breaks across the 3/4-digit sequence boundary. So fetch
    # the full (small) status set, sort in memory, and slice the page.
    if sort_by_product:
        all_rows = _fetch_all(client, statuses, categoryids, productid)
        all_rows.sort(key=_product_sort_key)
        window = all_rows[offset:offset + limit]
        return [_row(r) for r in window], len(all_rows)

    q = client.table("batch_items").select(_COLS, count="exact").in_("status", statuses)
    q = _apply_filters(q, categoryids, productid)
    resp = q.order("id", desc=False).range(offset, offset + limit - 1).execute()
    rows = [_row(r) for r in (resp.data or [])]
    total = resp.count if resp.count is not None else len(rows)
    return rows, total


@dataclass
class CategorySummary:
    categoryid: str
    name: str | None
    unpublished: int
    ready: int
    queued: int


def category_summary(client: Client) -> list[CategorySummary]:
    """Per-category review backlog via the batch_category_summary() SQL function:
    ``unpublished`` products (no published mockup — includes failed and
    never-queued), plus ``ready`` and ``queued`` (incl. generating) card counts.
    One round-trip; sorted most-unpublished first."""
    resp = client.rpc("batch_category_summary").execute()
    rows = [
        CategorySummary(
            categoryid=r["categoryid"], name=r.get("name"),
            unpublished=int(r["unpublished"]), ready=int(r["ready"]),
            queued=int(r["queued"]),
        )
        for r in (resp.data or [])
    ]
    rows.sort(key=lambda s: (-s.unpublished, -s.ready, s.name or s.categoryid))
    return rows


def counts(client: Client) -> dict[str, int]:
    # One grouped round-trip via the batch_status_counts() SQL function instead of
    # one exact-count query per status. The counts endpoint is polled every few
    # seconds, so collapsing 6 round-trips into 1 is the win. Statuses with no rows
    # are absent from the result, so fill them to keep the full-status contract.
    resp = client.rpc("batch_status_counts").execute()
    seen = {r["status"]: int(r["n"]) for r in (resp.data or [])}
    return {s: seen.get(s, 0) for s in ALL_STATUSES}


def active_productids(client: Client, productids: list[str]) -> set[str]:
    """Of ``productids``, those that already have an un-reviewed card
    (see ``ACTIVE_STATUSES``) — used to skip re-enqueuing them."""
    if not productids:
        return set()
    resp = (
        client.table("batch_items").select("productid")
        .in_("status", ACTIVE_STATUSES)
        .in_("productid", productids)
        .execute()
    )
    return {r["productid"] for r in (resp.data or [])}


def get(client: Client, item_id: int) -> BatchRow | None:
    resp = client.table("batch_items").select(_COLS).eq("id", item_id).limit(1).execute()
    rows = resp.data or []
    return _row(rows[0]) if rows else None


def transition(client: Client, *, item_id: int, expect: str, to: str, **fields) -> bool:
    """Conditionally move a row ``expect -> to``, merging any extra column
    ``fields`` into the same update. Returns True iff this call won the row."""
    payload = {"status": to, "updated_at": "now()", **fields}
    resp = (
        client.table("batch_items").update(payload)
        .eq("id", item_id).eq("status", expect).execute()
    )
    return bool(resp.data)


def claim_next_queued(client: Client, assign_model: str | None = None) -> BatchRow | None:
    """Claim the next ``queued`` row (queued -> generating). Race-safe: if the
    conditional update loses (another worker won), retry the next candidate.
    Returns the claimed row, or None when no queued rows remain.

    ``assign_model`` stamps that model on the row as it is claimed, overriding
    whatever was set at enqueue. This is how the per-model drainer pools fan the
    batch out across independent capacity pools: a pool's drainer claims any
    queued card and records the model that actually generated it.

    Ordered by ``attempts`` then id, so a requeued transient failure (attempts>0)
    waits behind every fresh card. That both keeps first-pass work moving and
    gives the rate limit a cooldown before the same card is tried again."""
    fields = {"model": assign_model} if assign_model else {}
    while True:
        resp = (
            client.table("batch_items").select(_COLS)
            .eq("status", QUEUED)
            .order("attempts", desc=False).order("id", desc=False)
            .limit(1).execute()
        )
        rows = resp.data or []
        if not rows:
            return None
        row = _row(rows[0])
        if transition(client, item_id=row.id, expect=QUEUED, to=GENERATING, **fields):
            row.status = GENERATING
            if assign_model:
                row.model = assign_model
            return row
        # lost the race; try the next candidate


def reset_orphaned_generating(client: Client) -> int:
    """Crash recovery: flip any ``generating`` rows back to ``queued``. Safe only
    at startup, when no worker is running and every ``generating`` row is stale."""
    resp = (
        client.table("batch_items")
        .update({"status": QUEUED, "updated_at": "now()"})
        .eq("status", GENERATING).execute()
    )
    return len(resp.data or [])


def reset_stale_generating(client: Client, older_than_seconds: int) -> int:
    """Reclaim rows stuck in ``generating`` longer than any healthy generation
    could run — a drainer that died mid-card without reaching a terminal
    transition. Unlike ``reset_orphaned_generating`` (all rows), this is bounded
    by age, so it is safe to call while other drainers are live: it never steals a
    row one of them is still working. ``attempts`` is left untouched — the card
    simply returns to the queue for another pass."""
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)).isoformat()
    resp = (
        client.table("batch_items")
        .update({"status": QUEUED, "updated_at": "now()"})
        .eq("status", GENERATING).lt("updated_at", cutoff).execute()
    )
    return len(resp.data or [])
