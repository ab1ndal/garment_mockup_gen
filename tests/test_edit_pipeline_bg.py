from io import BytesIO
from PIL import Image
from mockup_generator.generation.edit_pipeline import EditParams, apply_edits, render


def _png_bytes(colour=(120, 60, 30), size=(80, 80)):
    buf = BytesIO()
    Image.new("RGB", size, colour).save(buf, format="PNG")
    return buf.getvalue()


def test_apply_edits_returns_rgb_png():
    out = Image.open(BytesIO(apply_edits(_png_bytes(), EditParams())))
    assert out.mode == "RGB"
    assert out.size == (80, 80)          # no background removal / composite; frame kept


def test_render_keeps_full_frame():
    # render takes a source RGB image; the whole photo is preserved, no cutout
    src = Image.new("RGB", (80, 80), (120, 60, 30))
    out = Image.open(BytesIO(render(src, EditParams())))
    assert out.mode == "RGB"
    assert out.getpixel((1, 1)) == out.getpixel((40, 40))   # uniform frame, nothing cut


def test_hue_shift_changes_colour():
    src = Image.new("RGB", (40, 40), (200, 40, 40))          # red
    base = Image.open(BytesIO(render(src, EditParams(autocontrast=False))))
    shifted = Image.open(BytesIO(render(src, EditParams(autocontrast=False, hue=120))))
    assert base.getpixel((0, 0)) != shifted.getpixel((0, 0))  # 120deg rotates hue


def test_hue_zero_is_noop():
    src = Image.new("RGB", (40, 40), (200, 40, 40))
    a = Image.open(BytesIO(render(src, EditParams(autocontrast=False))))
    b = Image.open(BytesIO(render(src, EditParams(autocontrast=False, hue=0.0))))
    assert a.getpixel((0, 0)) == b.getpixel((0, 0))


def test_render_rotate_quarter_swaps_dims():
    src = Image.new("RGB", (100, 40), (120, 60, 30))
    out = Image.open(BytesIO(render(src, EditParams(rotate_quarter=1))))
    assert out.size == (40, 100)                     # 90deg swaps w/h
