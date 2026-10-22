# tests/test_endpoints_dont_block.py
"""Slow endpoints must not stall the event loop.

An ``async def`` route runs on the event loop thread, so any blocking call in it
(image encoding, a publish, a Gemini call) freezes every other request until it
returns. Each test parks the slow call until a concurrent cheap request has been
answered; if the route blocks the loop, that request can't be answered and the
park times out.
"""
import asyncio
import threading
from io import BytesIO

import httpx
import pytest
from PIL import Image

from backend.main import app
from backend.auth import get_current_user, CurrentUser
from backend.deps import get_db
from backend.routers import generate as gen
from mockup_generator.db.profiles_repo import Profile

_PARK_TIMEOUT_S = 3


def _png_bytes() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (4, 4), (9, 9, 9)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _auth():
    u = CurrentUser(id="u1", email="a@b.c", role="user",
                    profile=Profile(id="u1", email="a@b.c", role="user", is_active=True))
    app.dependency_overrides[get_current_user] = lambda: u
    app.dependency_overrides[get_db] = lambda: object()
    yield
    app.dependency_overrides.clear()


def _run_with_cheap_request(slow_request) -> None:
    """Fire ``slow_request`` and then a cheap request on one event loop.

    ASGITransport skips lifespan, so no real Supabase is hit."""
    async def run() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            async def cheap():
                r = await c.get("/api/generate/options")
                assert r.status_code == 200
                answered.set()

            slow = asyncio.create_task(slow_request(c))
            await asyncio.sleep(0)  # let the slow request start first
            await asyncio.gather(slow, cheap())

    asyncio.run(run())


answered = threading.Event()


@pytest.fixture(autouse=True)
def _reset_answered():
    answered.clear()


def test_approve_does_not_block_other_requests(monkeypatch):
    saw_cheap = []

    def slow_publish(db, **kw):
        saw_cheap.append(answered.wait(_PARK_TIMEOUT_S))
        return {"image_url": "https://public/x.png", "variation_id": 1}

    monkeypatch.setattr(gen.publish, "publish_image", slow_publish)

    async def approve(c):
        r = await c.post("/api/generate/approve",
                         data={"productid": "BC25001", "source": "generated"},
                         files={"image": ("m.png", _png_bytes(), "image/png")})
        assert r.status_code == 200

    _run_with_cheap_request(approve)
    assert saw_cheap == [True]


def test_image_upload_does_not_block_other_requests(monkeypatch):
    saw_cheap = []

    def slow_generate(images, prompt, **kw):
        saw_cheap.append(answered.wait(_PARK_TIMEOUT_S))
        return _png_bytes()

    monkeypatch.setattr(gen.service, "generate_mockup_bytes", slow_generate)

    async def upload(c):
        r = await c.post("/api/generate/image/upload",
                         data={"prompt": "a saree"},
                         files={"files": ("a.png", _png_bytes(), "image/png")})
        assert r.status_code == 200

    _run_with_cheap_request(upload)
    assert saw_cheap == [True]
