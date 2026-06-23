import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.auth import get_current_user, CurrentUser
from backend.deps import get_db
from mockup_generator.db import products_repo
from mockup_generator.db.profiles_repo import Profile
from mockup_generator.integrations import drive_client
from mockup_generator.integrations.drive_client import DriveNotConfigured

_DRIVE_URL = "https://drive.google.com/drive/folders/ROOTID"


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


def _img(i):
    return {"id": i, "name": f"{i}.jpg", "mime_type": "image/jpeg", "thumbnail_url": f"data:thumb:{i}"}


def test_list_product_images_grouped(client, monkeypatch):
    monkeypatch.setattr(products_repo, "get_product",
                        lambda *a, **k: products_repo.Product("BC25001", "Saree", "SA", "Saree", False, _DRIVE_URL))
    grouped = {
        "loose": [_img("a"), _img("b")],
        "groups": [
            {"id": "RED", "name": "Red-variant", "images": [_img("r1"), _img("r2")]},
            {"id": "BLUE", "name": "Blue-variant", "images": [_img("b1")]},
        ],
    }
    monkeypatch.setattr(drive_client, "list_folder_image_groups", lambda fid: grouped)
    r = client.get("/api/products/BC25001/images")
    assert r.status_code == 200
    body = r.json()
    assert [i["id"] for i in body["loose"]] == ["a", "b"]
    assert [g["name"] for g in body["groups"]] == ["Red-variant", "Blue-variant"]
    assert [i["id"] for i in body["groups"][0]["images"]] == ["r1", "r2"]


def test_list_product_images_no_drive_folder_409(client, monkeypatch):
    # producturl with no parseable folder id → 409
    monkeypatch.setattr(products_repo, "get_product",
                        lambda *a, **k: products_repo.Product("BC25001", "Saree", "SA", "Saree", False, None))
    r = client.get("/api/products/BC25001/images")
    assert r.status_code == 409


def test_list_product_images_404_when_product_missing(client, monkeypatch):
    monkeypatch.setattr(products_repo, "get_product", lambda *a, **k: None)
    r = client.get("/api/products/BC99999/images")
    assert r.status_code == 404


def test_list_product_images_drive_not_configured_503(client, monkeypatch):
    monkeypatch.setattr(products_repo, "get_product",
                        lambda *a, **k: products_repo.Product("BC25001", "Saree", "SA", "Saree", False, _DRIVE_URL))

    def boom(fid):
        raise DriveNotConfigured("GOOGLE_DRIVE_SA_JSON is not set")
    monkeypatch.setattr(drive_client, "list_folder_image_groups", boom)
    r = client.get("/api/products/BC25001/images")
    assert r.status_code == 503
