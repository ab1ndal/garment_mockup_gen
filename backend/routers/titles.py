"""Title detail endpoints shared by every screen that publishes a product photo."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from supabase import Client

from backend.auth import CurrentUser, get_current_user
from backend.deps import get_db
from backend.schemas import TitleCurrentOut, TitlePreviewOut
from mockup_generator.db import product_descriptions_repo as descriptions_repo
from mockup_generator.db import product_titles_repo as titles_repo
from mockup_generator.describe.title_detail import normalize

router = APIRouter(prefix="/api/titles", tags=["titles"])


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
