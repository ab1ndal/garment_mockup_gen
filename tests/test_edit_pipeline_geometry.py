from PIL import Image
from mockup_generator.generation.edit_pipeline import (
    EditParams, apply_geometry_and_colour,
)


def _img(w=100, h=60, colour=(120, 60, 30)):
    return Image.new("RGB", (w, h), colour)


def test_quarter_rotate_swaps_dimensions():
    out = apply_geometry_and_colour(_img(100, 60), EditParams(rotate_quarter=1))
    assert out.size == (60, 100)          # 90 deg swaps W/H
    assert out.mode == "RGBA"


def test_no_rotation_keeps_dimensions():
    out = apply_geometry_and_colour(_img(100, 60), EditParams())
    assert out.size == (100, 60)


def test_straighten_expands_and_adds_transparency():
    out = apply_geometry_and_colour(_img(100, 60), EditParams(straighten_deg=10))
    assert out.size[0] > 100 and out.size[1] > 60   # expand=True grows canvas
    assert out.getchannel("A").getextrema()[0] == 0  # transparent corners exist


def test_brightness_increases_pixel_values():
    base = apply_geometry_and_colour(_img(colour=(100, 100, 100)),
                                     EditParams(autocontrast=False))
    bright = apply_geometry_and_colour(_img(colour=(100, 100, 100)),
                                       EditParams(autocontrast=False, brightness=1.4))
    assert bright.convert("RGB").getpixel((0, 0))[0] > \
        base.convert("RGB").getpixel((0, 0))[0]


def test_gray_world_neutralises_colour_cast():
    # a red-cast grey image should move toward neutral
    out = apply_geometry_and_colour(_img(colour=(160, 120, 120)),
                                    EditParams(autocontrast=False, white_balance=True))
    r, g, b = out.convert("RGB").getpixel((0, 0))
    assert abs(r - g) < 160 - 120          # cast reduced vs original 40-gap
