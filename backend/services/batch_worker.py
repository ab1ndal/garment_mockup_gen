"""Batch Generate background worker.

A single worker thread drains ``queued`` rows one at a time: claim (queued ->
generating), download the product's source images, generate one mockup, stage
the PNG in the private ``mockups-temp`` bucket, and mark the row ``ready`` (or
``failed`` with the error). Sequential by design — the generator already retries
rate limits. Resumable: a crash leaves rows ``generating``; ``reset_orphaned``
(called at startup) returns them to ``queued`` for the next sweep.

Staging is Supabase Storage, not Drive: a service account has no storage quota,
so creating a file in a My Drive folder fails with 403 storageQuotaExceeded.
"""

from __future__ import annotations

import logging
import threading
from io import BytesIO

from PIL import Image
from supabase import Client

from mockup_generator.config import settings
from mockup_generator.db import batch_items_repo as repo
from mockup_generator.generation import service
from mockup_generator.integrations import drive_client, storage_client

log = logging.getLogger(__name__)

_DEFAULT_CONCURRENCY = 3

_lock = threading.Lock()
_active = 0  # live drainers; guarded by _lock


def _concurrency() -> int:
    """How many cards to generate at once. Generation is a slow network call, so
    threads (not processes) are the right shape even on a small box.

    The real ceiling is the image model's rate limit, not the CPU: too many
    drainers turn into 429s, which the generator answers with backoff — spending
    wall-clock rather than saving it. Tune with ``BATCH_CONCURRENCY``.
    """
    raw = settings.batch_concurrency
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        log.warning("BATCH_CONCURRENCY=%r is not an int; using %d", raw, _DEFAULT_CONCURRENCY)
        return _DEFAULT_CONCURRENCY


def _spawn(fn, *args) -> None:
    """Run ``fn`` off the request thread. Overridden in tests to run inline."""
    threading.Thread(target=fn, args=args, daemon=True).start()


def run_one(db: Client) -> bool:
    """Claim and process one queued card. Returns False when nothing is queued."""
    row = repo.claim_next_queued(db)
    if row is None:
        return False
    try:
        images = [Image.open(BytesIO(drive_client.download_file(fid))) for fid in row.image_ids]
        png = service.generate_mockup_bytes(
            images, row.prompt_text, model=row.model,
            resolution=row.resolution, aspect_ratio=row.aspect_ratio,
        )
        # Keyed by item id, so a re-generated card (edit -> queued -> ready)
        # overwrites its own staged object instead of orphaning one.
        path, _ = storage_client.upload_mockup(
            row.productid, png, f"batch-{row.id}", bucket=storage_client.TEMP_BUCKET)
        repo.transition(db, item_id=row.id, expect=repo.GENERATING, to=repo.READY,
                        storage_path=path, error=None)
    except Exception as exc:  # noqa: BLE001 - record the failure on the card and continue
        log.warning("batch item %s generation failed: %s", row.id, exc)
        repo.transition(db, item_id=row.id, expect=repo.GENERATING, to=repo.FAILED,
                        error=str(exc))
    return True


def run_worker(db: Client) -> None:
    """Drain the queue until it runs dry, then release this drainer's slot.

    Concurrency-safe by construction: every card is taken via
    ``claim_next_queued``, a conditional ``queued -> generating`` update, so two
    drainers racing the same row means one wins and the other moves on.

    Its slot is accounted for by ``ensure_running``, which reserves before
    spawning; call it through ``ensure_running``, never directly, or the pool
    count will drift.
    """
    global _active
    try:
        while run_one(db):
            pass
    finally:
        with _lock:
            _active -= 1


def ensure_running(db: Client) -> None:
    """Top the drainer pool back up to ``_concurrency()``.

    Slots are reserved under the lock *before* spawning, so concurrent callers
    (two enqueues landing together) can't both see an empty pool and stack two.
    """
    global _active
    with _lock:
        want = _concurrency() - _active
        if want <= 0:
            return
        _active += want
    for _ in range(want):
        _spawn(run_worker, db)


def reset_orphaned(db: Client) -> int:
    return repo.reset_orphaned_generating(db)
