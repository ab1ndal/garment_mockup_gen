"""Title detail endpoints shared by every screen that publishes a product photo."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from supabase import Client

from backend.auth import CurrentUser, get_current_user
from backend.deps import get_db
from backend.routers.backfill import _first_originals
from backend.schemas import TitleCurrentOut, TitleDraftOut, TitleDraftRequest, TitlePreviewOut
from mockup_generator.db import product_descriptions_repo as descriptions_repo
from mockup_generator.db import product_titles_repo as titles_repo
from mockup_generator.describe.draft import DraftFailed, draft_description
from mockup_generator.describe.title_detail import normalize
from mockup_generator.integrations.drive_client import DriveNotConfigured

router = APIRouter(prefix="/api/titles", tags=["titles"])
log = logging.getLogger(__name__)


@router.get("/{productid}", response_model=TitleCurrentOut)
def get_title(productid: str, user: CurrentUser = Depends(get_current_user),
              db: Client = Depends(get_db)):
    cur = titles_repo.get_current(db, productid)
    if cur is None:
        raise HTTPException(status_code=404, detail="Product not found.")
    return TitleCurrentOut(title_detail=cur.title_detail, display_title=cur.display_title)


@router.get("/{productid}/preview", response_model=TitlePreviewOut)
def preview_title(productid: str, detail: str = "", user: CurrentUser = Depends(get_current_user),
                  db: Client = Depends(get_db)):
    facts = descriptions_repo.get_facts(db, productid)
    if facts is None:
        raise HTTPException(status_code=404, detail="Product not found.")
    p = titles_repo.preview(db, facts, normalize(detail))
    return TitlePreviewOut(title=p.title, duplicate=p.duplicate)


@router.post("/{productid}/draft", response_model=TitleDraftOut)
def draft_title(productid: str, req: TitleDraftRequest,
                user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    """Draft for screens with no description panel. Drive originals only: their
    mockups are not on Drive, and the prompt trusts the originals over a mockup."""
    facts = descriptions_repo.get_facts(db, productid)
    if facts is None:
        raise HTTPException(status_code=404, detail="Product not found.")
    try:
        originals = _first_originals(db, productid)
    except DriveNotConfigured as exc:
        raise HTTPException(status_code=503, detail="Drive access is not configured on the server") from exc
    except Exception as exc:  # noqa: BLE001 - surface any Drive failure as upstream error
        raise HTTPException(status_code=502, detail=f"Could not load the product photos: {exc}") from exc
    if not originals:
        raise HTTPException(status_code=400, detail="No Drive photos to draft from.")
    try:
        d = draft_description(facts, originals, None, req.note)
    except DraftFailed as exc:
        log.warning("title draft failed for %s: %s", productid, exc)
        raise HTTPException(status_code=502, detail=f"Could not draft a title detail: {exc}") from exc
    return TitleDraftOut(title_detail=d.title_detail, title_detail_note=d.title_detail_note,
                         colour_mismatch=d.colour_mismatch)
