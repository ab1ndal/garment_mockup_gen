"""Read and write ``product_copy_drafts``: AI text waiting for a person to approve it.

Never touches ``products``. Status changes are conditional updates, so two workers
or two reviewers cannot both win the same row, and the partial unique index on
active rows means a product has one current draft however many screens ask.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from postgrest.exceptions import APIError
from supabase import Client

QUEUED = "queued"
DRAFTING = "drafting"
READY = "ready"
FAILED = "failed"
IN_FLIGHT = (QUEUED, DRAFTING)

_TABLE = "product_copy_drafts"
_COLS = ("id, productid, status, source_card, images_key, image_refs, note, attempts, "
         "error, draft, cost_usd")
_UNIQUE_VIOLATION = "23505"
# 42P01: what production PostgREST returned for a missing table when checked on
# 2026-11-03. PGRST205: what newer versions return once the schema cache knows.
_TABLE_MISSING = ("42P01", "PGRST205")


@dataclass
class CopyDraft:
    id: int
    productid: str
    status: str
    source_card: str
    images_key: str
    image_refs: dict
    note: str | None = None
    attempts: int = 0
    error: str | None = None
    draft: dict | None = None
    cost_usd: float = 0.0


def is_table_missing(exc: Exception) -> bool:
    """True when the migration has not been applied yet."""
    return isinstance(exc, APIError) and exc.code in _TABLE_MISSING


def _row(r: dict) -> CopyDraft:
    return CopyDraft(
        id=int(r["id"]), productid=r["productid"], status=r["status"],
        source_card=r["source_card"], images_key=r["images_key"],
        image_refs=r.get("image_refs") or {}, note=r.get("note"),
        attempts=int(r.get("attempts") or 0), error=r.get("error"), draft=r.get("draft"),
        cost_usd=float(r.get("cost_usd") or 0),
    )


def get_active(client: Client, productid: str) -> CopyDraft | None:
    rows = (
        client.table(_TABLE).select(_COLS).eq("productid", productid).eq("active", True)
        .limit(1).execute()
    ).data or []
    return _row(rows[0]) if rows else None


def get_active_many(client: Client, productids: list[str]) -> dict[str, CopyDraft]:
    pids = list(dict.fromkeys(productids))
    if not pids:
        return {}
    rows = (
        client.table(_TABLE).select(_COLS).in_("productid", pids).eq("active", True).execute()
    ).data or []
    return {r["productid"]: _row(r) for r in rows}


def get_by_id(client: Client, draft_id: int) -> CopyDraft | None:
    """The draft if it is still the product's current one."""
    rows = (
        client.table(_TABLE).select(_COLS).eq("id", draft_id).eq("active", True)
        .limit(1).execute()
    ).data or []
    return _row(rows[0]) if rows else None


def create(client: Client, *, productid: str, source_card: str, images_key: str,
           image_refs: dict, note: str | None) -> CopyDraft | None:
    """Queue a draft. If another request just queued one for this product the unique
    index refuses this insert and that row is returned, so only one gets paid for."""
    try:
        rows = client.table(_TABLE).insert({
            "productid": productid, "source_card": source_card, "images_key": images_key,
            "image_refs": image_refs, "note": note,
        }).execute().data or []
    except APIError as exc:
        if exc.code != _UNIQUE_VIOLATION:
            raise
        return get_active(client, productid)
    return _row(rows[0]) if rows else None


def supersede(client: Client, draft_id: int) -> None:
    """Retire a finished draft so a new one can take its place. The row stays: it
    still counts toward the daily spend."""
    (client.table(_TABLE).update({"active": False, "updated_at": "now()"})
     .eq("id", draft_id).eq("active", True).in_("status", [READY, FAILED]).execute())


def requeue_failed(client: Client, draft_id: int, note: str | None) -> bool:
    resp = (
        client.table(_TABLE)
        .update({"status": QUEUED, "attempts": 0, "error": None, "note": note, "updated_at": "now()"})
        .eq("id", draft_id).eq("status", FAILED).execute()
    )
    return bool(resp.data)


def claim_next_queued(client: Client) -> CopyDraft | None:
    """Oldest queued row (queued -> drafting), counting the attempt. If another worker
    wins the row, try the next. None when the queue is empty."""
    while True:
        rows = (
            client.table(_TABLE).select(_COLS).eq("status", QUEUED)
            .order("id", desc=False).limit(1).execute()
        ).data or []
        if not rows:
            return None
        row = _row(rows[0])
        row.attempts += 1
        won = (
            client.table(_TABLE)
            .update({"status": DRAFTING, "attempts": row.attempts, "updated_at": "now()"})
            .eq("id", row.id).eq("status", QUEUED).execute()
        ).data
        if won:
            row.status = DRAFTING
            return row


def _spend(row: CopyDraft, cost_usd: float, input_tokens: int, output_tokens: int) -> dict:
    # Tokens are for this attempt; a failed attempt that was billed still counts.
    if not (input_tokens or output_tokens):
        return {}
    return {"cost_usd": round(row.cost_usd + cost_usd, 4), "input_tokens": input_tokens,
            "output_tokens": output_tokens, "drafted_at": datetime.now(timezone.utc).isoformat()}


def finish(client: Client, row: CopyDraft, draft: dict, *, cost_usd: float,
           input_tokens: int, output_tokens: int) -> None:
    (client.table(_TABLE)
     .update({"status": READY, "draft": draft, "error": None, "updated_at": "now()",
              **_spend(row, cost_usd, input_tokens, output_tokens)})
     .eq("id", row.id).eq("status", DRAFTING).execute())


def fail(client: Client, row: CopyDraft, error: str, *, cost_usd: float = 0.0,
         input_tokens: int = 0, output_tokens: int = 0) -> None:
    (client.table(_TABLE)
     .update({"status": FAILED, "error": error, "updated_at": "now()",
              **_spend(row, cost_usd, input_tokens, output_tokens)})
     .eq("id", row.id).eq("status", DRAFTING).execute())


def spent_last_24h(client: Client) -> float:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    rows = client.table(_TABLE).select("cost_usd").gte("drafted_at", cutoff).execute().data or []
    return sum(float(r.get("cost_usd") or 0) for r in rows)


def reset_orphaned(client: Client) -> int:
    """After a restart no worker is alive, so every drafting row was interrupted."""
    resp = (client.table(_TABLE).update({"status": QUEUED, "updated_at": "now()"})
            .eq("status", DRAFTING).execute())
    return len(resp.data or [])


def reset_stale(client: Client, older_than_seconds: int) -> int:
    """Reclaim rows from a worker that died, while other workers stay live."""
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)).isoformat()
    resp = (client.table(_TABLE).update({"status": QUEUED, "updated_at": "now()"})
            .eq("status", DRAFTING).lt("updated_at", cutoff).execute())
    return len(resp.data or [])
