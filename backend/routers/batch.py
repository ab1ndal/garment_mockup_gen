"""Batch Generate endpoints (DB-backed worklist + background worker)."""

from __future__ import annotations

import base64
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
from mockup_generator.integrations import drive_client
from mockup_generator.integrations.drive_client import DriveNotConfigured
from mockup_generator.services import batch_enqueue as enqueue
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


def _discard_drive_file(file_id: str) -> str | None:
    """Remove a staged batch file from the staging area. Delete it (the SA owns
    files it uploaded); if deletion isn't permitted (e.g. a Shared Drive role
    without delete rights), fall back to moving it into the ``published/`` archive
    folder so it still leaves ``_batch``. Returns a warning or None."""
    try:
        drive_client.delete_file(file_id)
        return None
    except Exception as exc:  # noqa: BLE001 - fall back to archiving instead
        log.warning("batch staged %s delete failed, moving to %s: %s",
                    file_id, drive_client.ARCHIVE_FOLDER, exc)
    try:
        archive = drive_client.ensure_subfolder(
            settings.generated_mockups_folder_id, drive_client.ARCHIVE_FOLDER)
        drive_client.move_file(file_id, archive)
        return None
    except Exception as exc:  # noqa: BLE001 - neither delete nor archive worked
        log.warning("batch staged %s could not be deleted or archived: %s", file_id, exc)
        return "Done, but the staged Drive file could not be removed."


@router.post("", response_model=BatchEnqueueResponse)
def enqueue_batch(req: BatchEnqueueRequest,
                  user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    batch_id = str(uuid.uuid4())
    rows, skipped = enqueue.plan_cards(
        db, category=req.category, count=req.count,
        model=req.model or settings.gemini_image_model,
        resolution=req.resolution or "4K",
        aspect_ratio=req.aspect_ratio or "1:1",
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
    rows, total = items_repo.page(db, statuses=statuses, offset=offset, limit=limit)
    names = products_repo.names_for(db, [r.productid for r in rows])
    thumb_src = [{"file_id": r.drive_file_id, "thumbnail_link": r.thumbnail_link}
                 for r in rows if r.drive_file_id]
    thumbs = drive_client.thumbnails_for(thumb_src) if thumb_src else {}
    items = [
        BatchItemOut(
            id=r.id, productid=r.productid, product_name=names.get(r.productid),
            color=r.color, status=r.status, image_ids=r.image_ids,
            drive_file_id=r.drive_file_id,
            generated_thumb_url=thumbs.get(r.drive_file_id) if r.drive_file_id else None,
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
    row = items_repo.get(db, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Card not found.")
    colors = variants_repo.list_colors(db, row.productid)
    sources = []
    for fid in row.image_ids:
        try:
            data = drive_client.download_file(fid)
            sources.append({"id": fid, "data_uri": "data:image/*;base64," + base64.b64encode(data).decode()})
        except DriveNotConfigured as exc:
            raise HTTPException(status_code=503, detail="Drive access is not configured on the server") from exc
        except Exception as exc:  # noqa: BLE001 - a missing source shouldn't break the card
            log.warning("batch source %s could not load: %s", fid, exc)
    generated = None
    if row.drive_file_id:
        try:
            g = drive_client.download_file(row.drive_file_id)
            generated = "data:image/png;base64," + base64.b64encode(g).decode()
        except Exception as exc:  # noqa: BLE001
            log.warning("batch generated %s could not load: %s", row.drive_file_id, exc)
    return {"sources": sources, "generated_preview": generated,
            "colors": colors, "color": row.color, "image_ids": row.image_ids}
