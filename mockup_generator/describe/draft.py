"""Draft a product description with Claude from the product's photos.

Uses the prompt shared with retail-inventory (``prompts/describe_prompt``) and its
output schema. Stateless: returns a draft, writes nothing.
"""

from __future__ import annotations

import base64
import copy
import json
import logging
from dataclasses import dataclass
from io import BytesIO

import anthropic
from PIL import Image

from mockup_generator.config import settings
from mockup_generator.prompts.describe_prompt import (
    ProductFacts, build_prompt, load_examples, load_schema,
)

log = logging.getLogger(__name__)

MAX_ORIGINALS = 3
MAX_DESCRIPTION_CHARS = 600
_MAX_EDGE = 1568          # larger images are downscaled by the API anyway; send less
_JPEG_QUALITY = 85
_MAX_TOKENS = 16000
_EFFORT = "medium"
# Structured outputs reject some JSON Schema keywords the shared schema uses.
_UNSUPPORTED_SCHEMA_KEYS = ("maxItems",)

_STYLE_INTRO = (
    "Below are product descriptions the shop owner approved. "
    "Match the shape and level of detail of these approved descriptions; never copy their facts."
)


@dataclass
class Draft:
    description: str
    includes: str
    uncertain: list[str]
    conflicts: list[str]


class DraftFailed(RuntimeError):
    """The model returned no usable draft."""


def prepare_image(raw: bytes) -> str:
    """Base64 JPEG, RGB, long edge at most ``_MAX_EDGE``."""
    with Image.open(BytesIO(raw)) as img:
        img = img.convert("RGB")
        img.thumbnail((_MAX_EDGE, _MAX_EDGE))
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=_JPEG_QUALITY)
    return base64.standard_b64encode(buf.getvalue()).decode("ascii")


def _api_schema() -> dict:
    def strip(node):
        if isinstance(node, dict):
            return {k: strip(v) for k, v in node.items() if k not in _UNSUPPORTED_SCHEMA_KEYS}
        if isinstance(node, list):
            return [strip(v) for v in node]
        return node
    return strip(copy.deepcopy(load_schema()))


def _system() -> str:
    examples = "\n\n".join(load_examples().values())
    return f"{_STYLE_INTRO}\n\n{examples}"


def _image_block(raw: bytes) -> dict:
    return {"type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": prepare_image(raw)}}


def _content(facts: ProductFacts, originals: list[bytes], mockup: bytes | None,
             owner_note: str | None) -> list[dict]:
    blocks: list[dict] = []
    for i, raw in enumerate(originals[:MAX_ORIGINALS], start=1):
        blocks += [{"type": "text", "text": f"orig_{i}"}, _image_block(raw)]
    if mockup is not None:
        blocks += [{"type": "text", "text": "mockup_1"}, _image_block(mockup)]
    blocks.append({"type": "text", "text": build_prompt(facts, owner_note)})
    return blocks


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=4, timeout=90.0)


def draft_description(facts: ProductFacts, originals: list[bytes], mockup: bytes | None,
                      owner_note: str | None = None, *,
                      client: anthropic.Anthropic | None = None) -> Draft:
    if not originals and mockup is None:
        raise DraftFailed("no images to draft from")
    try:
        resp = (client or _client()).messages.create(
            model=settings.description_model,
            max_tokens=_MAX_TOKENS,
            system=_system(),
            output_config={"effort": _EFFORT, "format": {"type": "json_schema", "schema": _api_schema()}},
            messages=[{"role": "user", "content": _content(facts, originals, mockup, owner_note)}],
        )
    except anthropic.APIError as exc:  # the SDK has already retried 429/5xx
        log.warning("description draft for %s: Claude API error: %s", facts.productid, exc)
        raise DraftFailed(f"Claude API error: {exc}") from exc
    if resp.stop_reason != "end_turn":
        log.warning("description draft for %s stopped with %s (request %s)",
                    facts.productid, resp.stop_reason, getattr(resp, "_request_id", None))
        raise DraftFailed(f"model stopped with {resp.stop_reason}")
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DraftFailed("model returned invalid JSON") from exc
    description = (data.get("description") or "").strip()
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise DraftFailed(f"draft is {len(description)} chars, over {MAX_DESCRIPTION_CHARS}")
    return Draft(description=description, includes=(data.get("includes") or "").strip(),
                 uncertain=list(data.get("uncertain") or []), conflicts=list(data.get("conflicts") or []))
