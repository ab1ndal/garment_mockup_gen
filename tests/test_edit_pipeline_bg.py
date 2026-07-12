from io import BytesIO
from PIL import Image
import mockup_generator.generation.edit_pipeline as ep
from mockup_generator.generation.edit_pipeline import EditParams, apply_edits


def _png_bytes(colour=(120, 60, 30), size=(80, 80)):
    buf = BytesIO()
    Image.new("RGB", size, colour).save(buf, format="PNG")
    return buf.getvalue()


def _fake_cutout(img):
    # opaque centre square, transparent border -> lets us see the bg composite
    rgba = img.convert("RGBA")
    a = Image.new("L", rgba.size, 0)
    w, h = rgba.size
    for x in range(w // 4, 3 * w // 4):
        for y in range(h // 4, 3 * h // 4):
            a.putpixel((x, y), 255)
    rgba.putalpha(a)
    return rgba


def test_apply_edits_composites_white_bg(monkeypatch):
    monkeypatch.setattr(ep, "_remove_background", _fake_cutout)
    out = Image.open(BytesIO(apply_edits(_png_bytes(), EditParams(bg="white"))))
    assert out.mode == "RGB"
    assert out.getpixel((1, 1)) == (255, 255, 255)     # transparent border -> white


def test_apply_edits_composites_cream_bg(monkeypatch):
    monkeypatch.setattr(ep, "_remove_background", _fake_cutout)
    out = Image.open(BytesIO(apply_edits(_png_bytes(), EditParams(bg="cream"))))
    assert out.getpixel((1, 1)) == (250, 247, 240)     # cream corner


def test_apply_edits_shadow_pads_canvas(monkeypatch):
    monkeypatch.setattr(ep, "_remove_background", _fake_cutout)
    no_shadow = Image.open(BytesIO(apply_edits(_png_bytes(), EditParams(shadow=False))))
    shadow = Image.open(BytesIO(apply_edits(_png_bytes(), EditParams(shadow=True))))
    assert shadow.size[1] > no_shadow.size[1]          # shadow pads canvas height
