"""Background worker that turns queued ``product_copy_drafts`` rows into AI text.

Failure modes it is built around:

- Paying twice: a row is claimed by a conditional update, so one worker runs it.
- Restart mid-queue: ``reset_orphaned`` returns interrupted rows to the queue at boot.
  An interrupted call may already have been billed, so a row gets ``_MAX_ATTEMPTS``
  claims and then fails with a Retry instead of looping.
- Rate limits: the Anthropic client retries 429 and 5xx with backoff (see
  ``draft._client``). When that runs out the row fails plainly. It is not requeued,
  because a person can press Retry and a loop could spend without anyone looking.
- Runaway spend: ``_DAILY_LIMIT`` refuses new calls once the rolling 24 hours reach
  the cap. Up to ``draft_concurrency`` calls already running can pass it.

It writes only to ``product_copy_drafts``. ``products`` changes only on Approve.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import asdict

from supabase import Client

from mockup_generator.config import settings
from mockup_generator.db import copy_drafts_repo as repo
from mockup_generator.db import product_descriptions_repo as descriptions_repo
from mockup_generator.db import product_titles_repo as titles_repo
from mockup_generator.describe.draft import DraftFailed, draft_cost_usd, draft_description
from mockup_generator.integrations import drive_client, storage_client

log = logging.getLogger(__name__)

_MAX_ATTEMPTS = 2
# Longer than the worst healthy call: a 90s timeout, tried 5 times, plus backoff.
_STALE_SECONDS = 900
_DAILY_LIMIT = "Today's AI limit is used up. Type the text yourself, or press Retry tomorrow."
_INTERRUPTED = "The AI draft was interrupted twice. Press Retry."

_lock = threading.Lock()
_active = 0  # live drainers; guarded by _lock


def _spawn(fn, *args) -> None:
    """Run ``fn`` off the request thread. Overridden in tests to run inline."""
    threading.Thread(target=fn, args=args, daemon=True).start()


def _load_images(row: repo.CopyDraft) -> tuple[list[bytes], bytes | None]:
    refs = row.image_refs
    originals = [drive_client.download_file(fid) for fid in refs.get("originals") or []]
    mockup = None
    try:
        if refs.get("mockup_drive"):
            mockup = drive_client.download_file(refs["mockup_drive"])
        elif refs.get("mockup_storage"):
            mockup = storage_client.download_mockup(refs["mockup_storage"], bucket=storage_client.TEMP_BUCKET)
    except Exception as exc:  # noqa: BLE001 - a staged mockup goes when its card is accepted
        if not originals:
            raise
        log.info("copy draft %s (%s): drafting without the mockup: %s", row.id, row.productid, exc)
    return originals, mockup


def run_one(db: Client) -> bool:
    """Claim and draft one queued row. False when the queue is empty."""
    row = repo.claim_next_queued(db)
    if row is None:
        return False
    if row.attempts > _MAX_ATTEMPTS:
        repo.fail(db, row, _INTERRUPTED)
        return True
    try:
        if repo.spent_last_24h(db) >= settings.draft_daily_usd_cap:
            log.warning("copy draft %s (%s): daily cap reached", row.id, row.productid)
            repo.fail(db, row, _DAILY_LIMIT)
            return True
        facts = descriptions_repo.get_facts(db, row.productid)
        if facts is None:
            repo.fail(db, row, "Product not found.")
            return True
        originals, mockup = _load_images(row)
        d = draft_description(facts, originals, mockup, row.note)
    except DraftFailed as exc:
        log.warning("copy draft %s (%s) failed: %s", row.id, row.productid, exc)
        repo.fail(db, row, str(exc), cost_usd=draft_cost_usd(settings.description_model, exc.input_tokens, exc.output_tokens),
                  input_tokens=exc.input_tokens, output_tokens=exc.output_tokens)
        return True
    except Exception as exc:  # noqa: BLE001 - record on the row; the reviewer sees it with Retry
        log.warning("copy draft %s (%s) failed: %s", row.id, row.productid, exc, exc_info=True)
        repo.fail(db, row, f"Could not load the product photos: {exc}")
        return True

    cost = draft_cost_usd(settings.description_model, d.input_tokens, d.output_tokens)
    preview_title, duplicate = None, False
    try:
        p = titles_repo.preview(db, facts, d.title_detail)
        preview_title, duplicate = p.title, p.duplicate
    except Exception as exc:  # noqa: BLE001 - the card can do without it; the money is spent, keep the draft
        log.warning("copy draft %s (%s): title preview failed: %s", row.id, row.productid, exc)
    repo.finish(db, row, {
        "description": d.description, "includes": d.includes, "uncertain": d.uncertain,
        "conflicts": d.conflicts, "tags": [asdict(t) for t in d.tags],
        "title_detail": d.title_detail, "title_detail_note": d.title_detail_note,
        "colour_mismatch": d.colour_mismatch,
        "preview_title": preview_title, "duplicate": duplicate,
    }, cost_usd=cost, input_tokens=d.input_tokens, output_tokens=d.output_tokens)
    log.info("copy draft %s (%s) ready: %d in, %d out, $%.4f", row.id, row.productid,
             d.input_tokens, d.output_tokens, cost)
    return True


def _drain(db: Client) -> None:
    global _active
    try:
        while run_one(db):
            pass
    except Exception as exc:  # noqa: BLE001 - a daemon thread has nobody to raise to
        log.warning("copy draft worker stopped: %s", exc, exc_info=True)
    finally:
        with _lock:
            _active = max(0, _active - 1)


def ensure_running(db: Client) -> None:
    """Top the pool up to ``draft_concurrency``. Slots are reserved under the lock
    before spawning, so two requests landing together cannot stack extra workers."""
    global _active
    try:
        stale = repo.reset_stale(db, _STALE_SECONDS)
        if stale:
            log.info("copy drafts: reclaimed %d stale row(s)", stale)
    except Exception as exc:  # noqa: BLE001 - reclaim is best-effort; still start the pool
        log.warning("copy drafts: stale reclaim skipped: %s", exc)
    with _lock:
        want = max(0, settings.draft_concurrency - _active)
        _active += want
    for _ in range(want):
        _spawn(_drain, db)


def reset_orphaned(db: Client) -> int:
    return repo.reset_orphaned(db)
