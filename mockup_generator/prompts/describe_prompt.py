"""Fills the product-description prompt shared with retail-inventory.

The prompt, output schema and owner-approved examples are vendored copies (see
``describe/SOURCE.md``); this module is a port of retail-inventory's
``scripts/describe/build_prompt.js`` so both pipelines send the model the same
text for the same product.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

from mockup_generator.prompts.tag_vocab import family_of, prompt_block

# Long or markdown-styled descriptions are earlier AI photo captions (owner: often
# wrong); short ones are the shop's own. Same rule as build_prompt.js.
_AI_CAPTION_MIN_CHARS = 201
_AI_SOURCE = "written earlier by an AI from photos; it may be wrong, so trust the photos over it"
_SHOP_SOURCE = "written by the shop; facts here are true unless a photo clearly contradicts them"


@dataclass(frozen=True)
class ProductFacts:
    productid: str
    name: str
    categoryid: str | None
    category_name: str | None
    fabric: str | None
    colours: list[str]
    sizes: list[str]
    description: str | None


def _read(name: str) -> str:
    return resources.files("mockup_generator.prompts").joinpath("describe", name).read_text("utf-8")


@lru_cache(maxsize=1)
def _template() -> str:
    return _read("prompt.md")


@lru_cache(maxsize=1)
def load_schema() -> dict:
    return json.loads(_read("schema.json"))


@lru_cache(maxsize=1)
def load_examples() -> dict[str, str]:
    return json.loads(_read("approved.json"))


def build_prompt(facts: ProductFacts, owner_note: str | None = None) -> str:
    # Tag values are limited to what the product's category family may use (tag_vocab.json).
    # retail's build_prompt.js throws for a category in no family; Backfill still needs a
    # description for one (e.g. a category added later), so its tags are all "always empty".
    family = family_of(facts.categoryid)
    data = (
        f"name: {facts.name}\ncategory: {facts.category_name}\nfabric: {facts.fabric}\n"
        f"colours: {', '.join(facts.colours)}\nsizes: {', '.join(facts.sizes)}"
    )
    text = facts.description or ""
    ai_written = "**" in text or len(text) >= _AI_CAPTION_MIN_CHARS
    note = (
        f"\nOWNER REVIEW NOTE on the previous draft (follow it):\n{owner_note.strip()}\n"
        if owner_note and owner_note.strip() else ""
    )
    return (
        _template()
        .replace("{DATA}", data)
        .replace("{TEXT_SOURCE}", _AI_SOURCE if ai_written else _SHOP_SOURCE)
        .replace("{TEXT}", text or "(none)")
        .replace("{OWNER_NOTE}", note)
        .replace("{TAGS}", prompt_block(family))
    )
