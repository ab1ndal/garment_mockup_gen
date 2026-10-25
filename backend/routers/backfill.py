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

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from PIL import Image
from supabase import Client

from backend.auth import CurrentUser, get_current_user
from backend.deps import get_db
from backend.schemas import (
    BackfillApproveRequest, BackfillCountsResponse, BackfillDescription,
    BackfillDraftRequest, BackfillDraftResponse, BackfillEditRequest,
    BackfillFlagRequest, BackfillItem, BackfillItemsResponse,
)
from mockup_generator.config import settings
from mockup_generator.db import (
    backfill_edits_repo, backfill_items_repo as items_repo,
    mockups_repo, product_descriptions_repo as descriptions_repo,
    productimages_repo, products_repo,
)
from mockup_generator.describe.draft import (
    MAX_DESCRIPTION_CHARS, MAX_ORIGINALS, DraftFailed, draft_description,
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
_DESCRIPTION_CHANGED = "Image approved, but the description changed since you opened it. Not saved."
_DESCRIPTION_SAVE_FAILED = "Image approved, but the description could not be saved."
_DESCRIPTION_TOO_LONG = (
    f"Image approved, but the description is over {MAX_DESCRIPTION_CHARS} characters. Not saved.")


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
    # Independent chains, run side by side: each step is a round-trip to the
    # (distant) database or to Drive, so in sequence they added up. Colors are
    # not here: the card fetches them from /api/products/{id}/colors so its
    # dropdown doesn't wait on Drive.
    timings: dict[str, int] = {}

    def timed_call(stage, fn, *args):
        with timed(timings, stage):
            return fn(*args)

    originals = {"loose": [], "groups": []}
    with ThreadPoolExecutor(max_workers=1) as pool:
        download = pool.submit(timed_call, "download", drive_client.download_file, file_id)
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
            "suggested_aspect": _suggest_aspect(w, h)}


@router.post("/approve")
def approve(req: BackfillApproveRequest, background: BackgroundTasks,
            user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    timings: dict[str, int] = {}
    # Reserve the row first so a second reviewer can't also publish it.
    with timed(timings, "claim"):
        _claim(db, req.file_id, items_repo.PENDING, items_repo.PUBLISHED)
    try:
        # The display-order lookup (a database round-trip) overlaps the Drive
        # download instead of following it inside publish.
        with ThreadPoolExecutor(max_workers=1) as pool:
            order_f = pool.submit(productimages_repo.next_display_order, db, req.productid)
            with timed(timings, "download"):
                png = drive_client.download_file(req.file_id)
            with timed(timings, "wait_order"):
                order = order_f.result()
        if req.remove_watermark:
            with timed(timings, "watermark"):
                png = watermark.remove_corner_star(png)
        with timed(timings, "publish"):
            result = publish.publish_image(
                db, productid=req.productid, png=png, color=req.color,
                theme_name=req.theme_name, aspect_ratio=req.aspect_ratio,
                created_by=user.id, prompt_text=None, display_order=order,
            )
    except DriveNotConfigured as exc:
        items_repo.transition(db, file_id=req.file_id, expect=items_repo.PUBLISHED, to=items_repo.PENDING)
        raise HTTPException(status_code=503, detail="Drive access is not configured on the server") from exc
    except Exception as exc:  # noqa: BLE001 - revert the claim so the card returns for retry
        items_repo.transition(db, file_id=req.file_id, expect=items_repo.PUBLISHED, to=items_repo.PENDING)
        raise HTTPException(status_code=502, detail=f"Could not publish the mockup: {exc}") from exc

    description_warning = _save_description(db, req) if req.description is not None else None

    # Archiving the Drive original takes ~1.8s and the reviewer doesn't need to
    # wait for it: the row is already `published`, and a failed move is
    # reconciled by the next rescan. So it runs after the response is sent, and a
    # failure is logged rather than shown.
    background.add_task(_archive_original, req.file_id)
    log.info("backfill approve timing file_id=%s productid=%s %s",
             req.file_id, req.productid, format_timings(timings))

    return {"status": "ok", "image_url": result["image_url"],
            "variation_id": result["variation_id"], "warning": None,
            "description_warning": description_warning}


def _save_description(db: Client, req: BackfillApproveRequest) -> str | None:
    """Save the reviewer's draft once the image is published. Never raises: the
    image approval already happened, so a failure is returned as a warning."""
    text = req.description.strip()
    if len(text) > MAX_DESCRIPTION_CHARS:
        return _DESCRIPTION_TOO_LONG
    try:
        saved = descriptions_repo.save_reviewed(
            db, req.productid, text,
            expected_description=req.expected_description,
            expected_reviewed_at=req.expected_reviewed_at)
    except Exception as exc:  # noqa: BLE001 - image is published; report, don't fail
        log.warning("description save failed for %s after approving %s: %s",
                    req.productid, req.file_id, exc)
        return _DESCRIPTION_SAVE_FAILED
    if not saved:
        log.info("description for %s changed since load; draft not saved", req.productid)
        return _DESCRIPTION_CHANGED
    return None


@router.get("/description/{productid}", response_model=BackfillDescription)
def get_description(productid: str, user: CurrentUser = Depends(get_current_user),
                    db: Client = Depends(get_db)):
    cur = descriptions_repo.get_current(db, productid)
    if cur is None:
        raise HTTPException(status_code=404, detail="Product not found.")
    return BackfillDescription(description=cur.description, description_source=cur.source,
                               description_reviewed_at=cur.reviewed_at)


def _first_originals(db: Client, productid: str) -> list[bytes]:
    """Up to MAX_ORIGINALS Drive photos of the product: loose images first, then
    variant subfolders, in the folder's natural order. Downloaded one at a time."""
    product = products_repo.get_product(db, productid)
    fid = drive_client.extract_folder_id(getattr(product, "producturl", None))
    if not fid:
        return []
    groups = drive_client.list_folder_image_groups(fid)
    images = groups["loose"] + [i for g in groups["groups"] for i in g["images"]]
    return [drive_client.download_file(i["id"]) for i in images[:MAX_ORIGINALS]]


@router.post("/{file_id}/description/draft", response_model=BackfillDraftResponse)
def draft_description_route(file_id: str, req: BackfillDraftRequest,
                            user: CurrentUser = Depends(get_current_user),
                            db: Client = Depends(get_db)):
    if not (req.use_originals or req.use_mockup):
        raise HTTPException(status_code=400, detail="Pick at least one image source.")
    facts = descriptions_repo.get_facts(db, req.productid)
    if facts is None:
        raise HTTPException(status_code=404, detail="Product not found.")
    try:
        originals = _first_originals(db, req.productid) if req.use_originals else []
        mockup = drive_client.download_file(file_id) if req.use_mockup else None
    except DriveNotConfigured as exc:
        raise HTTPException(status_code=503, detail="Drive access is not configured on the server") from exc
    except Exception as exc:  # noqa: BLE001 - surface any Drive failure as upstream error
        raise HTTPException(status_code=502, detail=f"Could not load the product photos: {exc}") from exc
    if not originals and mockup is None:
        raise HTTPException(status_code=400, detail="No images to draft from.")
    try:
        d = draft_description(facts, originals, mockup, req.note)
    except DraftFailed as exc:
        log.warning("description draft failed for %s (%s): %s", req.productid, file_id, exc)
        raise HTTPException(status_code=502, detail=f"Could not draft a description: {exc}") from exc
    return BackfillDraftResponse(description=d.description, includes=d.includes,
                                 uncertain=d.uncertain, conflicts=d.conflicts)


def _archive_original(file_id: str) -> None:
    """Move a published original out of the worklist (SA is Editor: can move,
    not delete). Runs as a background task, so it must not raise."""
    try:
        archive = drive_client.ensure_subfolder(
            settings.generated_mockups_folder_id, drive_client.ARCHIVE_FOLDER)
        drive_client.move_file(file_id, archive)
    except Exception as exc:  # noqa: BLE001 - published already; rescan reconciles
        log.warning("published %s but Drive archive failed (rescan will reconcile): %s",
                    file_id, exc)


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
