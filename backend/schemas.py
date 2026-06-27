# backend/schemas.py
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class CategoryOut(BaseModel):
    categoryid: str
    name: str


class ProductOut(BaseModel):
    productid: str
    name: str
    categoryid: str | None = None
    category_name: str | None = None
    base_mockup: bool = False
    producturl: str | None = None


class ProductImage(BaseModel):
    id: str
    name: str
    mime_type: str
    thumbnail_url: str


class ProductImageGroup(BaseModel):
    """A variant subfolder and its images."""
    id: str
    name: str
    images: list[ProductImage]


class ProductImages(BaseModel):
    """Source images for a product: loose (top-level) + per-subfolder variant groups."""
    loose: list[ProductImage]
    groups: list[ProductImageGroup]


class PromptOut(BaseModel):
    prompt_id: int
    categoryid: str
    label: str
    body: str
    is_default: bool


class PromptCreate(BaseModel):
    categoryid: str
    label: str
    body: str
    is_default: bool = False


class PromptUpdate(BaseModel):
    label: str | None = None
    body: str | None = None
    is_default: bool | None = None


class RefineRequest(BaseModel):
    instruction: str
    categoryid: str | None = None
    kind: Literal["image", "video"] = "image"


class RefineResponse(BaseModel):
    refined: str


class GenerateRequest(BaseModel):
    productid: str
    prompt: str
    image_ids: list[str] = []
    model: str | None = None
    resolution: str | None = None
    aspect_ratio: str | None = None
    color: str | None = None
    refine_image_b64: str | None = None   # prior output, included as an extra reference on refine


class VideoGenerateRequest(BaseModel):
    productid: str
    prompt: str
    image_url: str | None = None   # supabase mockup URL to animate; else resolved by color
    color: str | None = None
    model: str | None = None
    resolution: str | None = None
    aspect_ratio: str | None = None
    duration: int | None = None


class VideoJobResponse(BaseModel):
    job_id: str
    status: str               # pending | running | done | error
    detail: str | None = None


class GeneratePreview(BaseModel):
    status: str
    detail: str
    image_b64: str


class GenerateUploadPreview(BaseModel):
    status: str
    detail: str
    image_b64: str
    mime_type: str


class GenerateResponse(BaseModel):
    status: str
    detail: str
    image_url: str | None = None
    variation_id: int | None = None


class ApproveResponse(BaseModel):
    status: str
    detail: str
    image_url: str
    variation_id: int | None = None


class BackfillItem(BaseModel):
    productid: str | None
    product_name: str | None
    alpha: str | None
    file_id: str
    filename: str
    thumbnail_url: str | None
    unknown_product: bool


class BackfillItemsResponse(BaseModel):
    total: int            # full count for the requested status (drives the pager)
    offset: int
    limit: int
    items: list[BackfillItem]


class BackfillCountsResponse(BaseModel):
    counts: dict[str, int]   # {status: count} for each sub-tab


class BackfillApproveRequest(BaseModel):
    file_id: str
    productid: str
    color: str | None = None
    theme_name: str | None = None
    aspect_ratio: str | None = None


class BackfillFlagRequest(BaseModel):
    file_id: str
    productid: str | None = None


class BackfillEditRequest(BaseModel):
    file_id: str
    productid: str | None = None
    comment: str | None = None
