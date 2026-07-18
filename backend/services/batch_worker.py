"""Batch Generate background worker.

A pool of worker threads drains ``queued`` rows: claim (queued -> generating),
download the product's source images, generate one mockup, stage the PNG in the
private ``mockups-temp`` bucket, and mark the row ``ready``. A transient failure
(429 quota, 5xx, or an empty NO_IMAGE response) is requeued for another pass up
to ``_MAX_ATTEMPTS``; a permanent one, or a card past the cap, is marked
``failed`` with the error. Requeued cards sort behind fresh ones, so the rate
limit gets a cooldown instead of the same card hammering it.

Resumable: a crash leaves rows ``generating``. ``reset_orphaned`` (startup)
returns them all to ``queued``; ``reset_stale_generating`` (every enqueue) does
the same for rows aged past ``_STALE_SECONDS`` while other drainers stay live.

Staging is Supabase Storage, not Drive: a service account has no storage quota,
so creating a file in a My Drive folder fails with 403 storageQuotaExceeded.
"""

from __future__ import annotations

import logging
import threading
import time
from io import BytesIO

from google.genai import errors as genai_errors
from PIL import Image
from supabase import Client

from mockup_generator.config import settings
from mockup_generator.db import batch_items_repo as repo
from mockup_generator.generation import service
from mockup_generator.generation.service import NoImageReturned
from mockup_generator.integrations import drive_client, storage_client

log = logging.getLogger(__name__)

_DEFAULT_CONCURRENCY = 3

# How many full generation passes a card gets before it is marked failed for good.
# Each pass already retries 429/5xx internally (see generate_with_retries), so this
# is the outer bound on requeues for a card whose whole pass keeps hitting the wall.
_MAX_ATTEMPTS = 4

# A row still 'generating' after this long can only be a drainer that died without
# reaching a terminal transition — no healthy pass runs this long (8 internal
# retries with backoff capped at 60s tops out well under it). Reclaimed to queued.
_STALE_SECONDS = 1200  # 20 min

_lock = threading.Lock()
_active: dict[str, int] = {}  # model -> live drainers; guarded by _lock


def _is_transient(exc: Exception) -> bool:
    """A failure worth another full generation pass: quota exhaustion (429), a 5xx,
    or an empty NO_IMAGE response (flash occasionally returns no image part on an
    otherwise-fine prompt). Everything else — a bad request, missing source images,
    a genuinely refused prompt — is permanent and must not be retried, or it just
    burns the same generation again."""
    if isinstance(exc, NoImageReturned):
        return True
    if isinstance(exc, genai_errors.ServerError):
        return True
    if isinstance(exc, genai_errors.ClientError):
        return getattr(exc, "code", None) == 429
    return False


def _model_budgets() -> dict[str, int]:
    """model -> number of dedicated drainers. Generation is a slow network call,
    so threads (not processes) are the right shape even on a small box.

    Each base model has its OWN shared-capacity pool on Vertex, so dedicating
    threads per model fans the batch out across independent pools instead of
    piling every worker onto one throttled pool (where extra workers only earn
    429s and backoff). Configured with ``BATCH_MODEL_CONCURRENCY``
    (``"model=n,model=n"``); unset falls back to the single ``GEMINI_IMAGE_MODEL``
    at ``BATCH_CONCURRENCY`` — the original single-pool behaviour.
    """
    raw = settings.batch_model_concurrency
    if raw:
        budgets: dict[str, int] = {}
        for part in raw.split(","):
            model, _, num = part.strip().partition("=")
            model = model.strip()
            try:
                n = max(0, int(num))
            except (TypeError, ValueError):
                log.warning("BATCH_MODEL_CONCURRENCY: bad entry %r; skipping", part)
                continue
            if model and n:
                budgets[model] = n
        if budgets:
            return budgets
        log.warning("BATCH_MODEL_CONCURRENCY=%r parsed empty; using single-model", raw)
    try:
        n = max(1, int(settings.batch_concurrency))
    except (TypeError, ValueError):
        log.warning("BATCH_CONCURRENCY=%r is not an int; using %d", settings.batch_concurrency, _DEFAULT_CONCURRENCY)
        n = _DEFAULT_CONCURRENCY
    return {settings.gemini_image_model: n}


def _spawn(fn, *args) -> None:
    """Run ``fn`` off the request thread. Overridden in tests to run inline."""
    threading.Thread(target=fn, args=args, daemon=True).start()


def run_one(db: Client, model: str) -> bool:
    """Claim and process one queued card for ``model``'s pool, stamping that model
    on the card (overriding whatever was stamped at enqueue) so the batch fans out
    across independent per-model capacity pools. False when nothing is queued."""
    row = repo.claim_next_queued(db, assign_model=model)
    if row is None:
        return False
    attempt = row.attempts + 1
    started = time.perf_counter()
    log.info("batch item %s (%s) generating: attempt %d/%d",
             row.id, row.productid, attempt, _MAX_ATTEMPTS)
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
                        storage_path=path, attempts=attempt, error=None)
        log.info("batch item %s (%s) ready in %.1fs (attempt %d)",
                 row.id, row.productid, time.perf_counter() - started, attempt)
    except Exception as exc:  # noqa: BLE001 - record the failure on the card and continue
        _record_failure(db, row, attempt, exc, time.perf_counter() - started)
    return True


def _record_failure(db: Client, row: repo.BatchRow, attempt: int,
                    exc: Exception, elapsed: float) -> None:
    """Requeue a transient failure for another pass until the attempt cap, then
    give up and mark it failed. Permanent failures fail on the first pass."""
    if _is_transient(exc) and attempt < _MAX_ATTEMPTS:
        log.warning(
            "batch item %s (%s) transient failure on attempt %d/%d after %.1fs, "
            "requeueing: %s", row.id, row.productid, attempt, _MAX_ATTEMPTS, elapsed, exc)
        repo.transition(db, item_id=row.id, expect=repo.GENERATING, to=repo.QUEUED,
                        attempts=attempt, error=str(exc))
        return
    reason = "attempt cap reached" if _is_transient(exc) else "permanent error"
    log.warning(
        "batch item %s (%s) failed (%s) on attempt %d/%d after %.1fs: %s",
        row.id, row.productid, reason, attempt, _MAX_ATTEMPTS, elapsed, exc)
    repo.transition(db, item_id=row.id, expect=repo.GENERATING, to=repo.FAILED,
                    attempts=attempt, error=str(exc))


def run_worker(db: Client, model: str) -> None:
    """Drain the queue until it runs dry, then release this drainer's slot.

    Concurrency-safe by construction: every card is taken via
    ``claim_next_queued``, a conditional ``queued -> generating`` update, so two
    drainers racing the same row means one wins and the other moves on.

    Its slot is accounted for by ``ensure_running``, which reserves before
    spawning; call it through ``ensure_running``, never directly, or the pool
    count will drift.
    """
    try:
        while run_one(db, model):
            pass
    finally:
        with _lock:
            _active[model] = max(0, _active.get(model, 1) - 1)


def ensure_running(db: Client) -> None:
    """Top the drainer pool back up to ``_concurrency()``.

    Slots are reserved under the lock *before* spawning, so concurrent callers
    (two enqueues landing together) can't both see an empty pool and stack two.

    Also reclaims stale ``generating`` rows first: a drainer that died mid-card
    would otherwise strand its card until the next restart. Called on every
    enqueue/edit/retry, so recovery no longer waits on a reboot.
    """
    try:
        stale = repo.reset_stale_generating(db, _STALE_SECONDS)
        if stale:
            log.info("batch: reclaimed %d stale 'generating' row(s) to 'queued'", stale)
    except Exception as exc:  # noqa: BLE001 - reclaim is best-effort; still start the pool
        log.warning("batch: stale-generating reclaim skipped: %s", exc)
    budgets = _model_budgets()
    to_spawn: list[str] = []
    with _lock:
        for model, budget in budgets.items():
            want = budget - _active.get(model, 0)
            if want > 0:
                _active[model] = _active.get(model, 0) + want
                to_spawn.extend([model] * want)
    for model in to_spawn:
        _spawn(run_worker, db, model)


def reset_orphaned(db: Client) -> int:
    return repo.reset_orphaned_generating(db)
