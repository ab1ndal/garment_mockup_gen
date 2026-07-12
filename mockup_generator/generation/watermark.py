"""Erase the Gemini sparkle watermark from the bottom-right corner.

The sparkle sits on flat studio background at a fixed offset from the corner
(measured on a 680x1082 reference: 39x39 px, ~38 px in from each edge). The
ROI below covers it with margin; its contents are repainted as the average of
a vertical blend (row above the ROI -> row below) and a horizontal blend
(column left of the ROI -> column right), which reproduces flat or gently
graded backgrounds and erases anything printed on top. Manual toggle decides
whether this runs — there is no detection.
"""

from __future__ import annotations

from io import BytesIO

import numpy as np
from PIL import Image

# Relative ROI bounds (fraction of width/height).
ROI_X0, ROI_X1 = 0.86, 0.97
ROI_Y0, ROI_Y1 = 0.915, 0.98


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
    ty = ((np.arange(rh) + 1) / (rh + 1))[:, None, None]  # 0..1 down the ROI
    tx = ((np.arange(rw) + 1) / (rw + 1))[None, :, None]  # 0..1 across the ROI
    vert = top[None, :, :] * (1 - ty) + bottom[None, :, :] * ty
    horiz = left[:, None, :] * (1 - tx) + right[:, None, :] * tx
    a[y0:y1, x0:x1] = (vert + horiz) / 2

    out = Image.fromarray(a.round().clip(0, 255).astype(np.uint8))
    buf = BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()
