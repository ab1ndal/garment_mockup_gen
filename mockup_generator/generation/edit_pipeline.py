"""Deterministic, params-driven image edit pipeline for imported product shots.

Pure image ops (no I/O) except the lazily-loaded rembg session. Colour/tonal ops
run on RGB before straighten introduces alpha; the BiRefNet cutout + composite +
optional shadow run last. See
docs/superpowers/specs/2026-07-12-drive-product-shot-import-design.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from mockup_generator.config import settings

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


_BG_COLOURS = {"white": WHITE, "cream": CREAM}
_session = None


class BackgroundRemovalUnavailable(RuntimeError):
    """rembg / the BiRefNet model could not be loaded or run."""


def _get_session():
    global _session
    if _session is None:
        try:
            from rembg import new_session
            _session = new_session(settings.rembg_model)
        except Exception as exc:  # noqa: BLE001 - surfaced as 503 upstream
            raise BackgroundRemovalUnavailable(str(exc)) from exc
    return _session


def _remove_background(img: Image.Image) -> Image.Image:
    """Return an RGBA cutout via rembg + BiRefNet. The single rembg touch-point."""
    try:
        from rembg import remove
        return remove(img, session=_get_session(), post_process_mask=True).convert("RGBA")
    except BackgroundRemovalUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        raise BackgroundRemovalUnavailable(str(exc)) from exc


def _add_drop_shadow(fg: Image.Image, bg_rgb: tuple[int, int, int],
                     offset: tuple[int, int] = (0, 18), blur: int = 24,
                     opacity: float = 0.35) -> Image.Image:
    fg = fg.convert("RGBA")
    w, h = fg.size
    margin = blur * 3
    size = (w + margin * 2, h + margin * 2)
    shadow = Image.new("RGBA", size, (0, 0, 0, 0))
    solid = Image.new("RGBA", (w, h), (0, 0, 0, int(255 * opacity)))
    shadow.paste(solid, (margin + offset[0], margin + offset[1]), fg.getchannel("A"))
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur))
    base = Image.new("RGBA", size, bg_rgb + (255,))
    fg_layer = Image.new("RGBA", size, (0, 0, 0, 0))
    fg_layer.paste(fg, (margin, margin), fg)
    out = Image.alpha_composite(base, shadow)
    out = Image.alpha_composite(out, fg_layer)
    return out.convert("RGB")


def apply_edits(src_bytes: bytes, params: EditParams) -> bytes:
    """Full pipeline: geometry+colour -> cutout -> composite -> optional shadow.

    Returns RGB PNG bytes. Raises BackgroundRemovalUnavailable if rembg/the model
    cannot be loaded or run.
    """
    src = Image.open(BytesIO(src_bytes))
    prepared = apply_geometry_and_colour(src, params)       # RGBA
    cutout = _remove_background(prepared)                    # RGBA
    bg_rgb = _BG_COLOURS.get(params.bg, WHITE)
    if params.shadow:
        composited = _add_drop_shadow(cutout, bg_rgb)
    else:
        base = Image.new("RGBA", cutout.size, bg_rgb + (255,))
        composited = Image.alpha_composite(base, cutout).convert("RGB")
    buf = BytesIO()
    composited.save(buf, format="PNG")
    return buf.getvalue()
