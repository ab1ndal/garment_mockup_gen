"""Batch Generate endpoints (DB-backed worklist + background worker)."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from supabase import Client

from backend.auth import CurrentUser, get_current_user
from backend.deps import get_db
from backend.schemas import (
    BatchAcceptRequest, BatchActionResponse, BatchCountsResponse, BatchEditRequest,
    BatchEnqueueRequest, BatchEnqueueResponse, BatchItemOut, BatchItemsResponse,
)
from mockup_generator.config import settings
from mockup_generator.db import batch_items_repo as items_repo
from mockup_generator.db import products_repo, variants_repo
from mockup_generator.generation import publish
from mockup_generator.integrations import drive_client, storage_client
from mockup_generator.integrations.drive_client import DriveNotConfigured
from mockup_generator.services import batch_enqueue as enqueue
# Batch offers the same generation options as single-image generation, and must
# default them the same way — a null resolution here used to mean 4K while the
# portal's own default was 2K, so batches silently ran at print quality.
from backend.routers.generate import (
    ALLOWED_ASPECTS, ALLOWED_MODELS, ALLOWED_RESOLUTIONS, _DEFAULTS as GEN_DEFAULTS,
)
from backend.services import batch_worker as worker

router = APIRouter(prefix="/api/batch", tags=["batch"])
log = logging.getLogger(__name__)

_ALREADY_HANDLED = "This card was already handled."

# tab name -> statuses queried for that sub-tab
_TABS: dict[str, list[str]] = {
    "ready": [items_repo.READY],
    "in_progress": [items_repo.QUEUED, items_repo.GENERATING],
    "failed": [items_repo.FAILED],
    "history": [items_repo.PUBLISHED, items_repo.REJECTED],
}


def _claim(db: Client, item_id: int, expect: str, to: str, **fields) -> None:
    if not items_repo.transition(db, item_id=item_id, expect=expect, to=to, **fields):
        raise HTTPException(status_code=409, detail=_ALREADY_HANDLED)


def _staged_url(storage_path: str | None) -> str | None:
    """Signed URL for a staged mockup, or None. The temp bucket is private, so the
    link is minted per read and expires; a signing failure must not fail the whole
    page, so the card just renders without its thumbnail.

    Handled cards have no staged file left — accept and reject both delete it and
    null the path — so History rows resolve to None here rather than signing a
    URL for an object that is gone.
    """
    if not storage_path:
        return None
    try:
        return storage_client.signed_url(storage_path, bucket=storage_client.TEMP_BUCKET)
    except Exception as exc:  # noqa: BLE001 - one missing thumb shouldn't 500 the list
        log.warning("batch staged %s could not be signed: %s", storage_path, exc)
        return None


def _discard_staged(storage_path: str) -> str | None:
    """Remove a staged mockup from the private temp bucket. Returns a warning or
    None. We own the bucket outright, so there is no permission fallback to make —
    a failure here only leaves an orphaned object, never a wrong review outcome."""
    try:
        storage_client.delete_object(storage_path, bucket=storage_client.TEMP_BUCKET)
        return None
    except Exception as exc:  # noqa: BLE001 - the card's outcome already stands
        log.warning("batch staged %s could not be removed: %s", storage_path, exc)
        return "Done, but the staged file could not be removed from temp storage."


@router.post("", response_model=BatchEnqueueResponse)
def enqueue_batch(req: BatchEnqueueRequest,
                  user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    # The generation options are chosen per batch and stamped onto every card, so
    # an unsupported value would only surface much later, as N failed cards.
    if req.model is not None and req.model not in ALLOWED_MODELS:
        raise HTTPException(status_code=400, detail=f"Unsupported model: {req.model}")
    if req.resolution is not None and req.resolution not in ALLOWED_RESOLUTIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported resolution: {req.resolution}")
    if req.aspect_ratio is not None and req.aspect_ratio not in ALLOWED_ASPECTS:
        raise HTTPException(status_code=400, detail=f"Unsupported aspect ratio: {req.aspect_ratio}")
    batch_id = str(uuid.uuid4())
    rows, skipped = enqueue.plan_cards(
        db, category=req.category, count=req.count,
        model=req.model or settings.gemini_image_model,
        resolution=req.resolution or GEN_DEFAULTS["resolution"],
        aspect_ratio=req.aspect_ratio or GEN_DEFAULTS["aspect_ratio"],
        batch_id=batch_id, created_by=user.id,
    )
    items_repo.insert_many(db, rows)
    if rows:
        worker.ensure_running(db)
    return BatchEnqueueResponse(batch_id=batch_id, queued=len(rows), skipped=skipped)


@router.get("/items", response_model=BatchItemsResponse)
def list_items(tab: str = "ready", offset: int = 0, limit: int = 20,
               user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    statuses = _TABS.get(tab)
    if statuses is None:
        raise HTTPException(status_code=400, detail=f"Unknown tab: {tab}")
    rows, total = items_repo.page(
        db, statuses=statuses, offset=offset, limit=limit,
        sort_by_product=(tab == "ready"),
    )
    names = products_repo.names_for(db, [r.productid for r in rows])
    items = [
        BatchItemOut(
            id=r.id, productid=r.productid, product_name=names.get(r.productid),
            color=r.color, status=r.status, image_ids=r.image_ids,
            storage_path=r.storage_path,
            generated_thumb_url=_staged_url(r.storage_path),
            error=r.error,
        )
        for r in rows
    ]
    return BatchItemsResponse(total=total, offset=offset, limit=limit, items=items)


@router.get("/counts", response_model=BatchCountsResponse)
def counts(user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    return BatchCountsResponse(counts=items_repo.counts(db))


@router.get("/{item_id}/sources")
def card_sources(item_id: int,
                 user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    """Everything the review card needs beyond what the list already returned.

    Thumbnails only: at ~w600 each, fetched in parallel, this opens in a fraction
    of the time the full-resolution originals took, and picking sources needs no
    more detail than that. The lightbox fetches the large image lazily via
    ``/api/drive/image/{id}`` when an image is actually opened, and the generated
    mockup already has a URL on the card — neither is worth inlining here.
    """
    row = items_repo.get(db, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Card not found.")
    colors = variants_repo.list_colors(db, row.productid)
    try:
        sources = [
            {"id": t["id"], "thumb_url": t["thumbnail_url"]}
            for t in drive_client.thumbnails_for_ids(row.image_ids)
        ]
    except DriveNotConfigured as exc:
        raise HTTPException(status_code=503, detail="Drive access is not configured on the server") from exc
    return {"sources": sources, "colors": colors, "color": row.color, "image_ids": row.image_ids}


@router.post("/{item_id}/accept", response_model=BatchActionResponse)
def accept(item_id: int, req: BatchAcceptRequest,
           user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    row = items_repo.get(db, item_id)
    if row is None or not row.storage_path:
        raise HTTPException(status_code=404, detail="Card not found or not ready.")
    # Reserve the row so a second reviewer can't also publish it.
    _claim(db, item_id, items_repo.READY, items_repo.PUBLISHED)
    try:
        png = storage_client.download_mockup(row.storage_path, bucket=storage_client.TEMP_BUCKET)
        result = publish.publish_image(
            db, productid=row.productid, png=png,
            color=req.color if req.color is not None else row.color,
            theme_name=req.theme_name,
            aspect_ratio=req.aspect_ratio or row.aspect_ratio,
            created_by=user.id, prompt_text=row.prompt_text,
        )
    except Exception as exc:  # noqa: BLE001 - revert the claim so the card returns for retry
        items_repo.transition(db, item_id=item_id, expect=items_repo.PUBLISHED, to=items_repo.READY)
        raise HTTPException(status_code=502, detail=f"Could not publish the mockup: {exc}") from exc

    warning = _discard_staged(row.storage_path)
    # The staged object is gone; drop the pointer to it so nothing tries to read
    # it back.
    items_repo.transition(db, item_id=item_id, expect=items_repo.PUBLISHED,
                          to=items_repo.PUBLISHED, storage_path=None)
    log.info("batch %s published as %s", item_id, result["image_url"])
    return BatchActionResponse(status="ok", warning=warning)


@router.post("/{item_id}/reject", response_model=BatchActionResponse)
def reject(item_id: int,
           user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    row = items_repo.get(db, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Card not found.")
    _claim(db, item_id, items_repo.READY, items_repo.REJECTED)
    warning = None
    if row.storage_path:
        warning = _discard_staged(row.storage_path)
        items_repo.transition(db, item_id=item_id, expect=items_repo.REJECTED,
                              to=items_repo.REJECTED, storage_path=None)
    return BatchActionResponse(status="ok", warning=warning)


@router.post("/{item_id}/edit", response_model=BatchActionResponse)
def edit(item_id: int, req: BatchEditRequest,
         user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    row = items_repo.get(db, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Card not found.")
    note = (req.prompt_note or "").strip()
    prompt_text = f"{row.prompt_text}\n\nRevision note: {note}" if note else row.prompt_text
    image_ids = req.image_ids if req.image_ids else row.image_ids
    # ready -> queued with the updated prompt/images; clear the stale staged file id.
    _claim(db, item_id, items_repo.READY, items_repo.QUEUED,
           prompt_text=prompt_text, image_ids=image_ids, storage_path=None, error=None)
    warning = _discard_staged(row.storage_path) if row.storage_path else None
    worker.ensure_running(db)
    return BatchActionResponse(status="ok", warning=warning)


@router.post("/{item_id}/retry", response_model=BatchActionResponse)
def retry(item_id: int,
          user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    _claim(db, item_id, items_repo.FAILED, items_repo.QUEUED, error=None)
    worker.ensure_running(db)
    return BatchActionResponse(status="ok", warning=None)
