from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from supabase import Client

from backend.auth import CurrentUser, get_current_user
from backend.deps import get_db
from backend.schemas import CategoryOut, ProductImage, ProductOut
from mockup_generator.db import products_repo
from mockup_generator.integrations import drive_client
from mockup_generator.integrations.drive_client import DriveNotConfigured

router = APIRouter(prefix="/api", tags=["products"])


@router.get("/categories", response_model=list[CategoryOut])
def list_categories(user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    return [CategoryOut(categoryid=cid, name=name) for cid, name in products_repo.list_categories(db)]


@router.get("/products", response_model=list[ProductOut])
def list_products(
    category: str | None = None,
    id: str | None = None,
    id_start: str | None = None,
    id_end: str | None = None,
    pending: bool = True,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: CurrentUser = Depends(get_current_user),
    db: Client = Depends(get_db),
):
    try:
        rows = products_repo.list_products(
            db, category=category, product_id=id, id_start=id_start, id_end=id_end,
            pending=pending, limit=limit, offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return [ProductOut(**vars(p)) for p in rows]


@router.get("/products/{productid}", response_model=ProductOut)
def get_product(productid: str, user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    p = products_repo.get_product(db, productid)
    if p is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return ProductOut(**vars(p))


@router.get("/products/{productid}/images", response_model=list[ProductImage])
def list_product_images(
    productid: str,
    user: CurrentUser = Depends(get_current_user),
    db: Client = Depends(get_db),
):
    """Source images in the product's Drive folder, for preview + selection."""
    p = products_repo.get_product(db, productid)
    if p is None:
        raise HTTPException(status_code=404, detail="Product not found")

    folder_id = drive_client.extract_folder_id(p.producturl)
    if not folder_id:
        raise HTTPException(status_code=409, detail="Product has no linked Drive folder")

    try:
        images = drive_client.list_folder_images(folder_id)
    except DriveNotConfigured as exc:
        raise HTTPException(status_code=503, detail="Drive access is not configured on the server") from exc
    except Exception as exc:  # Drive API / network errors
        raise HTTPException(status_code=502, detail=f"Could not read Drive folder: {exc}") from exc

    return [ProductImage(**img) for img in images]
