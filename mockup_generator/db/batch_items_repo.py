"""Read/write access to ``batch_items`` — the Batch Generate worklist.

One row per (product, color) card. Status transitions are optimistic:
``transition`` issues a conditional update guarded on the expected current
status and reports whether it won the row, so concurrent reviewers (and the
worker) can't both act on the same card.
"""

from __future__ import annotations

from dataclasses import dataclass

from supabase import Client

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


def page(client: Client, *, statuses: list[str], offset: int, limit: int) -> tuple[list[BatchRow], int]:
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
