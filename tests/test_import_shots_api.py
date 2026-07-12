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


def test_publish_uploads_webp_only_and_inserts_one_row(client, monkeypatch):
    calls = {}
    monkeypatch.setattr(im.drive_client, "download_file", lambda fid: b"SRC")
    monkeypatch.setattr(im.edit_pipeline, "apply_edits", lambda src, params: b"PNG")
    monkeypatch.setattr(im.publish, "_encode_webp", lambda png: b"WEBP")
    monkeypatch.setattr(im.storage_client, "slugify", lambda c: "red")
    monkeypatch.setattr(im.storage_client, "short_hex", lambda: "abcd1234")
    monkeypatch.setattr(im.productimages_repo, "next_product_shot_order",
                        lambda db, pid: 20)

    def _upload(pid, data, key, *, ext, content_type):
        calls["upload"] = {"data": data, "ext": ext, "content_type": content_type, "key": key}
        return (f"{pid}/{key}.{ext}", "https://pub/red_20_abcd1234.webp")
    monkeypatch.setattr(im.storage_client, "upload_mockup", _upload)

    def _insert(db, *, productid, imageurl, productcolor, theme, displayorder):
        calls["insert"] = {"theme": theme, "order": displayorder, "url": imageurl,
                           "color": productcolor}
        return {}
    monkeypatch.setattr(im.productimages_repo, "insert", _insert)

    r = client.post("/api/import/publish", json={
        "productid": "P1", "file_id": "f1", "color": "Red", "params": {"bg": "white"}})
    assert r.status_code == 200
    body = r.json()
    assert body["image_url"].endswith(".webp") and body["displayorder"] == 20
    assert calls["upload"]["ext"] == "webp"
    assert calls["upload"]["content_type"] == "image/webp"
    assert calls["upload"]["data"] == b"WEBP"
    assert calls["insert"]["theme"] == "Product Shot"
    assert calls["insert"]["order"] == 20


def test_preview_returns_data_uri(client, monkeypatch):
    monkeypatch.setattr(im.drive_client, "download_file", lambda fid: b"SRC")
    monkeypatch.setattr(im.edit_pipeline, "apply_edits", lambda src, params: b"PNGBYTES")
    r = client.post("/api/import/preview", json={"file_id": "f1", "params": {}})
    assert r.status_code == 200
    assert r.json()["preview"].startswith("data:image/png;base64,")


def test_preview_503_when_bg_unavailable(client, monkeypatch):
    monkeypatch.setattr(im.drive_client, "download_file", lambda fid: b"SRC")
    def _boom(src, params):
        raise im.edit_pipeline.BackgroundRemovalUnavailable("no model")
    monkeypatch.setattr(im.edit_pipeline, "apply_edits", _boom)
    r = client.post("/api/import/preview", json={"file_id": "f1", "params": {}})
    assert r.status_code == 503


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
