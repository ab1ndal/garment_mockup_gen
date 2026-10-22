import pytest
from fastapi.testclient import TestClient
from backend.main import app
from backend.auth import CurrentUser, Profile, get_current_user
from backend.deps import get_db
from backend.routers import import_shots as im


@pytest.fixture
def client():
    u = CurrentUser(id="u1", email="a@b.c", role="user",
                    profile=Profile(id="u1", email="a@b.c", role="user", is_active=True))
    app.dependency_overrides[get_current_user] = lambda: u
    app.dependency_overrides[get_db] = lambda: object()
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _clear_source_cache():
    im._SOURCE_CACHE.clear()
    yield
    im._SOURCE_CACHE.clear()


def test_publish_uploads_web_images_and_inserts_one_row(client, monkeypatch):
    calls = {}
    monkeypatch.setattr(im, "_render", lambda fid, params: b"PNG")
    monkeypatch.setattr(im.storage_client, "slugify", lambda c: "red")
    monkeypatch.setattr(im.storage_client, "short_hex", lambda: "abcd1234")
    monkeypatch.setattr(im.productimages_repo, "next_product_shot_order",
                        lambda db, pid: 20)

    def _upload(pid, png, key):
        calls["upload"] = {"png": png, "key": key}
        return "https://pub/red_20_abcd1234.webp"
    monkeypatch.setattr(im.publish, "upload_web_images", _upload)

    def _insert(db, *, productid, imageurl, productcolor, theme, displayorder):
        calls["insert"] = {"theme": theme, "order": displayorder, "url": imageurl,
                           "color": productcolor}
        return {}
    monkeypatch.setattr(im.productimages_repo, "insert", _insert)

    r = client.post("/api/import/publish", json={
        "productid": "P1", "file_id": "f1", "color": "Red", "params": {"brightness": 1.1}})
    assert r.status_code == 200
    body = r.json()
    assert body["image_url"].endswith(".webp") and body["displayorder"] == 20
    # Routed through the shared web-image path so size variants are uploaded too.
    assert calls["upload"]["png"] == b"PNG"
    assert calls["insert"]["theme"] == "Product Shot"
    assert calls["insert"]["order"] == 20


def test_preview_returns_data_uri(client, monkeypatch):
    monkeypatch.setattr(im, "_render", lambda fid, params: b"PNGBYTES")
    r = client.post("/api/import/preview", json={"file_id": "f1", "params": {}})
    assert r.status_code == 200
    assert r.json()["preview"].startswith("data:image/png;base64,")


def test_drive_images_lists_folder(client, monkeypatch):
    monkeypatch.setattr(im.products_repo, "get_product",
                        lambda db, pid: type("P", (), {"producturl": "https://drive/x"})())
    monkeypatch.setattr(im.drive_client, "extract_folder_id", lambda url: "FID")
    monkeypatch.setattr(im.drive_client, "list_folder_image_groups",
                        lambda fid: {"loose": [{"id": "i1", "name": "a.jpg",
                                                "mime_type": "image/jpeg",
                                                "thumbnail_url": "t"}], "groups": []})
    r = client.get("/api/import/products/P1/drive-images")
    assert r.status_code == 200
    assert r.json()["loose"][0]["id"] == "i1"
