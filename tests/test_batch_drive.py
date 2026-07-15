from mockup_generator.integrations import drive_client


def test_list_folder_image_ids_returns_ids_capped(monkeypatch):
    monkeypatch.setattr(drive_client, "_clients", lambda: (object(), object()))

    # Stateless fake keyed on the folder in the query, so both calls below behave
    # identically: top level = 2 images + 1 subfolder; the subfolder = 1 image.
    def fake_paged(svc, q, fields):
        if "'folderX'" in q:
            return [
                {"id": "i1", "mimeType": "image/png"},
                {"id": "i2", "mimeType": "image/jpeg"},
                {"id": "sub", "mimeType": drive_client._FOLDER_MIME, "name": "Red"},
            ]
        return [{"id": "i3", "mimeType": "image/png"}]

    monkeypatch.setattr(drive_client, "_paged_files", fake_paged)
    ids = drive_client.list_folder_image_ids("folderX", limit=14)
    assert ids == ["i1", "i2", "i3"]
    capped = drive_client.list_folder_image_ids("folderX", limit=2)
    assert len(capped) == 2
