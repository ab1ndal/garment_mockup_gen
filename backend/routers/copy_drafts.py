"""AI draft endpoints. A screen asks when a reviewer opens a card or chooses an image,
then polls until the draft is ready. Nothing here writes to ``products``."""

from __future__ import annotations

import logging
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException
from supabase import Client

from backend.auth import CurrentUser, get_current_user
from backend.deps import get_db
from backend.routers.backfill import _first_original_ids
from backend.schemas import CopyDraftOut, CopyDraftRequest, ProposedTagOut
from backend.services import copy_drafts as service
from backend.services.copy_drafts import DraftImages, NoImages
from mockup_generator.db import batch_items_repo, copy_drafts_repo as repo
from mockup_generator.describe.draft import MAX_ORIGINALS
from mockup_generator.integrations.drive_client import DriveNotConfigured

router = APIRouter(prefix="/api/drafts", tags=["drafts"])
log = logging.getLogger(__name__)

_UNAVAILABLE = "unavailable"   # migration not applied
_NOT_NEEDED = "not_needed"     # people already wrote this product's text


def _out(row: repo.CopyDraft | None) -> CopyDraftOut:
    if row is None:
        return CopyDraftOut(status=_NOT_NEEDED)
    d = row.draft or {}
    return CopyDraftOut(
        id=row.id, status=row.status, source_card=row.source_card, error=row.error,
        description=d.get("description"), includes=d.get("includes") or "",
        uncertain=d.get("uncertain") or [], conflicts=d.get("conflicts") or [],
        tags=[ProposedTagOut(**t) for t in d.get("tags") or []],
        title_detail=d.get("title_detail") or "", title_detail_note=d.get("title_detail_note") or "",
        colour_mismatch=d.get("colour_mismatch") or "",
    )


def _answer(fn: Callable[[], repo.CopyDraft | None]) -> CopyDraftOut:
    """Without the migration the review screens still work, only with no AI text."""
    try:
        return _out(fn())
    except NoImages as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DriveNotConfigured as exc:
        raise HTTPException(status_code=503, detail="Drive access is not configured on the server") from exc
    except Exception as exc:  # noqa: BLE001 - tell the two apart, then surface it
        if repo.is_table_missing(exc):
            log.warning("copy drafts: table missing; apply docs/migrations/2026-11-03-product-copy-drafts.sql")
            return CopyDraftOut(status=_UNAVAILABLE)
        log.warning("copy draft request failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail=f"Could not start the AI draft: {exc}") from exc


@router.post("/backfill/{file_id}", response_model=CopyDraftOut)
def request_backfill(file_id: str, productid: str, req: CopyDraftRequest,
                     user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    if not (req.use_originals or req.use_mockup):
        raise HTTPException(status_code=400, detail="Pick at least one image source.")

    def images() -> DraftImages:
        return DraftImages(originals=tuple(_first_original_ids(db, productid)) if req.use_originals else (),
                           mockup_drive=file_id if req.use_mockup else None)
    return _answer(lambda: service.request_draft(db, productid, f"backfill:{file_id}", images,
                                                 force=req.force, note=req.note))


@router.post("/batch/{item_id}", response_model=CopyDraftOut)
def request_batch(item_id: int, req: CopyDraftRequest,
                  user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    if not (req.use_originals or req.use_mockup):
        raise HTTPException(status_code=400, detail="Pick at least one image source.")
    card = batch_items_repo.get(db, item_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Card not found.")

    def images() -> DraftImages:
        return DraftImages(originals=tuple(card.image_ids[:MAX_ORIGINALS]) if req.use_originals else (),
                           mockup_storage=card.storage_path if req.use_mockup else None)
    return _answer(lambda: service.request_draft(db, card.productid, f"batch:{item_id}", images,
                                                 force=req.force, note=req.note))


@router.post("/product/{productid}", response_model=CopyDraftOut)
def request_product(productid: str, req: CopyDraftRequest,
                    user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    """For screens whose chosen image is not stored anywhere yet: Drive originals only."""
    return _answer(lambda: service.request_draft(
        db, productid, "product", lambda: DraftImages(originals=tuple(_first_original_ids(db, productid))),
        force=req.force, note=req.note))


@router.get("/{productid}", response_model=CopyDraftOut)
def get_draft(productid: str, user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    return _answer(lambda: repo.get_active(db, productid))
