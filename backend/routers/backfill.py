"""Backfill review endpoints (DB-backed worklist).

Review state lives in ``backfill_items`` (seeded once from Drive, reconciled via
``/rescan``) — listing a page is a single indexed query, not a Drive scan. Each
action transitions a row optimistically (``transition`` guards on the expected
status so concurrent reviewers can't both win) and then mirrors the change into
Drive by moving the file to the matching reserved folder:

  approve     -> publish to Supabase + move original to published/
  flag        -> base_mockup=false + move to rejected/   (regenerate)
  flag-edit   -> move to edit/ + record a pending backfill_edits row
  skip        -> move to skipped/ (deferred, re-reviewable)
  unskip      -> move back to the worklist root

Drive still holds the bytes: thumbnails are fetched per page and the full-res PNG
is downloaded only when publishing.
"""

from __future__ import annotations

import base64
import logging
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

from fastapi import APIRouter, Depends, HTTPException
from PIL import Image
from supabase import Client

from backend.auth import CurrentUser, get_current_user
from backend.deps import get_db
from backend.schemas import (
    BackfillApproveRequest, BackfillCountsResponse, BackfillEditRequest,
    BackfillFlagRequest, BackfillItem, BackfillItemsResponse,
)
from mockup_generator.config import settings
from mockup_generator.db import (
    backfill_edits_repo, backfill_items_repo as items_repo,
    mockups_repo, products_repo, variants_repo,
)
from mockup_generator.generation import publish, watermark
from mockup_generator.integrations import drive_client
from mockup_generator.integrations.drive_client import DriveNotConfigured
from mockup_generator.services import backfill_sync
from mockup_generator.timing import format_timings, timed

router = APIRouter(prefix="/api/backfill", tags=["backfill"])
log = logging.getLogger(__name__)

_STD_ASPECTS = [("1:1", 1.0), ("4:5", 0.8), ("3:4", 0.75), ("9:16", 0.5625), ("16:9", 1.7778)]

_ALREADY_HANDLED = "This mockup was already handled by another reviewer."


def _suggest_aspect(w: int, h: int) -> str:
    if not h:
        return "1:1"
    ratio = w / h
    return min(_STD_ASPECTS, key=lambda a: abs(a[1] - ratio))[0]


def _claim(db: Client, file_id: str, expect: str, to: str) -> None:
    """Win the row ``expect -> to`` or raise 409 if another reviewer beat us to it."""
    if not items_repo.transition(db, file_id=file_id, expect=expect, to=to):
        raise HTTPException(status_code=409, detail=_ALREADY_HANDLED)


def _move(file_id: str, parent_id: str, where: str) -> str | None:
    """Mirror a transition into Drive. Returns a non-fatal warning on failure (the
    DB row is already authoritative); raises only when Drive isn't configured."""
    try:
        drive_client.move_file(file_id, parent_id)
        return None
    except DriveNotConfigured as exc:
        raise HTTPException(status_code=503, detail="Drive access is not configured on the server") from exc
    except Exception as exc:  # noqa: BLE001 - status already saved; Drive mirror is best-effort
        log.warning("status saved but Drive move to %s failed for %s: %s", where, file_id, exc)
        return f"Saved, but the Drive file could not be moved to {where}/ (will reconcile on rescan)."


@router.get("/items", response_model=BackfillItemsResponse)
def list_items(status: str = items_repo.PENDING, offset: int = 0, limit: int = 20,
               user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    if status not in items_repo.TAB_STATUSES:
        raise HTTPException(status_code=400, detail=f"Unknown status: {status}")
    rows, total = items_repo.page(db, status=status, offset=offset, limit=limit)
    thumbs = drive_client.thumbnails_for(
        [{"file_id": r.file_id, "thumbnail_link": r.thumbnail_link} for r in rows]
    ) if rows else {}

    items = [
        BackfillItem(
            productid=r.productid,
            product_name=r.product_name,
            alpha=r.alpha,
            file_id=r.file_id,
            filename=r.filename,
            thumbnail_url=thumbs.get(r.file_id),
            unknown_product=not r.product_found,
        )
        for r in rows
    ]
    return BackfillItemsResponse(total=total, offset=offset, limit=limit, items=items)


@router.get("/counts", response_model=BackfillCountsResponse)
def counts(user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    return BackfillCountsResponse(counts=items_repo.counts(db))


@router.post("/rescan")
def rescan(user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    """Re-sync ``backfill_items`` from Drive (the only path that scans Drive)."""
    try:
        written = backfill_sync.rescan(db, settings.generated_mockups_folder_id)
    except DriveNotConfigured as exc:
        raise HTTPException(status_code=503, detail="Drive access is not configured on the server") from exc
    return {"status": "ok", "synced": written}


@router.get("/{file_id}/sources")
def card_sources(file_id: str, productid: str | None = None,
                 user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    # Three independent chains, run side by side: each step is a round-trip to
    # the (distant) database or to Drive, so in sequence they added up.
    timings: dict[str, int] = {}

    def timed_call(stage, fn, *args):
        with timed(timings, stage):
            return fn(*args)

    originals = {"loose": [], "groups": []}
    with ThreadPoolExecutor(max_workers=2) as pool:
        download = pool.submit(timed_call, "download", drive_client.download_file, file_id)
        # Unknown products have no variant rows, so this is [] for them as before.
        colors_f = pool.submit(timed_call, "colors", variants_repo.list_colors, db, productid) \
            if productid else None
        product = timed_call("product", products_repo.get_product, db, productid) \
            if productid else None
        if product and getattr(product, "producturl", None):
            fid = drive_client.extract_folder_id(product.producturl)
            if fid:
                try:
                    originals = timed_call("originals", drive_client.list_folder_image_groups, fid)
                except DriveNotConfigured:
                    raise HTTPException(status_code=503, detail="Drive access is not configured on the server")
                except Exception as exc:  # noqa: BLE001 - originals are reference-only
                    log.warning("could not list originals for %s: %s", productid, exc)
        colors = colors_f.result() if colors_f and product else []

        try:
            png = download.result()
        except DriveNotConfigured as exc:
            raise HTTPException(status_code=503, detail="Drive access is not configured on the server") from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Could not load the generated image: {exc}") from exc
    log.info("backfill sources timing file_id=%s %s", file_id, format_timings(timings))

    try:
        w, h = Image.open(BytesIO(png)).size
    except Exception:  # noqa: BLE001
        w, h = 1, 1
    preview = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    return {"originals": originals, "generated_preview": preview,
            "colors": colors, "suggested_aspect": _suggest_aspect(w, h)}


@router.post("/approve")
def approve(req: BackfillApproveRequest,
            user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    timings: dict[str, int] = {}
    # Reserve the row first so a second reviewer can't also publish it.
    with timed(timings, "claim"):
        _claim(db, req.file_id, items_repo.PENDING, items_repo.PUBLISHED)
    try:
        with timed(timings, "download"):
            png = drive_client.download_file(req.file_id)
        if req.remove_watermark:
            with timed(timings, "watermark"):
                png = watermark.remove_corner_star(png)
        with timed(timings, "publish"):
            result = publish.publish_image(
                db, productid=req.productid, png=png, color=req.color,
                theme_name=req.theme_name, aspect_ratio=req.aspect_ratio,
                created_by=user.id, prompt_text=None,
            )
    except DriveNotConfigured as exc:
        items_repo.transition(db, file_id=req.file_id, expect=items_repo.PUBLISHED, to=items_repo.PENDING)
        raise HTTPException(status_code=503, detail="Drive access is not configured on the server") from exc
    except Exception as exc:  # noqa: BLE001 - revert the claim so the card returns for retry
        items_repo.transition(db, file_id=req.file_id, expect=items_repo.PUBLISHED, to=items_repo.PENDING)
        raise HTTPException(status_code=502, detail=f"Could not publish the mockup: {exc}") from exc

    # Archive the original out of the worklist (SA is Editor, can move not delete).
    warning = None
    try:
        with timed(timings, "ensure_archive_folder"):
            archive = drive_client.ensure_subfolder(
                settings.generated_mockups_folder_id, drive_client.ARCHIVE_FOLDER)
        with timed(timings, "move_to_archive"):
            drive_client.move_file(req.file_id, archive)
    except Exception as exc:  # noqa: BLE001 - published already; Drive archive is non-fatal
        log.warning("published %s but Drive archive failed: %s", req.file_id, exc)
        warning = "Published, but the Drive original could not be archived (will reconcile on rescan)."
    log.info("backfill approve timing file_id=%s productid=%s %s",
             req.file_id, req.productid, format_timings(timings))

    return {"status": "ok", "image_url": result["image_url"],
            "variation_id": result["variation_id"], "warning": warning}


@router.post("/flag")
def flag(req: BackfillFlagRequest,
         user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    _claim(db, req.file_id, items_repo.PENDING, items_repo.REGENERATE)
    if req.productid:
        mockups_repo.set_base_mockup(db, req.productid, False)
    rejected = drive_client.ensure_subfolder(
        settings.generated_mockups_folder_id, drive_client.REJECTED_FOLDER)
    warning = _move(req.file_id, rejected, drive_client.REJECTED_FOLDER)
    return {"status": "ok", "warning": warning}


@router.post("/flag-edit")
def flag_edit(req: BackfillEditRequest,
              user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    _claim(db, req.file_id, items_repo.PENDING, items_repo.EDIT)
    edit = drive_client.ensure_subfolder(
        settings.generated_mockups_folder_id, drive_client.EDIT_FOLDER)
    warning = _move(req.file_id, edit, drive_client.EDIT_FOLDER)

    comment = (req.comment or "").strip() or None
    try:
        backfill_edits_repo.insert(
            db, file_id=req.file_id, productid=req.productid,
            comment=comment, created_by=user.id,
        )
    except Exception as exc:  # noqa: BLE001 - moved already; the record is non-fatal
        log.warning("flagged %s for edit but the edit record failed: %s", req.file_id, exc)
        warning = warning or "Flagged for edit, but the edit record could not be saved."
    return {"status": "ok", "warning": warning}


@router.post("/skip")
def skip(req: BackfillFlagRequest,
         user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    """Defer a card to ``skipped/`` — off the worklist but re-reviewable."""
    _claim(db, req.file_id, items_repo.PENDING, items_repo.SKIPPED)
    skipped = drive_client.ensure_subfolder(
        settings.generated_mockups_folder_id, drive_client.SKIPPED_FOLDER)
    warning = _move(req.file_id, skipped, drive_client.SKIPPED_FOLDER)
    return {"status": "ok", "warning": warning}


@router.post("/unskip")
def unskip(req: BackfillFlagRequest,
           user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    """Undo a skip: move back to the worklist root and re-surface as pending."""
    _claim(db, req.file_id, items_repo.SKIPPED, items_repo.PENDING)
    warning = _move(req.file_id, settings.generated_mockups_folder_id, "the worklist")
    return {"status": "ok", "warning": warning}
