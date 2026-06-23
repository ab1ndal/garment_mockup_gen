# backend/schemas.py
from __future__ import annotations

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


class GenerateRequest(BaseModel):
    productid: str
    prompt: str
    image_ids: list[str] = []
    model: str | None = None
    resolution: str | None = None
    aspect_ratio: str | None = None
    color: str | None = None


class GeneratePreview(BaseModel):
    status: str
    detail: str
    image_b64: str


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
