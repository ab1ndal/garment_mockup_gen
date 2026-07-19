"""Deterministic, params-driven image edit pipeline for imported product shots.

Pure image ops, no I/O and no heavy model dependencies. `render` applies the
cheap, params-driven colour/geometry ops to a source photo. Background removal
(rembg/BiRefNet) was removed so the backend fits a small-RAM host; imported
shots keep their original background and are colour/geometry-corrected only.
See docs/superpowers/specs/2026-07-12-drive-product-shot-import-design.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

import numpy as np
from PIL import Image, ImageEnhance, ImageOps

WHITE = (255, 255, 255)

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
    hue: float = 0.0               # degrees -180..180, 0 unchanged


def _gray_world(img: Image.Image) -> Image.Image:
    """Scale each channel so its mean equals the global gray mean."""
    rgb = np.asarray(img.convert("RGB"), dtype=np.float64)
    means = rgb.reshape(-1, 3).mean(axis=0)
    gray = means.mean()
    scale = gray / np.clip(means, 1e-6, None)
    out = np.clip(rgb * scale, 0, 255).astype(np.uint8)
    return Image.fromarray(out)  # 3-channel uint8 -> RGB


def _apply_hue(img: Image.Image, degrees: float) -> Image.Image:
    """Rotate the hue channel in HSV space. PIL has no hue enhancer, so shift the
    H channel (0-255 == 0-360 deg) and wrap; saturation/value are untouched."""
    hsv = img.convert("HSV")
    h, s, v = hsv.split()
    shift = int(round(degrees / 360.0 * 256)) % 256
    h = h.point(lambda p: (p + shift) % 256)
    return Image.merge("HSV", (h, s, v)).convert("RGB")


def normalize_source(src_bytes: bytes) -> Image.Image:
    """EXIF-normalise the source and return an RGB image ready for `render`."""
    src = Image.open(BytesIO(src_bytes))
    return ImageOps.exif_transpose(src).convert("RGB")


def render(src: Image.Image, params: EditParams) -> bytes:
    """Apply the cheap, params-driven ops to a source RGB photo.

    Order: white balance, autocontrast, brightness, saturation, hue, then
    quarter-rotate/straighten. No background removal — the whole frame is kept.
    Straighten fills the exposed corners with white. Returns RGB PNG bytes.
    """
    rgb = src.convert("RGB")

    if params.white_balance:
        rgb = _gray_world(rgb)
    if params.autocontrast:
        rgb = ImageOps.autocontrast(rgb, cutoff=1, preserve_tone=True)
    if params.brightness != 1.0:
        rgb = ImageEnhance.Brightness(rgb).enhance(params.brightness)
    if params.saturation != 1.0:
        rgb = ImageEnhance.Color(rgb).enhance(params.saturation)
    if params.hue:
        rgb = _apply_hue(rgb, params.hue)

    q = params.rotate_quarter % 4
    if q:
        rgb = rgb.transpose(_QUARTER_CW[q])
    if params.straighten_deg:
        rgb = rgb.rotate(params.straighten_deg, resample=Image.Resampling.BICUBIC,
                         expand=True, fillcolor=WHITE)

    buf = BytesIO()
    rgb.save(buf, format="PNG")
    return buf.getvalue()


def apply_edits(src_bytes: bytes, params: EditParams) -> bytes:
    """Full pipeline convenience wrapper: normalise then render."""
    return render(normalize_source(src_bytes), params)
