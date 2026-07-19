from io import BytesIO
from PIL import Image
from mockup_generator.generation.edit_pipeline import EditParams, render


def _src(w=100, h=60, colour=(120, 60, 30)):
    # RGB source photo; colour/geometry ops apply to the whole frame
    return Image.new("RGB", (w, h), colour)


def _out(src, params):
    return Image.open(BytesIO(render(src, params)))


def test_quarter_rotate_swaps_dimensions():
    assert _out(_src(100, 60), EditParams(rotate_quarter=1)).size == (60, 100)


def test_no_rotation_keeps_dimensions():
    assert _out(_src(100, 60), EditParams()).size == (100, 60)


def test_straighten_expands_canvas():
    out = _out(_src(100, 60), EditParams(straighten_deg=10))
    assert out.size[0] > 100 and out.size[1] > 60   # expand=True grows canvas


def test_brightness_increases_pixel_values():
    base = _out(_src(colour=(100, 100, 100)), EditParams(autocontrast=False))
    bright = _out(_src(colour=(100, 100, 100)),
                  EditParams(autocontrast=False, brightness=1.4))
    assert bright.getpixel((0, 0))[0] > base.getpixel((0, 0))[0]


def test_gray_world_neutralises_colour_cast():
    out = _out(_src(colour=(160, 120, 120)),
               EditParams(autocontrast=False, white_balance=True))
    r, g, _b = out.getpixel((0, 0))
    assert abs(r - g) < 160 - 120          # red cast reduced vs original 40-gap
