"""Tests for ``load_reference_image`` — the bounded reference decoder.

Generation only ever sends a MAX_SIDE JPEG, so a reference must never be held at
its original resolution: five 12 MP originals per batch card across six drainers
is what pushed the 512 MB instance past its memory limit. These pin the size
bound and the release of the full-resolution buffer.
"""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from mockup_generator.generation.common import MAX_SIDE, load_reference_image


def _encoded(size: tuple[int, int], fmt: str = "JPEG", mode: str = "RGB") -> bytes:
    buf = BytesIO()
    Image.new(mode, size, (10, 20, 30) if mode == "RGB" else 128).save(buf, format=fmt)
    return buf.getvalue()


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
def test_oversized_reference_is_bounded_to_max_side(fmt):
    """Every supported format comes back within MAX_SIDE — ``draft`` only helps
    JPEG, so the others have to be caught by the thumbnail step."""
    im = load_reference_image(_encoded((4000, 3000), fmt))
    assert max(im.size) == MAX_SIDE
    assert im.size == (MAX_SIDE, 768)  # aspect ratio preserved


def test_small_reference_is_not_upscaled():
    im = load_reference_image(_encoded((320, 240)))
    assert im.size == (320, 240)


def test_result_is_rgb_and_detached_from_source_bytes():
    """The decoded image must not hold the source file open: that reference is
    what kept the full-resolution buffer alive for the whole generation call."""
    im = load_reference_image(_encoded((2000, 2000), "PNG", mode="L"))
    assert im.mode == "RGB"
    assert getattr(im, "fp", None) is None


def test_reference_stays_usable_after_load():
    """A bounded reference still round-trips through the JPEG encode that
    ``part_from_pil`` performs when building the request."""
    im = load_reference_image(_encoded((3000, 3000)))
    buf = BytesIO()
    im.save(buf, format="JPEG", quality=90)
    assert Image.open(BytesIO(buf.getvalue())).size == (MAX_SIDE, MAX_SIDE)
