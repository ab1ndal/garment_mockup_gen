"""Shared publish path for approved mockups.

Uploads the PNG to the public ``mockups`` bucket and *appends* a new
``productimages`` row — every approve is kept, so multiple designs for the same
``(productid, color, phototheme)`` coexist instead of overwriting one another.
The display order (the next append position) is baked into the Storage key, so
each design also gets its own permanent object. Also writes a
``mockup_variations`` audit row and flips ``mockups.base_mockup``. Used by both
``/generate/approve`` (Phase 3) and the Phase 7 backfill flow — one publish
path, no duplication.
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image

from mockup_generator.db import mockup_variations_repo, mockups_repo, productimages_repo
from mockup_generator.integrations import storage_client

_WEBP_QUALITY = 85


def _encode_webp(png: bytes) -> bytes:
    """Re-encode PNG bytes to lossy WEBP (quality 85) for web display."""
    with Image.open(BytesIO(png)) as img:
        buf = BytesIO()
        img.save(buf, format="WEBP", quality=_WEBP_QUALITY)
        return buf.getvalue()


def build_photo_theme(theme_name: str | None, aspect_ratio: str | None) -> str:
    """Dedup photo-theme string: label, plus ``·<aspect>`` for non-1:1."""
    label = (theme_name or productimages_repo.DEFAULT_THEME).strip() \
        or productimages_repo.DEFAULT_THEME
    if aspect_ratio and aspect_ratio != "1:1":
        return f"{label}·{aspect_ratio}"
    return label


def publish_image(
    db, *, productid: str, png: bytes, color: str | None,
    theme_name: str | None, aspect_ratio: str | None, created_by: str | None,
    prompt_text: str | None = None, prompt_id: int | None = None,
) -> dict:
    """Publish ``png`` for ``productid`` + ``color``. Returns
    ``{"image_url", "variation_id"}``."""
    theme = build_photo_theme(theme_name, aspect_ratio)

    # Append: never overwrite a prior design. The next display order goes into
    # the Storage key so each variant gets its own permanent object.
    order = productimages_repo.next_display_order(db, productid)
    slug = storage_client.slugify(color)
    stem = "_".join(p for p in (slug, str(order)) if p)
    key = f"{stem}_{storage_client.short_hex()}"
    # Keep the PNG as archival; the DB references the web-optimized WEBP under
    # the same key stem (different extension, no collision).
    storage_client.upload_mockup(productid, png, key)
    _path, public_url = storage_client.upload_mockup(
        productid, _encode_webp(png), key, ext="webp", content_type="image/webp"
    )

    row = mockup_variations_repo.insert(
        db, productid=productid, prompt_text=prompt_text, image_url=public_url,
        color=color, created_by=created_by, prompt_id=prompt_id,
    )
    mockups_repo.set_base_mockup(db, productid, True)
    productimages_repo.insert(db, productid=productid, imageurl=public_url,
                              productcolor=color, theme=theme, displayorder=order)
    return {"image_url": public_url, "variation_id": row.get("variation_id")}
