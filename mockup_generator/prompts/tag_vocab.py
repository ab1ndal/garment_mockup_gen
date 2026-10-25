"""Owner-approved tag vocabulary as lookups for the description prompt.

Port of retail-inventory's ``scripts/describe/tag_vocab.js`` (``familyOf``,
``allowed``, ``promptBlock``) over the vendored ``describe/tag_vocab.json``;
``tests/test_describe_prompt.py`` checks the output matches the JS.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources

SINGLE = ("silhouette", "length", "neck", "sleeve")


@lru_cache(maxsize=1)
def _vocab() -> dict:
    return json.loads(
        resources.files("mockup_generator.prompts").joinpath("describe", "tag_vocab.json").read_text("utf-8"))


def family_of(categoryid: str | None) -> str | None:
    families = _vocab()["families"]
    return next((f for f, spec in families.items() if categoryid in spec["categories"]), None)


def allowed(group: str, family: str | None) -> list[str]:
    """Values of one group allowed for one family; empty when the group does not apply to it."""
    g = _vocab()["groups"][group]
    if not family or family not in g["families"]:
        return []
    return [v["value"] for v in g["values"] if family in v.get("families", g["families"])]


def prompt_block(family: str) -> str:
    """The TAGS block of prompt.md for one family."""
    def line(group: str, how: str) -> str:
        vals = allowed(group, family)
        return (f"- {group}: {how} from [{', '.join(vals)}]" if vals
                else f"- {group}: always empty for this product")
    return "\n".join([
        "TAGS: use only the values listed below, spelled exactly. A value not in the list is not allowed; leave it out instead.",
        line("occasion", "every occasion the piece clearly suits (no fixed limit), only"),
        line("work", '"type" is the technique only, one per item,'),
        line("print_weave", "every print or weave that applies,"),
        *(line(k, 'one value, or "" if not visible or unclear,') for k in SINGLE),
        'Work: put where it sits in "placement" (free text, it is not a tag); each work item carries confidence 0–1. '
        'Use "festive" when the piece suits festivals (there is no separate festival tag). '
        'If a print or weave was named as work, list it under print_weave instead.',
    ])
