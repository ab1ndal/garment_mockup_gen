import base64
import json
from io import BytesIO

import pytest
from anthropic.types import Message, TextBlock, Usage
from PIL import Image

from mockup_generator.config import settings
from mockup_generator.describe import draft as d
from mockup_generator.prompts.describe_prompt import ProductFacts, load_schema

_FACTS = ProductFacts("BC25008", "Georgette Saree", "SA", "Saree", "Georgette", ["Red"], ["Free Size"], None)
_OK = {"description": "Flowing drape with a resham jaal.", "includes": "saree", "work": [],
       "print_weave": [], "silhouette": "", "length": "", "neck": "", "sleeve": "",
       "occasion": [], "uncertain": ["border width"], "conflicts": []}


def _img(w=40, h=30, fmt="PNG") -> bytes:
    buf = BytesIO()
    Image.new("RGB", (w, h), (200, 10, 10)).save(buf, format=fmt)
    return buf.getvalue()


def _msg(text, stop_reason="end_turn"):
    return Message.model_construct(
        id="msg_1", type="message", role="assistant", model="claude-opus-5-5",
        content=[TextBlock(type="text", text=text)], stop_reason=stop_reason,
        stop_sequence=None, usage=Usage(input_tokens=1, output_tokens=1))


class FakeClient:
    def __init__(self, reply):
        self.calls, self._reply = [], reply
        self.messages = self
    def create(self, **kw):
        self.calls.append(kw)
        return self._reply


def _blocks(call):
    return call["messages"][0]["content"]


def test_request_shape():
    c = FakeClient(_msg(json.dumps(_OK)))
    d.draft_description(_FACTS, [_img(), _img()], _img(), client=c)
    call = c.calls[0]
    assert call["model"] == settings.description_model
    assert call["output_config"]["effort"] == "medium"
    fmt = call["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"] == load_schema()
    kinds = [(b["type"], b.get("text")) for b in _blocks(call)]
    assert kinds[:6] == [("text", "orig_1"), ("image", None), ("text", "orig_2"), ("image", None),
                         ("text", "mockup_1"), ("image", None)]
    assert kinds[-1][0] == "text" and "Georgette Saree" in kinds[-1][1]
    assert "Match the shape and level of detail of these approved descriptions; never copy their facts." in call["system"]
    assert "Flowing drape with a golden Swarovski border" in call["system"]


def test_owner_note_reaches_prompt():
    c = FakeClient(_msg(json.dumps(_OK)))
    d.draft_description(_FACTS, [_img()], None, owner_note="has mirror work", client=c)
    assert "has mirror work" in _blocks(c.calls[0])[-1]["text"]


def test_unknown_category_fails_draft():
    facts = ProductFacts("BC1", "X", "XX", "X", None, [], [], None)
    with pytest.raises(d.DraftFailed, match="no tag_vocab family"):
        d.draft_description(facts, [_img()], None, client=FakeClient(_msg(json.dumps(_OK))))


def test_parses_draft():
    out = d.draft_description(_FACTS, [_img()], None, client=FakeClient(_msg(json.dumps(_OK))))
    assert out == d.Draft("Flowing drape with a resham jaal.", "saree", ["border width"], [])


@pytest.mark.parametrize("reply", [
    _msg(json.dumps(_OK), stop_reason="refusal"),
    _msg(json.dumps(_OK), stop_reason="max_tokens"),
    _msg("not json"),
    _msg(json.dumps({**_OK, "description": "x" * 601})),
], ids=["refusal", "max_tokens", "invalid_json", "over_cap"])
def test_bad_replies_raise(reply):
    with pytest.raises(d.DraftFailed):
        d.draft_description(_FACTS, [_img()], None, client=FakeClient(reply))


def test_no_images_raises():
    with pytest.raises(d.DraftFailed, match="no images"):
        d.draft_description(_FACTS, [], None, client=FakeClient(_msg(json.dumps(_OK))))


def test_at_most_three_originals():
    c = FakeClient(_msg(json.dumps(_OK)))
    d.draft_description(_FACTS, [_img()] * 5, None, client=c)
    assert sum(b["type"] == "image" for b in _blocks(c.calls[0])) == 3


def test_prepare_image_resizes_large():
    out = Image.open(BytesIO(base64.b64decode(d.prepare_image(_img(6000, 4000)))))
    assert out.format == "JPEG"
    assert max(out.size) == 1568


def test_prepare_image_keeps_small_and_converts_rgba():
    buf = BytesIO()
    Image.new("RGBA", (50, 20), (0, 0, 0, 0)).save(buf, format="PNG")
    out = Image.open(BytesIO(base64.b64decode(d.prepare_image(buf.getvalue()))))
    assert out.size == (50, 20) and out.mode == "RGB"


def test_api_error_raises_draft_failed():
    import anthropic
    import httpx2

    class Boom(FakeClient):
        def create(self, **kw):
            req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
            raise anthropic.InternalServerError(
                "overloaded", response=httpx2.Response(529, request=req), body=None)
    with pytest.raises(d.DraftFailed, match="Claude API error"):
        d.draft_description(_FACTS, [_img()], None, client=Boom(None))


def test_unreadable_image_raises_draft_failed():
    with pytest.raises(d.DraftFailed, match="could not read image orig_2"):
        d.draft_description(_FACTS, [_img(), b"not an image"], None,
                            client=FakeClient(_msg(json.dumps(_OK))))


def test_empty_model_draft_raises():
    with pytest.raises(d.DraftFailed, match="empty"):
        d.draft_description(_FACTS, [_img()], None,
                            client=FakeClient(_msg(json.dumps({**_OK, "description": "  "}))))
