import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.auth import get_current_user, CurrentUser
from mockup_generator.db.profiles_repo import Profile


@pytest.fixture
def client():
    u = CurrentUser(id="u1", email="a@b.c", role="user",
                    profile=Profile(id="u1", email="a@b.c", role="user", is_active=True))
    app.dependency_overrides[get_current_user] = lambda: u
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_generate_image_stub_501(client):
    r = client.post("/api/generate/image", json={"productid": "BC25001", "prompt": "x"})
    assert r.status_code == 501
    assert "Phase 3" in r.json()["detail"]


def test_generate_video_stub_501(client):
    r = client.post("/api/generate/video", json={"productid": "BC25001", "prompt": "x"})
    assert r.status_code == 501
