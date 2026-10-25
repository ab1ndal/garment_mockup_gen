from pathlib import Path

import pytest

from mockup_generator.prompts.describe_prompt import (
    ProductFacts, build_prompt, load_examples, load_schema,
)

_RETAIL = Path(__file__).resolve().parents[2] / "retail-inventory"
_VENDORED = Path(__file__).resolve().parents[1] / "mockup_generator" / "prompts" / "describe"


def _facts(description=None, **kw):
    base = dict(productid="BC25008", name="Georgette Saree", category_name="Saree",
                fabric="Georgette", colours=["Red", "Blue"], sizes=["Free Size"],
                description=description)
    base.update(kw)
    return ProductFacts(**base)


def test_data_block():
    p = build_prompt(_facts())
    assert "name: Georgette Saree\ncategory: Saree\nfabric: Georgette\ncolours: Red, Blue\nsizes: Free Size" in p


def test_text_source_shop():
    p = build_prompt(_facts("Mirror work on yoke"))
    assert "written by the shop; facts here are true unless a photo clearly contradicts them" in p
    assert "Mirror work on yoke" in p


def test_text_source_ai_markdown():
    p = build_prompt(_facts("**Bold** caption"))
    assert "written earlier by an AI from photos; it may be wrong, so trust the photos over it" in p


def test_text_source_ai_long():
    p = build_prompt(_facts("x" * 201))
    assert "written earlier by an AI from photos; it may be wrong, so trust the photos over it" in p


def test_text_source_200_chars_is_shop():
    assert "written by the shop" in build_prompt(_facts("x" * 200))


def test_empty_description():
    assert "(none)" in build_prompt(_facts(None))


def test_owner_note():
    p = build_prompt(_facts(), owner_note="has mirror work on sleeves")
    assert "OWNER REVIEW NOTE on the previous draft (follow it):\nhas mirror work on sleeves" in p


def test_no_placeholders_left():
    p = build_prompt(_facts())
    for ph in ("{DATA}", "{TEXT}", "{TEXT_SOURCE}", "{OWNER_NOTE}"):
        assert ph not in p
    assert "OWNER REVIEW NOTE" not in p


def test_schema_and_examples_load():
    assert "description" in load_schema()["required"]
    assert load_examples()["BC25008"].startswith("Flowing drape")


@pytest.mark.parametrize("name,src", [
    ("prompt.md", "scripts/describe/prompt.md"),
    ("schema.json", "scripts/describe/schema.json"),
    ("approved.json", "tasks/description-pilot/approved.json"),
])
def test_vendored_matches_retail(name, src):
    retail = _RETAIL / src
    if not retail.exists():
        pytest.skip("no sibling retail-inventory checkout")
    assert (_VENDORED / name).read_bytes() == retail.read_bytes(), (
        f"{name} drifted from retail-inventory; re-copy it (see SOURCE.md)")
