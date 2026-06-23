import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.auth import get_current_user, CurrentUser
from backend.deps import get_db
from mockup_generator.db import products_repo
from mockup_generator.db.profiles_repo import Profile


@pytest.fixture
def client(monkeypatch):
    fake_user = CurrentUser(id="u1", email="a@b.c", role="user",
                            profile=Profile(id="u1", email="a@b.c", role="user", is_active=True))
    app.dependency_overrides[get_current_user] = lambda: fake_user
    app.dependency_overrides[get_db] = lambda: object()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_list_products_returns_items(client, monkeypatch):
    sample = [products_repo.Product("BC25001", "Silk-Saree", "SA", "Saree", False, "http://d")]
    monkeypatch.setattr(products_repo, "list_products", lambda *a, **k: sample)
    r = client.get("/api/products?category=SA&pending=true")
    assert r.status_code == 200
    body = r.json()
    assert body[0]["productid"] == "BC25001"
    assert body[0]["category_name"] == "Saree"


def test_get_product_404(client, monkeypatch):
    monkeypatch.setattr(products_repo, "get_product", lambda *a, **k: None)
    r = client.get("/api/products/BC99999")
    assert r.status_code == 404


def test_bad_range_returns_400(client, monkeypatch):
    def boom(*a, **k):
        raise ValueError("invalid product id range")
    monkeypatch.setattr(products_repo, "list_products", boom)
    r = client.get("/api/products?id_start=BC25001&id_end=oops")
    assert r.status_code == 400
