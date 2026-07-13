"""Erase the Gemini sparkle watermark from the bottom-right corner.

The sparkle sits on flat studio background at a fixed offset from the corner
(measured on a 680x1082 reference: 39x39 px, ~38 px in from each edge). The
ROI below covers it with margin; its contents are repainted with a Coons patch
built from the 1-px ring around the ROI — a transfinite interpolation that
matches all four boundary edges exactly, so the fill meets the surrounding
pixels with no visible step even under shadow gradients. Photo grain measured
in a band around the ROI is re-added so the patch is not a suspiciously smooth
rectangle. Manual toggle decides whether this runs — there is no detection.
"""

from __future__ import annotations

from io import BytesIO

import numpy as np
from PIL import Image

# Relative ROI bounds (fraction of width/height).
ROI_X0, ROI_X1 = 0.86, 0.97
ROI_Y0, ROI_Y1 = 0.915, 0.98


def _grain_sigma(a: np.ndarray, x0: int, x1: int, y0: int, y1: int) -> float:
    """Luminance grain level in a band around the ROI.

    Median of absolute row-to-row differences: robust to garment edges in the
    band, and unlike a high-pass residual it captures the row-scale roughness
    the eye compares, even when photo grain is spatially correlated.
    """
    bw = max(4, (y1 - y0) // 4)
    bx0, by0 = max(0, x0 - bw), max(0, y0 - bw)
    bx1, by1 = min(a.shape[1], x1 + bw), min(a.shape[0], y1 + bw)
    band = a[by0:by1, bx0:bx1].mean(axis=-1)
    mask = np.ones(band.shape, dtype=bool)
    mask[y0 - by0:y0 - by0 + (y1 - y0), x0 - bx0:x0 - bx0 + (x1 - x0)] = False
    d = np.diff(band, axis=0)
    valid = mask[1:] & mask[:-1]
    if not valid.any():
        return 0.0
    # For Gaussian noise: median|diff| = 0.6745 * sigma * sqrt(2).
    return float(np.median(np.abs(d[valid])) / (0.6745 * np.sqrt(2)))


def remove_corner_star(png_bytes: bytes) -> bytes:
    """Repaint the bottom-right ROI from its surrounding background.

    Returns PNG (RGB) bytes. Images too small to hold the ROI plus a 1-px
    border ring are returned unchanged.
    """
    a = np.asarray(Image.open(BytesIO(png_bytes)).convert("RGB")).astype(np.float64)
    h, w = a.shape[:2]
    x0, x1 = int(w * ROI_X0), int(w * ROI_X1)
    y0, y1 = int(h * ROI_Y0), int(h * ROI_Y1)
    if x1 - x0 < 1 or y1 - y0 < 1 or x0 < 1 or y0 < 1 or x1 >= w or y1 >= h:
        return png_bytes

    rh, rw = y1 - y0, x1 - x0
    top, bottom = a[y0 - 1, x0:x1], a[y1, x0:x1]          # (rw, 3)
    left, right = a[y0:y1, x0 - 1], a[y0:y1, x1]          # (rh, 3)
    c00, c01 = a[y0 - 1, x0 - 1], a[y0 - 1, x1]           # ring corners
    c10, c11 = a[y1, x0 - 1], a[y1, x1]
    ty = ((np.arange(rh) + 1) / (rh + 1))[:, None, None]  # 0..1 down the ROI
    tx = ((np.arange(rw) + 1) / (rw + 1))[None, :, None]  # 0..1 across the ROI
    vert = top[None, :, :] * (1 - ty) + bottom[None, :, :] * ty
    horiz = left[:, None, :] * (1 - tx) + right[:, None, :] * tx
    corners = (c00 * (1 - ty) * (1 - tx) + c01 * (1 - ty) * tx
               + c10 * ty * (1 - tx) + c11 * ty * tx)
    fill = vert + horiz - corners  # Coons patch: exact on all four edges

    sigma = _grain_sigma(a, x0, x1, y0, y1)
    if sigma > 0.25:  # skip on synthetic/flat images
        rng = np.random.default_rng(np.frombuffer(png_bytes[:32], dtype=np.uint8))
        # Luminance grain: one noise field shared across channels, like photo
        # sensor/JPEG grain (chroma noise is far weaker than luma).
        fill = fill + rng.normal(0.0, sigma, (rh, rw, 1))
    a[y0:y1, x0:x1] = fill

    out = Image.fromarray(a.round().clip(0, 255).astype(np.uint8))
    buf = BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()
