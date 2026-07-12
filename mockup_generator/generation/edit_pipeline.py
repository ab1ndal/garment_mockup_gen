"""Deterministic, params-driven image edit pipeline for imported product shots.

Pure image ops (no I/O) except the lazily-loaded rembg session. Colour/tonal ops
run on RGB before straighten introduces alpha; the BiRefNet cutout + composite +
optional shadow run last. See
docs/superpowers/specs/2026-07-12-drive-product-shot-import-design.md.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageEnhance, ImageOps

WHITE = (255, 255, 255)
CREAM = (250, 247, 240)  # #FAF7F0

_QUARTER_CW = {
    1: Image.Transpose.ROTATE_270,  # Pillow ROTATE_90 is CCW; 270 == 90 clockwise
    2: Image.Transpose.ROTATE_180,
    3: Image.Transpose.ROTATE_90,
}


@dataclass
class EditParams:
    rotate_quarter: int = 0        # 0|1|2|3 -> 0/90/180/270 clockwise
    straighten_deg: float = 0.0    # +-15
    autocontrast: bool = True
    white_balance: bool = False    # gray-world
    brightness: float = 1.0        # ~0.5-1.5, 1.0 unchanged
    saturation: float = 1.0        # ~0.5-1.5, 1.0 unchanged
    bg: str = "white"              # "white" | "cream"
    shadow: bool = False


def _gray_world(img: Image.Image, mask: Image.Image | None = None) -> Image.Image:
    """Scale each channel so its mean equals the global gray mean."""
    rgb = np.asarray(img.convert("RGB"), dtype=np.float64)
    if mask is not None:
        keep = np.asarray(mask.convert("L")) > 0
        sel = rgb[keep] if keep.any() else rgb.reshape(-1, 3)
    else:
        sel = rgb.reshape(-1, 3)
    means = sel.mean(axis=0)
    gray = means.mean()
    scale = gray / np.clip(means, 1e-6, None)
    out = np.clip(rgb * scale, 0, 255).astype(np.uint8)
    return Image.fromarray(out)  # 3-channel uint8 -> RGB


def apply_geometry_and_colour(img: Image.Image, params: EditParams) -> Image.Image:
    """EXIF-normalise -> quarter-rotate -> colour/tonal (RGB) -> straighten (RGBA)."""
    img = ImageOps.exif_transpose(img).convert("RGB")

    q = params.rotate_quarter % 4
    if q:
        img = img.transpose(_QUARTER_CW[q])

    if params.white_balance:
        img = _gray_world(img)
    if params.autocontrast:
        img = ImageOps.autocontrast(img, cutoff=1, preserve_tone=True)
    if params.brightness != 1.0:
        img = ImageEnhance.Brightness(img).enhance(params.brightness)
    if params.saturation != 1.0:
        img = ImageEnhance.Color(img).enhance(params.saturation)

    rgba = img.convert("RGBA")
    if params.straighten_deg:
        rgba = rgba.rotate(params.straighten_deg, resample=Image.Resampling.BICUBIC,
                           expand=True, fillcolor=(0, 0, 0, 0))
    return rgba
