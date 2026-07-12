"""No-Gemini import path: edit a product's Drive photos and publish them WEBP-only.

Stateless and product-scoped. Publishes into the reserved 20+ display band as
"Product Shot" rows (productimages only). See
docs/superpowers/specs/2026-07-12-drive-product-shot-import-design.md.
"""

from __future__ import annotations

import base64
import logging
import threading
from collections import OrderedDict
from io import BytesIO

from fastapi import APIRouter, Depends, HTTPException
from PIL import Image
from supabase import Client

from backend.auth import CurrentUser, get_current_user
from backend.deps import get_db
from backend.schemas import (
    CreatePresetRequest, ImportDriveImagesResponse, ImportPublishRequest,
    ImportPublishResponse, PresetModel, PresetsResponse, PreviewRequest,
    PreviewResponse, ReleaseRequest, WarmRequest,
)
from mockup_generator.db import edit_presets_repo, productimages_repo, products_repo
from mockup_generator.generation import edit_pipeline, publish
from mockup_generator.generation.edit_pipeline import BackgroundRemovalUnavailable, EditParams
from mockup_generator.integrations import drive_client, storage_client
from mockup_generator.integrations.drive_client import DriveNotConfigured

router = APIRouter(prefix="/api/import", tags=["import"])
log = logging.getLogger(__name__)

_PRODUCT_SHOT_THEME = "Product Shot"


def _download(file_id: str) -> bytes:
    try:
        return drive_client.download_file(file_id)
    except DriveNotConfigured as exc:
        raise HTTPException(status_code=503, detail="Drive access is not configured on the server") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Could not load the image: {exc}") from exc


_CUTOUT_CACHE: "OrderedDict[str, bytes]" = OrderedDict()  # file_id -> PNG-encoded RGBA cutout
_CACHE_CAP = 12
_CACHE_LOCK = threading.Lock()
_INFLIGHT: "dict[str, threading.Event]" = {}


def _decode(png: bytes) -> Image.Image:
    return Image.open(BytesIO(png)).convert("RGBA")


def _get_cutout(file_id: str) -> Image.Image:
    """Return the RGBA cutout for a Drive file, computing+caching it (as PNG
    bytes) on a miss. Concurrent misses on the same file_id share ONE compute
    via an in-flight event, so warm + preview never double-run BiRefNet.
    """
    while True:
        with _CACHE_LOCK:
            cached = _CUTOUT_CACHE.get(file_id)
            if cached is not None:
                _CUTOUT_CACHE.move_to_end(file_id)
                return _decode(cached)
            event = _INFLIGHT.get(file_id)
            if event is None:
                event = threading.Event()
                _INFLIGHT[file_id] = event
                break  # we own the compute for this file_id
        # another request is already computing this file_id; wait, then re-check
        event.wait()
    # owner path: compute outside the lock so cache hits / other keys aren't blocked
    try:
        cutout = edit_pipeline.compute_cutout(_download(file_id))
        buf = BytesIO()
        cutout.save(buf, format="PNG")
        png = buf.getvalue()
    except BackgroundRemovalUnavailable as exc:
        with _CACHE_LOCK:
            _INFLIGHT.pop(file_id, None)
        event.set()
        raise HTTPException(status_code=503, detail="Background removal is unavailable on the server") from exc
    except BaseException:
        with _CACHE_LOCK:
            _INFLIGHT.pop(file_id, None)
        event.set()
        raise
    with _CACHE_LOCK:
        _CUTOUT_CACHE[file_id] = png
        _CUTOUT_CACHE.move_to_end(file_id)
        while len(_CUTOUT_CACHE) > _CACHE_CAP:
            _CUTOUT_CACHE.popitem(last=False)
        _INFLIGHT.pop(file_id, None)
    event.set()
    return cutout


def _release_cutout(file_id: str) -> None:
    with _CACHE_LOCK:
        _CUTOUT_CACHE.pop(file_id, None)


def _render(file_id: str, params_model) -> bytes:
    return edit_pipeline.render(_get_cutout(file_id),
                                EditParams(**params_model.model_dump()))


@router.get("/products/{productid}/drive-images", response_model=ImportDriveImagesResponse)
def drive_images(productid: str, user: CurrentUser = Depends(get_current_user),
                 db: Client = Depends(get_db)):
    product = products_repo.get_product(db, productid)
    producturl = getattr(product, "producturl", None) if product else None
    if not producturl:
        raise HTTPException(status_code=404, detail="Product has no Drive folder URL")
    fid = drive_client.extract_folder_id(producturl)
    if not fid:
        raise HTTPException(status_code=404, detail="Could not parse the Drive folder from the product URL")
    try:
        return drive_client.list_folder_image_groups(fid)
    except DriveNotConfigured as exc:
        raise HTTPException(status_code=503, detail="Drive access is not configured on the server") from exc


@router.post("/preview", response_model=PreviewResponse)
def preview(req: PreviewRequest, user: CurrentUser = Depends(get_current_user),
            db: Client = Depends(get_db)):
    png = _render(req.file_id, req.params)
    return PreviewResponse(preview="data:image/png;base64," + base64.b64encode(png).decode("ascii"))


@router.post("/warm")
def warm(req: WarmRequest, user: CurrentUser = Depends(get_current_user)):
    """Pre-compute + cache the cutout so the first adjustment is instant too."""
    _get_cutout(req.file_id)
    return {"status": "ok"}


@router.post("/release")
def release(req: ReleaseRequest, user: CurrentUser = Depends(get_current_user)):
    """Drop the cached cutout once the user is done editing that image."""
    _release_cutout(req.file_id)
    return {"status": "ok"}


@router.post("/publish", response_model=ImportPublishResponse)
def publish_shot(req: ImportPublishRequest, user: CurrentUser = Depends(get_current_user),
                 db: Client = Depends(get_db)):
    webp = publish._encode_webp(_render(req.file_id, req.params))
    order = productimages_repo.next_product_shot_order(db, req.productid)
    slug = storage_client.slugify(req.color)
    stem = "_".join(p for p in (req.productid, slug, str(order)) if p)
    key = f"{stem}_{storage_client.short_hex()}"
    _path, url = storage_client.upload_mockup(
        req.productid, webp, key, ext="webp", content_type="image/webp")
    productimages_repo.insert(db, productid=req.productid, imageurl=url,
                              productcolor=req.color, theme=_PRODUCT_SHOT_THEME,
                              displayorder=order)
    return ImportPublishResponse(image_url=url, displayorder=order)


@router.get("/presets", response_model=PresetsResponse)
def list_presets(user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    return PresetsResponse(presets=edit_presets_repo.list_all(db))


@router.post("/presets", response_model=PresetModel)
def create_preset(req: CreatePresetRequest, user: CurrentUser = Depends(get_current_user),
                  db: Client = Depends(get_db)):
    return edit_presets_repo.insert(db, name=req.name, params=req.params.model_dump(),
                                    is_default=req.is_default, created_by=user.id)


@router.put("/presets/{preset_id}/default")
def mark_default(preset_id: int, user: CurrentUser = Depends(get_current_user),
                 db: Client = Depends(get_db)):
    edit_presets_repo.set_default(db, preset_id)
    return {"status": "ok"}


@router.delete("/presets/{preset_id}")
def delete_preset(preset_id: int, user: CurrentUser = Depends(get_current_user),
                  db: Client = Depends(get_db)):
    edit_presets_repo.delete(db, preset_id)
    return {"status": "ok"}
