"""Backfill review endpoints.

Walk the previously-generated Drive mockups: list paginated review cards
(``items``), load a card's originals + preview (``sources``), then publish
(``approve`` → Supabase + delete the Drive original) or send back for
regeneration (``flag`` → base_mockup=false + move to ``rejected/``).
"""

from __future__ import annotations

import base64
import logging
from io import BytesIO

from fastapi import APIRouter, Depends, HTTPException
from PIL import Image
from supabase import Client

from backend.auth import CurrentUser, get_current_user
from backend.deps import get_db
from backend.schemas import (
    BackfillApproveRequest, BackfillFlagRequest, BackfillItem, BackfillItemsResponse,
)
from mockup_generator.config import settings
from mockup_generator.db import mockups_repo, products_repo, variants_repo
from mockup_generator.generation import publish
from mockup_generator.integrations import drive_client
from mockup_generator.integrations.drive_client import DriveNotConfigured
from mockup_generator.services import backfill_service

router = APIRouter(prefix="/api/backfill", tags=["backfill"])
log = logging.getLogger(__name__)

_STD_ASPECTS = [("1:1", 1.0), ("4:5", 0.8), ("3:4", 0.75), ("9:16", 0.5625), ("16:9", 1.7778)]


def _suggest_aspect(w: int, h: int) -> str:
    if not h:
        return "1:1"
    ratio = w / h
    return min(_STD_ASPECTS, key=lambda a: abs(a[1] - ratio))[0]


@router.get("/items", response_model=BackfillItemsResponse)
def list_items(offset: int = 0, limit: int = 20, refresh: bool = False,
               user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    try:
        index = backfill_service.get_index(settings.generated_mockups_folder_id, refresh=refresh)
    except DriveNotConfigured as exc:
        raise HTTPException(status_code=503, detail="Drive access is not configured on the server") from exc
    page = backfill_service.paginate(index, offset, limit)
    thumbs = drive_client.thumbnails_for(page)

    items: list[BackfillItem] = []
    for it in page:
        pid = it["productid"]
        product = products_repo.get_product(db, pid) if pid else None
        colors = variants_repo.list_colors(db, pid) if product else []
        items.append(BackfillItem(
            productid=pid,
            product_name=getattr(product, "name", None),
            alpha=it["alpha"],
            file_id=it["file_id"],
            filename=it["name"],
            thumbnail_url=thumbs.get(it["file_id"]),
            colors=colors,
            unknown_product=product is None,
        ))
    return BackfillItemsResponse(total=len(index), remaining=len(index), items=items)


@router.get("/{file_id}/sources")
def card_sources(file_id: str, productid: str | None = None,
                 user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    originals = {"loose": [], "groups": []}
    product = products_repo.get_product(db, productid) if productid else None
    if product and getattr(product, "producturl", None):
        fid = drive_client.extract_folder_id(product.producturl)
        if fid:
            try:
                originals = drive_client.list_folder_image_groups(fid)
            except DriveNotConfigured:
                raise HTTPException(status_code=503, detail="Drive access is not configured on the server")
            except Exception as exc:  # noqa: BLE001 - originals are reference-only
                log.warning("could not list originals for %s: %s", productid, exc)

    try:
        png = drive_client.download_file(file_id)
    except DriveNotConfigured as exc:
        raise HTTPException(status_code=503, detail="Drive access is not configured on the server") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not load the generated image: {exc}") from exc

    try:
        w, h = Image.open(BytesIO(png)).size
    except Exception:  # noqa: BLE001
        w, h = 1, 1
    preview = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    return {"originals": originals, "generated_preview": preview,
            "suggested_aspect": _suggest_aspect(w, h)}


@router.post("/approve")
def approve(req: BackfillApproveRequest,
            user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    try:
        png = drive_client.download_file(req.file_id)
    except DriveNotConfigured as exc:
        raise HTTPException(status_code=503, detail="Drive access is not configured on the server") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not load the generated image: {exc}") from exc

    try:
        result = publish.publish_image(
            db, productid=req.productid, png=png, color=req.color,
            theme_name=req.theme_name, aspect_ratio=req.aspect_ratio,
            created_by=user.id, prompt_text=None,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Could not publish the mockup: {exc}") from exc

    warning = None
    try:
        drive_client.delete_file(req.file_id)
    except Exception as exc:  # noqa: BLE001 - published already; Drive cleanup is non-fatal
        log.warning("published %s but Drive delete failed: %s", req.file_id, exc)
        warning = "Published, but the Drive original could not be removed (will reappear on refresh)."
    backfill_service.evict(req.file_id)

    return {"status": "ok", "image_url": result["image_url"],
            "variation_id": result["variation_id"], "warning": warning}


@router.post("/flag")
def flag(req: BackfillFlagRequest,
         user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    if req.productid:
        mockups_repo.set_base_mockup(db, req.productid, False)
    rejected = drive_client.ensure_subfolder(settings.generated_mockups_folder_id, "rejected")
    try:
        drive_client.move_file(req.file_id, rejected)
    except DriveNotConfigured as exc:
        raise HTTPException(status_code=503, detail="Drive access is not configured on the server") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not move the image to rejected/: {exc}") from exc
    backfill_service.evict(req.file_id)
    return {"status": "ok"}
