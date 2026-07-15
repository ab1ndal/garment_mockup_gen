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

from mockup_generator.db import batch_items_repo as repo
from mockup_generator.generation import service
from mockup_generator.integrations import drive_client, storage_client

log = logging.getLogger(__name__)

_lock = threading.Lock()
_running = False


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
    """Drain the queue. Single-instance: a second call returns immediately."""
    global _running
    with _lock:
        if _running:
            return
        _running = True
    try:
        while run_one(db):
            pass
    finally:
        with _lock:
            _running = False


def ensure_running(db: Client) -> None:
    """Start the worker off-thread if it isn't already draining."""
    with _lock:
        if _running:
            return
    _spawn(run_worker, db)


def reset_orphaned(db: Client) -> int:
    return repo.reset_orphaned_generating(db)
