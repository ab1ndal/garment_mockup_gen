from io import BytesIO

import numpy as np
from PIL import Image

from mockup_generator.generation.watermark import remove_corner_star, ROI_X0, ROI_X1, ROI_Y0, ROI_Y1

W, H = 680, 1082
BG = (221, 221, 221)
# Star bbox measured on the reference sample.
STAR = (603, 1006, 642, 1045)  # x0, y0, x1, y1


def _png(img: Image.Image) -> bytes:
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _with_star() -> bytes:
    img = Image.new("RGB", (W, H), BG)
    px = img.load()
    x0, y0, x1, y1 = STAR
    cx, cy, r = (x0 + x1) // 2, (y0 + y1) // 2, (x1 - x0) // 2
    for y in range(y0, y1):
        for x in range(x0, x1):
            if abs(x - cx) + abs(y - cy) <= r:   # diamond, like the sparkle
                px[x, y] = (245, 245, 245)
    return _png(img)


def test_star_erased():
    out = remove_corner_star(_with_star())
    a = np.asarray(Image.open(BytesIO(out)).convert("RGB")).astype(int)
    x0, y0, x1, y1 = STAR
    star_region = a[y0:y1, x0:x1]
    assert np.abs(star_region - np.array(BG)).max() <= 2  # flat bg restored


def test_pixels_outside_roi_untouched():
    src = _with_star()
    out = remove_corner_star(src)
    before = np.asarray(Image.open(BytesIO(src)).convert("RGB"))
    after = np.asarray(Image.open(BytesIO(out)).convert("RGB"))
    rx0, ry0 = int(W * ROI_X0), int(H * ROI_Y0)
    rx1, ry1 = int(W * ROI_X1), int(H * ROI_Y1)
    mask = np.ones((H, W), dtype=bool)
    mask[ry0:ry1, rx0:rx1] = False
    assert (before[mask] == after[mask]).all()


def test_no_star_is_visual_noop():
    src = _png(Image.new("RGB", (W, H), BG))
    out = remove_corner_star(src)
    a = np.asarray(Image.open(BytesIO(out)).convert("RGB")).astype(int)
    assert np.abs(a - np.array(BG)).max() <= 2  # whole image still flat bg


def test_gradient_background_reconstructed():
    # Vertical gradient — the blend must follow it, not flatten it.
    grad = np.tile(np.linspace(180, 240, H).astype(np.uint8)[:, None, None], (1, W, 3))
    src = _png(Image.fromarray(grad))
    out = remove_corner_star(src)
    a = np.asarray(Image.open(BytesIO(out)).convert("RGB")).astype(int)
    assert np.abs(a - grad.astype(int)).max() <= 4


def test_tiny_image_returned_unchanged():
    src = _png(Image.new("RGB", (5, 5), BG))
    assert remove_corner_star(src) == src


def _shadow_blob() -> np.ndarray:
    # Soft shadow falling next to the ROI, like a garment hem shadow —
    # nonlinear and non-separable, the case that exposed a visible seam.
    yy, xx = np.mgrid[0:H, 0:W].astype(float)
    g = 230 - 100 * np.exp(-(((xx - 0.80 * W) / (0.08 * W)) ** 2
                             + ((yy - 0.95 * H) / (0.06 * H)) ** 2))
    return np.repeat(np.clip(g, 0, 255).astype(np.uint8)[:, :, None], 3, axis=2)


def test_no_seam_on_shadowed_background():
    # The fill must meet the surrounding pixels without a visible step on any
    # ROI edge (JND is ~2/255 on smooth areas).
    src = _png(Image.fromarray(_shadow_blob()))
    out = remove_corner_star(src)
    a = np.asarray(Image.open(BytesIO(out)).convert("RGB")).astype(float)
    x0, x1 = int(W * ROI_X0), int(W * ROI_X1)
    y0, y1 = int(H * ROI_Y0), int(H * ROI_Y1)
    edges = {
        "left": np.abs(a[y0:y1, x0] - a[y0:y1, x0 - 1]),
        "right": np.abs(a[y0:y1, x1 - 1] - a[y0:y1, x1]),
        "top": np.abs(a[y0, x0:x1] - a[y0 - 1, x0:x1]),
        "bottom": np.abs(a[y1 - 1, x0:x1] - a[y1, x0:x1]),
    }
    for name, d in edges.items():
        assert d.mean() <= 1.0, f"{name} edge step {d.mean():.2f}"


def test_grain_matches_surroundings():
    # On a noisy photo the repainted patch must carry similar grain, not be a
    # perfectly smooth rectangle.
    rng = np.random.default_rng(7)
    noisy = np.clip(np.full((H, W, 3), 221.0) + rng.normal(0, 3, (H, W, 3)), 0, 255)
    src = _png(Image.fromarray(noisy.astype(np.uint8)))
    out = remove_corner_star(src)
    a = np.asarray(Image.open(BytesIO(out)).convert("RGB")).astype(float).mean(axis=-1)
    x0, x1 = int(W * ROI_X0), int(W * ROI_X1)
    y0, y1 = int(H * ROI_Y0), int(H * ROI_Y1)
    inside = np.abs(np.diff(a[y0 + 5:y1 - 5, x0 + 5:x1 - 5], axis=0)).mean()
    outside = np.abs(np.diff(a[y0 - 40:y0 - 5, x0:x1], axis=0)).mean()
    assert inside >= outside * 0.5, f"inside grain {inside:.2f} vs outside {outside:.2f}"
    assert inside <= outside * 1.5, f"inside grain {inside:.2f} vs outside {outside:.2f}"
