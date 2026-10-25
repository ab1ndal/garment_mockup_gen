from pathlib import Path

import pytest

import shutil
import subprocess

from mockup_generator.prompts.describe_prompt import (
    ProductFacts, build_prompt, load_examples, load_schema,
)
from mockup_generator.prompts.tag_vocab import family_of, prompt_block

_RETAIL = Path(__file__).resolve().parents[2] / "retail-inventory"
_VENDORED = Path(__file__).resolve().parents[1] / "mockup_generator" / "prompts" / "describe"


def _facts(description=None, **kw):
    base = dict(productid="BC25008", name="Georgette Saree", categoryid="SA", category_name="Saree",
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
    for ph in ("{DATA}", "{TEXT}", "{TEXT_SOURCE}", "{OWNER_NOTE}", "{TAGS}"):
        assert ph not in p
    assert "OWNER REVIEW NOTE" not in p


def test_tags_block_uses_category_family():
    p = build_prompt(_facts(categoryid="LE", category_name="Lehenga"))
    assert prompt_block("lehenga") in p
    assert "office" not in prompt_block("lehenga").split("- work:")[0]


def test_unknown_category_raises():
    with pytest.raises(ValueError, match="category XX is in no tag_vocab family"):
        build_prompt(_facts(categoryid="XX"))


def test_family_of():
    assert family_of("SA") == "saree"
    assert family_of("SHT") == "men_western"
    assert family_of("XX") is None
    assert family_of(None) is None


@pytest.mark.parametrize("family", ["saree", "lehenga", "dress_material", "women_ready",
                                    "men_ethnic", "men_western"])
def test_prompt_block_matches_retail_js(family):
    js = _RETAIL / "scripts" / "describe" / "tag_vocab.js"
    if not js.exists() or shutil.which("node") is None:
        pytest.skip("no sibling retail-inventory checkout or node")
    out = subprocess.run(
        ["node", "-e", f"process.stdout.write(require({str(js)!r}).promptBlock({family!r}))"],
        capture_output=True, text=True, check=True).stdout
    assert prompt_block(family) == out


def test_schema_and_examples_load():
    assert "description" in load_schema()["required"]
    assert load_examples()["BC25008"].startswith("Flowing drape")


@pytest.mark.parametrize("name,src", [
    ("prompt.md", "scripts/describe/prompt.md"),
    ("schema.json", "scripts/describe/schema.json"),
    ("approved.json", "tasks/description-pilot/approved.json"),
    ("tag_vocab.json", "scripts/describe/tag_vocab.json"),
])
def test_vendored_matches_retail(name, src):
    retail = _RETAIL / src
    if not retail.exists():
        pytest.skip("no sibling retail-inventory checkout")
    assert (_VENDORED / name).read_bytes() == retail.read_bytes(), (
        f"{name} drifted from retail-inventory; re-copy it (see SOURCE.md)")
