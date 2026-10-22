"""Shared publish path for approved mockups.

Uploads the PNG, its display WEBP and 150/600/1200px WEBP size variants to the
public ``mockups`` bucket and *appends* a new
``productimages`` row — every approve is kept, so multiple designs for the same
``(productid, color, phototheme)`` coexist instead of overwriting one another.
The display order (the next append position) is baked into the Storage key, so
each design also gets its own permanent object. Also writes a
``mockup_variations`` audit row and flips ``mockups.base_mockup``. Used by both
``/generate/approve`` (Phase 3) and the Phase 7 backfill flow — one publish
path, no duplication.
"""

from __future__ import annotations

import threading
from io import BytesIO

from PIL import Image

from mockup_generator.db import mockup_variations_repo, mockups_repo, productimages_repo
from mockup_generator.integrations import storage_client

_WEBP_QUALITY = 85

# Longest side of the web display variant. The archival PNG keeps the model's
# full output; this copy only ever feeds an <img> on the storefront, where 2048
# is already generous. Beyond it the encode cost climbs faster than the value: a
# 4K re-encode holds the decoded RGB buffer plus libwebp's own ARGB copy at once,
# ~227 MB per call against a 512 MB instance.
_WEBP_MAX_SIDE = 2048

# Approvals arrive on FastAPI's request threadpool, which is far wider than this
# box can afford for full-resolution image work — two concurrent 4K encodes plus
# the batch drainers is what tripped the memory limit. Two at a time keeps the
# peak bounded without making a reviewer wait on a queue in practice, since
# approvals are hand-driven one card at a time.
_ENCODE_SLOTS = threading.Semaphore(2)

# Fixed-width copies the storefront requests as ``{key}_{width}.webp`` instead of
# resizing on the fly. The naming is a contract with the storefront and the
# backfill script — do not change one side alone.
_VARIANT_WIDTHS = (150, 600, 1200)
_VARIANT_QUALITY = 80
# Keys carry a per-publish random suffix and are never rewritten, so browsers
# and the CDN can cache them for a year.
_VARIANT_CACHE_CONTROL = "31536000"


def _webp_bytes(img: Image.Image, quality: int) -> bytes:
    buf = BytesIO()
    img.save(buf, format="WEBP", quality=quality)
    return buf.getvalue()


def _encode_webp(png: bytes) -> bytes:
    """Re-encode PNG bytes to lossy WEBP (quality 85) for web display,
    downscaled to ``_WEBP_MAX_SIDE``. Smaller images are left as they are."""
    with _ENCODE_SLOTS, Image.open(BytesIO(png)) as img:
        img.thumbnail((_WEBP_MAX_SIDE, _WEBP_MAX_SIDE))
        return _webp_bytes(img, _WEBP_QUALITY)


def upload_web_images(productid: str, png: bytes, key: str) -> str:
    """Upload the display WEBP ``{key}.webp`` plus its fixed-width variants and
    return the display WEBP's public URL.

    Call before inserting the ``productimages`` row so a live row always has its
    variants. The PNG is decoded once, capped at ``_WEBP_MAX_SIDE``, and every
    encode derives from that copy; only one resized copy is alive at a time and
    the whole call holds an encode slot (this runs on a 512 MB instance).
    """
    with _ENCODE_SLOTS, Image.open(BytesIO(png)) as img:
        img.thumbnail((_WEBP_MAX_SIDE, _WEBP_MAX_SIDE))
        _path, public_url = storage_client.upload_mockup(
            productid, _webp_bytes(img, _WEBP_QUALITY), key,
            ext="webp", content_type="image/webp",
        )
        # Palette images would resize with NEAREST (Pillow forces it for "P").
        src = img.convert("RGBA") if img.mode in ("P", "1") else img
        for width in _VARIANT_WIDTHS:
            # Never enlarge: a narrower source keeps its own size under this key.
            if src.width > width:
                height = max(1, round(src.height * width / src.width))
                variant = src.resize((width, height), Image.LANCZOS)
            else:
                variant = src
            storage_client.upload_mockup(
                productid, _webp_bytes(variant, _VARIANT_QUALITY), f"{key}_{width}",
                ext="webp", content_type="image/webp",
                cache_control=_VARIANT_CACHE_CONTROL,
            )
            del variant
    return public_url


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
    public_url = upload_web_images(productid, png, key)

    row = mockup_variations_repo.insert(
        db, productid=productid, prompt_text=prompt_text, image_url=public_url,
        color=color, created_by=created_by, prompt_id=prompt_id,
    )
    mockups_repo.set_base_mockup(db, productid, True)
    productimages_repo.insert(db, productid=productid, imageurl=public_url,
                              productcolor=color, theme=theme, displayorder=order)
    return {"image_url": public_url, "variation_id": row.get("variation_id")}
