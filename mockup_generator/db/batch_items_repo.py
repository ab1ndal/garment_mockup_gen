"""Read/write access to ``batch_items`` — the Batch Generate worklist.

One row per (product, color) card. Status transitions are optimistic:
``transition`` issues a conditional update guarded on the expected current
status and reports whether it won the row, so concurrent reviewers (and the
worker) can't both act on the same card.
"""

from __future__ import annotations

from dataclasses import dataclass

from supabase import Client

from .product_ids import product_key

QUEUED = "queued"
GENERATING = "generating"
READY = "ready"
FAILED = "failed"
PUBLISHED = "published"
REJECTED = "rejected"

ALL_STATUSES = [QUEUED, GENERATING, READY, FAILED, PUBLISHED, REJECTED]

_COLS = (
    "id, batch_id, productid, color, image_ids, prompt_text, status, "
    "storage_path, error, model, resolution, aspect_ratio"
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


def _fetch_all(client: Client, statuses: list[str]) -> list[dict]:
    """Every row in ``statuses``, walked in chunks past PostgREST's per-request
    row cap so in-memory sorting sees the whole set."""
    out: list[dict] = []
    start = 0
    while True:
        resp = (
            client.table("batch_items").select(_COLS)
            .in_("status", statuses)
            .range(start, start + _FETCH_CHUNK - 1)
            .execute()
        )
        batch = resp.data or []
        out.extend(batch)
        if len(batch) < _FETCH_CHUNK:
            return out
        start += _FETCH_CHUNK


def page(
    client: Client, *, statuses: list[str], offset: int, limit: int,
    sort_by_product: bool = False,
) -> tuple[list[BatchRow], int]:
    # Product-key order can't be expressed in the query: it's a parsed expression,
    # and lexical id order breaks across the 3/4-digit sequence boundary. So fetch
    # the full (small) status set, sort in memory, and slice the page.
    if sort_by_product:
        all_rows = _fetch_all(client, statuses)
        all_rows.sort(key=_product_sort_key)
        window = all_rows[offset:offset + limit]
        return [_row(r) for r in window], len(all_rows)

    resp = (
        client.table("batch_items").select(_COLS, count="exact")
        .in_("status", statuses)
        .order("id", desc=False)
        .range(offset, offset + limit - 1)
        .execute()
    )
    rows = [_row(r) for r in (resp.data or [])]
    total = resp.count if resp.count is not None else len(rows)
    return rows, total


def counts(client: Client) -> dict[str, int]:
    out: dict[str, int] = {}
    for s in ALL_STATUSES:
        resp = (
            client.table("batch_items").select("id", count="exact")
            .eq("status", s).limit(1).execute()
        )
        out[s] = resp.count or 0
    return out


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


def claim_next_queued(client: Client) -> BatchRow | None:
    """Claim the oldest ``queued`` row (queued -> generating). Race-safe: if the
    conditional update loses (another worker won), retry the next candidate.
    Returns the claimed row, or None when no queued rows remain."""
    while True:
        resp = (
            client.table("batch_items").select(_COLS)
            .eq("status", QUEUED).order("id", desc=False).limit(1).execute()
        )
        rows = resp.data or []
        if not rows:
            return None
        row = _row(rows[0])
        if transition(client, item_id=row.id, expect=QUEUED, to=GENERATING):
            row.status = GENERATING
            return row
        # lost the race; try the next candidate


def reset_orphaned_generating(client: Client) -> int:
    """Crash recovery: flip any ``generating`` rows back to ``queued``."""
    resp = (
        client.table("batch_items")
        .update({"status": QUEUED, "updated_at": "now()"})
        .eq("status", GENERATING).execute()
    )
    return len(resp.data or [])
