# Phase 7 — Backfill Review Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a "Backfill" tab that walks the ~1–2k previously-generated Drive mockups one by one, lets a reviewer assign color/theme/aspect, then either publishes to Supabase (and deletes the Drive original) or flags the product for regeneration (and moves the Drive original to `rejected/`).

**Architecture:** Backend scans the generated Drive folder-of-folders once into an in-memory flat index (TTL 300s + manual refresh) and serves paginated review cards; originals load lazily per card. Approve reuses a shared `publish_image` path (extracted from the existing Phase 3 `/generate/approve`); both approve and flag mutate Drive so the folder is the worklist. React tab renders cards + a split review panel.

**Tech Stack:** FastAPI, Supabase (supabase-py), Google Drive API (service account), Pillow, React + TypeScript (Vite), pytest.

**Spec:** `docs/superpowers/specs/2026-06-24-phase7-backfill-review-design.md`

## Global Constraints

- Python `>=3.10,<3.11`; backend deps via Poetry.
- Drive folder-of-folders root id: `1FBDw_F40zDt4zvp6el3Ei50Nw8cOahm4` (config default).
- Filename forms: `<productid>.png`, `<productid><alpha>.png`, `<productid>_<alpha>.png`; productid is `BC` + digits (`^BC\d+$`).
- Generated images publish under `{productid}/{colorslug}_{hex}.png` in the **public** `mockups` Storage bucket (unchanged from Phase 3).
- One `productimages` row per `(productid, color, phototheme)`; `phototheme` = label, plus `·<aspect>` suffix for non-1:1 (unchanged).
- All new endpoints sit behind the existing active-profile auth dependency (`get_current_user`).
- Run backend tests with `poetry run python -m pytest -q`; frontend build with `npm run build` (in `frontend/`).
- Commit after each task. End commit messages with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## File Structure

**Backend**
- `docs/migrations/2026-06-24-backfill.sql` (new) — make `mockup_variations.prompt_text` nullable.
- `mockup_generator/config.py` (modify) — add `generated_mockups_folder_id`.
- `mockup_generator/integrations/drive_client.py` (modify) — write scope; `parse_generated_name`, `scan_folder_of_folders`, `delete_file`, `move_file`, `ensure_subfolder`, `thumbnails_for`.
- `mockup_generator/generation/publish.py` (new) — `build_photo_theme`, `publish_image` (extracted from `generate.py`).
- `backend/routers/generate.py` (modify) — rewire `approve_mockup` to call `publish_image`.
- `mockup_generator/services/backfill_service.py` (new) — in-memory index, `get_index`, `paginate`, `evict`.
- `backend/routers/backfill.py` (new) — `items`, `{file_id}/sources`, `approve`, `flag`.
- `backend/schemas.py` (modify) — backfill request/response models.
- `backend/main.py` (modify) — register the backfill router.

**Frontend**
- `frontend/src/api.ts` (modify) — typed wrappers + interfaces.
- `frontend/src/components/BackfillTab.tsx` (new) — card grid + review panel.
- `frontend/src/App.tsx` (modify) — add the "Backfill" tab.

**Tests**
- `tests/test_backfill_drive.py`, `tests/test_publish_image.py`, `tests/test_backfill_service.py`, `tests/test_backfill_api.py` (new).
- `tests/test_approve_publish.py` (existing — must stay green after Task 5).

---

## Task 1: Infra — migration, config setting, Drive write scope

**Files:**
- Create: `docs/migrations/2026-06-24-backfill.sql`
- Modify: `mockup_generator/config.py`
- Modify: `mockup_generator/integrations/drive_client.py:35`
- Test: `tests/test_backfill_drive.py`

**Interfaces:**
- Produces: `settings.generated_mockups_folder_id -> str`; `drive_client._SCOPES` includes `https://www.googleapis.com/auth/drive`.

- [ ] **Step 1: Write the migration file**

Create `docs/migrations/2026-06-24-backfill.sql`:

```sql
-- Phase 7 backfill: backfilled images have no generation prompt.
-- Make prompt_text nullable so the audit row can record provenance
-- (who/when/source path) without a fabricated prompt.
alter table public.mockup_variations alter column prompt_text drop not null;
```

- [ ] **Step 2: Write the failing test**

Add to `tests/test_backfill_drive.py`:

```python
from mockup_generator.config import settings
from mockup_generator.integrations import drive_client


def test_generated_folder_id_default():
    assert settings.generated_mockups_folder_id == "1FBDw_F40zDt4zvp6el3Ei50Nw8cOahm4"


def test_drive_scope_is_read_write():
    assert "https://www.googleapis.com/auth/drive" in drive_client._SCOPES
```

- [ ] **Step 3: Run test to verify it fails**

Run: `poetry run python -m pytest tests/test_backfill_drive.py -q`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'generated_mockups_folder_id'`.

- [ ] **Step 4: Add the config property**

In `mockup_generator/config.py`, add inside `class Settings` (after `google_drive_sa_json`):

```python
    @property
    def generated_mockups_folder_id(self) -> str:
        """Drive folder-of-folders holding previously-generated mockups to backfill."""
        return _get(
            "GENERATED_MOCKUPS_FOLDER_ID",
            default="1FBDw_F40zDt4zvp6el3Ei50Nw8cOahm4",
        )  # type: ignore[return-value]
```

- [ ] **Step 5: Broaden the Drive scope**

In `mockup_generator/integrations/drive_client.py`, change line 35 from
`_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]` to:

```python
_SCOPES = ["https://www.googleapis.com/auth/drive"]  # read + write: backfill deletes/moves files
```

- [ ] **Step 6: Run test to verify it passes**

Run: `poetry run python -m pytest tests/test_backfill_drive.py -q`
Expected: PASS (2 tests).

- [ ] **Step 7: Apply the migration**

Apply `docs/migrations/2026-06-24-backfill.sql` via the Supabase MCP `apply_migration` tool (name `backfill_prompt_text_nullable`). Confirm `mockup_variations.prompt_text` is now nullable.

- [ ] **Step 8: Commit**

```bash
git add docs/migrations/2026-06-24-backfill.sql mockup_generator/config.py mockup_generator/integrations/drive_client.py tests/test_backfill_drive.py
git commit -m "feat(backfill): config folder id, drive write scope, nullable prompt_text migration"
```

---

## Task 2: Filename parser `parse_generated_name`

**Files:**
- Modify: `mockup_generator/integrations/drive_client.py`
- Test: `tests/test_backfill_drive.py`

**Interfaces:**
- Produces: `parse_generated_name(name: str) -> tuple[str | None, str | None]` — `(productid, alpha)`; `(None, None)` when the stem is not `BC<digits>` optionally followed by letters.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_backfill_drive.py`:

```python
import pytest


@pytest.mark.parametrize("name,expected", [
    ("BC25123.png", ("BC25123", None)),
    ("BC25123A.png", ("BC25123", "A")),
    ("BC25123_A.png", ("BC25123", "A")),
    ("BC25123_a.png", ("BC25123", "A")),      # alpha upper-cased
    ("BC25123AB.png", ("BC25123", "AB")),
    ("BC1234.jpg", ("BC1234", None)),
    ("BC25123.v2.png", (None, None)),          # extra dots -> malformed
    ("IMG_001.png", (None, None)),             # non-BC -> malformed
    ("notes.txt", (None, None)),
])
def test_parse_generated_name(name, expected):
    assert drive_client.parse_generated_name(name) == expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run python -m pytest tests/test_backfill_drive.py::test_parse_generated_name -q`
Expected: FAIL — `AttributeError: module ... has no attribute 'parse_generated_name'`.

- [ ] **Step 3: Implement the parser**

In `mockup_generator/integrations/drive_client.py`, near the top (after `_FOLDER_ID_RE`), add:

```python
# Generated mockup filenames: "<productid>", "<productid><alpha>", "<productid>_<alpha>".
# productid is "BC" + digits; the greedy \d+ stops at the first letter.
_GEN_NAME_RE = re.compile(r"^(BC\d+)_?([A-Za-z]+)?$")
_IMG_EXT_RE = re.compile(r"\.(png|jpe?g|webp)$", re.IGNORECASE)


def parse_generated_name(name: str) -> tuple[str | None, str | None]:
    """Split a generated filename into (productid, alpha).

    Returns (None, None) for any stem that isn't a bare ``BC<digits>`` optionally
    followed by an attached or underscore-separated alpha suffix. The alpha is
    upper-cased. An image extension is stripped first; any other dot makes the
    name malformed.
    """
    stem = _IMG_EXT_RE.sub("", (name or "").strip())
    m = _GEN_NAME_RE.match(stem)
    if not m:
        return None, None
    alpha = m.group(2)
    return m.group(1), (alpha.upper() if alpha else None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run python -m pytest tests/test_backfill_drive.py::test_parse_generated_name -q`
Expected: PASS (9 parametrized cases).

- [ ] **Step 5: Commit**

```bash
git add mockup_generator/integrations/drive_client.py tests/test_backfill_drive.py
git commit -m "feat(backfill): parse_generated_name for productid/alpha extraction"
```

---

## Task 3: Drive mutations — delete, move, ensure_subfolder

**Files:**
- Modify: `mockup_generator/integrations/drive_client.py`
- Test: `tests/test_backfill_drive.py`

**Interfaces:**
- Produces:
  - `delete_file(file_id: str) -> None`
  - `move_file(file_id: str, new_parent_id: str) -> None`
  - `ensure_subfolder(parent_id: str, name: str) -> str` (returns child folder id; creates if absent)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_backfill_drive.py`:

```python
class _RecordingFiles:
    """Records the Drive files() calls the mutation helpers make."""

    def __init__(self, list_result=None):
        self.calls = []
        self._list_result = list_result or {"files": []}

    def delete(self, **kw):
        self.calls.append(("delete", kw))
        return self

    def get(self, **kw):
        self.calls.append(("get", kw))
        self._last = ("get", kw)
        return self

    def update(self, **kw):
        self.calls.append(("update", kw))
        return self

    def list(self, **kw):
        self.calls.append(("list", kw))
        self._last = ("list", kw)
        return self

    def create(self, **kw):
        self.calls.append(("create", kw))
        self._last = ("create", kw)
        return self

    def execute(self):
        kind = self._last[0] if hasattr(self, "_last") else None
        if kind == "get":
            return {"parents": ["OLD_PARENT"]}
        if kind == "list":
            return self._list_result
        if kind == "create":
            return {"id": "NEW_FOLDER"}
        return {}


def _patch_files(monkeypatch, files):
    svc = type("Svc", (), {"files": lambda self: files})()
    monkeypatch.setattr(drive_client, "_clients", lambda: (svc, object()))
    return files


def test_delete_file(monkeypatch):
    files = _patch_files(monkeypatch, _RecordingFiles())
    drive_client.delete_file("F1")
    assert ("delete", {"fileId": "F1", "supportsAllDrives": True}) in files.calls


def test_move_file_swaps_parents(monkeypatch):
    files = _patch_files(monkeypatch, _RecordingFiles())
    drive_client.move_file("F1", "REJECTED")
    update = next(kw for name, kw in files.calls if name == "update")
    assert update["fileId"] == "F1"
    assert update["addParents"] == "REJECTED"
    assert update["removeParents"] == "OLD_PARENT"
    assert update["supportsAllDrives"] is True


def test_ensure_subfolder_returns_existing(monkeypatch):
    files = _patch_files(monkeypatch, _RecordingFiles(
        list_result={"files": [{"id": "EXISTING", "name": "rejected"}]}))
    assert drive_client.ensure_subfolder("ROOT", "rejected") == "EXISTING"
    assert not any(name == "create" for name, _ in files.calls)


def test_ensure_subfolder_creates_when_absent(monkeypatch):
    files = _patch_files(monkeypatch, _RecordingFiles(list_result={"files": []}))
    assert drive_client.ensure_subfolder("ROOT", "rejected") == "NEW_FOLDER"
    assert any(name == "create" for name, _ in files.calls)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run python -m pytest tests/test_backfill_drive.py -k "delete_file or move_file or ensure_subfolder" -q`
Expected: FAIL — helpers not defined.

- [ ] **Step 3: Implement the mutation helpers**

In `mockup_generator/integrations/drive_client.py`, add after `download_file`:

```python
def delete_file(file_id: str) -> None:
    """Permanently delete a Drive file (used after a backfill image is published)."""
    svc, _ = _clients()
    svc.files().delete(fileId=file_id, supportsAllDrives=True).execute()


def move_file(file_id: str, new_parent_id: str) -> None:
    """Move a file by swapping its parent to ``new_parent_id`` (flag → rejected/)."""
    svc, _ = _clients()
    meta = svc.files().get(fileId=file_id, fields="parents",
                           supportsAllDrives=True).execute()
    old_parents = ",".join(meta.get("parents", []))
    svc.files().update(
        fileId=file_id, addParents=new_parent_id, removeParents=old_parents,
        fields="id,parents", supportsAllDrives=True,
    ).execute()


def ensure_subfolder(parent_id: str, name: str) -> str:
    """Return the id of the child folder ``name`` under ``parent_id``, creating it
    if absent. Used once to resolve the root-level ``rejected/`` folder."""
    svc, _ = _clients()
    resp = (
        svc.files()
        .list(
            q=(f"'{parent_id}' in parents and name = '{name}' and "
               f"mimeType = '{_FOLDER_MIME}' and trashed = false"),
            fields="files(id,name)", pageSize=1,
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        )
        .execute()
    )
    existing = resp.get("files", [])
    if existing:
        return existing[0]["id"]
    created = svc.files().create(
        body={"name": name, "mimeType": _FOLDER_MIME, "parents": [parent_id]},
        fields="id", supportsAllDrives=True,
    ).execute()
    return created["id"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run python -m pytest tests/test_backfill_drive.py -k "delete_file or move_file or ensure_subfolder" -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add mockup_generator/integrations/drive_client.py tests/test_backfill_drive.py
git commit -m "feat(backfill): drive delete_file / move_file / ensure_subfolder"
```

---

## Task 4: Drive scan — `scan_folder_of_folders` + `thumbnails_for`

**Files:**
- Modify: `mockup_generator/integrations/drive_client.py`
- Test: `tests/test_backfill_drive.py`

**Interfaces:**
- Consumes: `parse_generated_name` (Task 2), `_attach_thumbnails` (existing).
- Produces:
  - `scan_folder_of_folders(root_id: str) -> list[dict]` — flat list of `{productid, alpha, file_id, name, subfolder_id, subfolder_name, thumbnail_link}` (one entry per image, across loose + every subfolder; malformed names included with `productid=None`).
  - `thumbnails_for(items: list[dict]) -> dict[str, str]` — `{file_id: data_uri}`, fetched in parallel for a page of items (each item needs `file_id` + `thumbnail_link`).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_backfill_drive.py` (reuses the `_FakeSvc`/`_file`/`_patch` helpers already in the file from `test_drive_client.py` style — define local copies here):

```python
_FOLDER = drive_client._FOLDER_MIME


def _named(fid, name, *, folder=False):
    return {"id": fid, "name": name,
            "mimeType": _FOLDER if folder else "image/png",
            "thumbnailLink": None if folder else f"link-{fid}"}


class _ScanFiles:
    def __init__(self, responses):
        self.responses = responses
        self._q = ""

    def list(self, *, q, **kw):
        self._q = q
        return self

    def execute(self):
        for folder_id, files in self.responses.items():
            if f"'{folder_id}' in parents" in self._q:
                return {"files": files}
        return {"files": []}


def _patch_scan(monkeypatch, responses):
    svc = type("Svc", (), {"files": lambda self: _ScanFiles(responses)})()
    monkeypatch.setattr(drive_client, "_clients", lambda: (svc, object()))


def test_scan_folder_of_folders_flattens(monkeypatch):
    responses = {
        "ROOT": [_named("a", "BC25001.png"),
                 _named("S1", "group1", folder=True),
                 _named("S2", "group2", folder=True)],
        "S1": [_named("b", "BC25002.png"), _named("c", "BC25002_A.png")],
        "S2": [_named("d", "weird-name.png")],
    }
    _patch_scan(monkeypatch, responses)

    out = drive_client.scan_folder_of_folders("ROOT")
    by_id = {i["file_id"]: i for i in out}

    assert by_id["a"]["productid"] == "BC25001" and by_id["a"]["subfolder_name"] is None
    assert by_id["b"]["productid"] == "BC25002" and by_id["b"]["subfolder_name"] == "group1"
    assert by_id["c"]["alpha"] == "A"
    assert by_id["d"]["productid"] is None          # malformed name still listed
    assert by_id["a"]["thumbnail_link"] == "link-a"
    assert len(out) == 4


def test_thumbnails_for_uses_attach(monkeypatch):
    monkeypatch.setattr(drive_client, "_clients", lambda: (object(), object()))
    monkeypatch.setattr(drive_client, "_attach_thumbnails",
                        lambda session, files: {f["id"]: {"thumbnail_url": f"data:{f['id']}"} for f in files})
    out = drive_client.thumbnails_for([
        {"file_id": "a", "thumbnail_link": "link-a"},
        {"file_id": "b", "thumbnail_link": "link-b"},
    ])
    assert out == {"a": "data:a", "b": "data:b"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run python -m pytest tests/test_backfill_drive.py -k "scan_folder or thumbnails_for" -q`
Expected: FAIL — functions not defined.

- [ ] **Step 3: Implement scan + thumbnails_for**

In `mockup_generator/integrations/drive_client.py`, add:

```python
def _paged_files(svc, q: str, fields: str) -> list[dict]:
    """List all files matching ``q``, following nextPageToken (bounds large folders)."""
    out: list[dict] = []
    token = None
    while True:
        resp = (
            svc.files()
            .list(q=q, fields=f"nextPageToken,{fields}", pageSize=_MAX_FILES,
                  pageToken=token, orderBy="folder,name_natural",
                  supportsAllDrives=True, includeItemsFromAllDrives=True)
            .execute()
        )
        out.extend(resp.get("files", []))
        token = resp.get("nextPageToken")
        if not token:
            return out


def _scan_item(f: dict, subfolder_id: str | None, subfolder_name: str | None) -> dict:
    productid, alpha = parse_generated_name(f.get("name", ""))
    return {
        "productid": productid, "alpha": alpha, "file_id": f["id"],
        "name": f.get("name", f["id"]),
        "subfolder_id": subfolder_id, "subfolder_name": subfolder_name,
        "thumbnail_link": f.get("thumbnailLink"),
    }


def scan_folder_of_folders(root_id: str) -> list[dict]:
    """Flat list of every generated image under ``root_id`` (loose + one level of
    subfolders). Malformed filenames are included with ``productid=None`` so the
    UI can surface them. No thumbnails fetched here (cheap metadata only)."""
    svc, _ = _clients()
    top = _paged_files(
        svc, f"'{root_id}' in parents and trashed = false",
        "files(id,name,mimeType,thumbnailLink)",
    )
    items: list[dict] = []
    subfolders: list[dict] = []
    for f in top:
        if f.get("mimeType") == _FOLDER_MIME:
            subfolders.append(f)
        elif (f.get("mimeType") or "").startswith("image/"):
            items.append(_scan_item(f, None, None))
    for sf in subfolders:
        sub = _paged_files(
            svc, f"'{sf['id']}' in parents and mimeType contains 'image/' and trashed = false",
            "files(id,name,mimeType,thumbnailLink)",
        )
        for f in sub:
            items.append(_scan_item(f, sf["id"], sf.get("name", sf["id"])))
    return items


def thumbnails_for(items: list[dict]) -> dict[str, str]:
    """Return ``{file_id: data_uri}`` for a page of scan items, fetched in parallel."""
    if not items:
        return {}
    _, session = _clients()
    files = [{"id": i["file_id"], "thumbnailLink": i.get("thumbnail_link")} for i in items]
    got = _attach_thumbnails(session, files)
    return {fid: v["thumbnail_url"] for fid, v in got.items()}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run python -m pytest tests/test_backfill_drive.py -k "scan_folder or thumbnails_for" -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Run the full drive test file**

Run: `poetry run python -m pytest tests/test_backfill_drive.py tests/test_drive_client.py -q`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add mockup_generator/integrations/drive_client.py tests/test_backfill_drive.py
git commit -m "feat(backfill): scan_folder_of_folders + paged thumbnails_for"
```

---

## Task 5: Shared publish path `publish_image` + rewire `/generate/approve`

**Files:**
- Create: `mockup_generator/generation/publish.py`
- Modify: `backend/routers/generate.py:71-82` (move `_photo_theme`), `:247-278` (call `publish_image`)
- Test: `tests/test_publish_image.py`; regression `tests/test_approve_publish.py`

**Interfaces:**
- Consumes: `storage_client`, `mockup_variations_repo`, `mockups_repo`, `productimages_repo`.
- Produces:
  - `build_photo_theme(theme_name: str | None, aspect_ratio: str | None) -> str`
  - `publish_image(db, *, productid: str, png: bytes, color: str | None, theme_name: str | None, aspect_ratio: str | None, created_by: str | None, prompt_text: str | None = None, prompt_id: int | None = None) -> dict` returning `{"image_url": str, "variation_id": int | None}`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_publish_image.py`:

```python
from io import BytesIO

from PIL import Image

from mockup_generator.generation import publish


def _png() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (4, 4), (1, 2, 3)).save(buf, format="PNG")
    return buf.getvalue()


def _wire(monkeypatch, calls):
    monkeypatch.setattr(publish.storage_client, "short_hex", lambda: "deadbeef")
    monkeypatch.setattr(publish.storage_client, "upload_mockup",
                        lambda pid, data, key, **kw: (f"{pid}/{key}.png", f"https://public/{pid}/{key}.png"))
    monkeypatch.setattr(publish.productimages_repo, "list_for",
                        lambda db, pid, cap, theme="Default": [])
    monkeypatch.setattr(publish.productimages_repo, "delete_for",
                        lambda db, pid, cap, theme="Default": calls.__setitem__("deleted_for", (pid, cap, theme)))
    monkeypatch.setattr(publish.productimages_repo, "insert",
                        lambda db, **kw: (calls.__setitem__("image", kw) or {"imageid": 1}))
    monkeypatch.setattr(publish.mockup_variations_repo, "insert",
                        lambda db, **kw: (calls.__setitem__("variation", kw) or {"variation_id": 7}))
    monkeypatch.setattr(publish.mockups_repo, "set_base_mockup",
                        lambda db, pid, value=True: calls.__setitem__("flag", (pid, value)))


def test_build_photo_theme():
    assert publish.build_photo_theme(None, None) == "Default"
    assert publish.build_photo_theme("Studio", "1:1") == "Studio"
    assert publish.build_photo_theme("Studio", "9:16") == "Studio·9:16"


def test_publish_image_writes_all_rows(monkeypatch):
    calls = {}
    _wire(monkeypatch, calls)
    out = publish.publish_image(
        object(), productid="BC25001", png=_png(), color="Red",
        theme_name=None, aspect_ratio=None, created_by="u1",
    )
    assert out["image_url"] == "https://public/BC25001/red_deadbeef.png"
    assert out["variation_id"] == 7
    assert calls["flag"] == ("BC25001", True)
    assert calls["image"]["caption"] == "Red"
    assert calls["image"]["theme"] == "Default"
    assert calls["variation"]["color"] == "Red"


def test_publish_image_allows_null_prompt_text(monkeypatch):
    calls = {}
    _wire(monkeypatch, calls)
    publish.publish_image(
        object(), productid="BC1", png=_png(), color="Blue",
        theme_name=None, aspect_ratio=None, created_by="u1", prompt_text=None,
    )
    assert calls["variation"]["prompt_text"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run python -m pytest tests/test_publish_image.py -q`
Expected: FAIL — `ModuleNotFoundError: mockup_generator.generation.publish`.

- [ ] **Step 3: Create the publish module**

Create `mockup_generator/generation/publish.py`:

```python
"""Shared publish path for approved mockups.

Uploads the PNG to the public ``mockups`` bucket, replaces the single
``productimages`` row for ``(productid, color, phototheme)`` (cleaning up the
prior Storage object), writes a ``mockup_variations`` audit row, and flips
``mockups.base_mockup``. Used by both ``/generate/approve`` (Phase 3) and the
Phase 7 backfill flow — one publish path, no duplication.
"""

from __future__ import annotations

from mockup_generator.db import mockup_variations_repo, mockups_repo, productimages_repo
from mockup_generator.integrations import storage_client


def build_photo_theme(theme_name: str | None, aspect_ratio: str | None) -> str:
    """Dedup photo-theme string: label, plus ``·<aspect>`` for non-1:1."""
    label = (theme_name or productimages_repo.DEFAULT_THEME).strip() \
        or productimages_repo.DEFAULT_THEME
    if aspect_ratio and aspect_ratio != "1:1":
        return f"{label}·{aspect_ratio}"
    return label


def publish_image(
    db, *, productid: str, png: bytes, color: str | None,
    theme_name: str | None, aspect_ratio: str | None, created_by: str | None,
    prompt_text: str | None = None, prompt_id: int | None = None,
) -> dict:
    """Publish ``png`` for ``productid`` + ``color``. Returns
    ``{"image_url", "variation_id"}``."""
    slug = storage_client.slugify(color)
    key = f"{slug}_{storage_client.short_hex()}" if slug else storage_client.short_hex()
    _path, public_url = storage_client.upload_mockup(productid, png, key)

    theme = build_photo_theme(theme_name, aspect_ratio)

    # One row per (productid, color, theme): replace the prior row and clean up
    # its orphaned Storage object (best-effort — cleanup must not fail a publish).
    for prior in productimages_repo.list_for(db, productid, color, theme):
        old_path = storage_client.path_from_public_url(prior.get("imageurl") or "")
        if old_path:
            try:
                storage_client.delete_object(old_path)
            except Exception:  # noqa: BLE001 - orphan cleanup is non-fatal
                pass
    productimages_repo.delete_for(db, productid, color, theme)

    row = mockup_variations_repo.insert(
        db, productid=productid, prompt_text=prompt_text, image_url=public_url,
        color=color, created_by=created_by, prompt_id=prompt_id,
    )
    mockups_repo.set_base_mockup(db, productid, True)
    productimages_repo.insert(db, productid=productid, imageurl=public_url,
                              caption=color, theme=theme)
    return {"image_url": public_url, "variation_id": row.get("variation_id")}
```

- [ ] **Step 4: Rewire `approve_mockup` to call `publish_image`**

In `backend/routers/generate.py`:

1. Add to the imports (near line 33): `from mockup_generator.generation import publish`.
2. Delete the `_photo_theme` function (lines 71–82).
3. Replace the body of `approve_mockup` from the `slug = ...` line through the `productimages_repo.insert(...)` call (lines 247–278) with:

```python
    text = prompt_text or ("(manual upload)" if source == "corrected" else "")
    try:
        result = publish.publish_image(
            db, productid=productid, png=png, color=color,
            theme_name=theme_name, aspect_ratio=aspect_ratio,
            created_by=user.id, prompt_text=text,
        )
    except storage_client.StorageNotConfigured as exc:
        raise HTTPException(status_code=503, detail="Storage is not configured on the server") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not store the mockup: {exc}") from exc

    return ApproveResponse(
        status="ok", detail="Published.",
        image_url=result["image_url"], variation_id=result["variation_id"],
    )
```

(Remove the now-unused inline `upload_mockup`/`list_for`/`delete_for`/`delete_object`/`mockup_variations_repo.insert`/`set_base_mockup`/`productimages_repo.insert` block and the old `try/except` around `upload_mockup`. Keep the upload-size/validity checks above it.)

- [ ] **Step 5: Run the regression + new tests**

Run: `poetry run python -m pytest tests/test_publish_image.py tests/test_approve_publish.py -q`
Expected: PASS — new `publish_image` tests pass AND all existing `test_approve_publish.py` tests stay green (the `_wire` helper patches `gen.storage_client`/`gen.mockup_variations_repo`/etc; since those names now resolve through `publish`, verify: if any approve test fails because it patched `gen.*`, update those patches to target `gen.publish.<dep>` instead — e.g. `monkeypatch.setattr(gen.publish.storage_client, "upload_mockup", ...)`).

- [ ] **Step 6: Fix approve test patch targets if needed**

If Step 5 shows approve tests failing, edit `tests/test_approve_publish.py` `_wire` to patch through `gen.publish` (the module the router now delegates to): replace `gen.storage_client`→`gen.publish.storage_client`, `gen.mockup_variations_repo`→`gen.publish.mockup_variations_repo`, `gen.mockups_repo`→`gen.publish.mockups_repo`, `gen.productimages_repo`→`gen.publish.productimages_repo`. Re-run Step 5 until green.

- [ ] **Step 7: Run the full suite**

Run: `poetry run python -m pytest -q`
Expected: PASS (all prior tests + new).

- [ ] **Step 8: Commit**

```bash
git add mockup_generator/generation/publish.py backend/routers/generate.py tests/test_publish_image.py tests/test_approve_publish.py
git commit -m "refactor(generate): extract shared publish_image; reuse in approve"
```

---

## Task 6: Backfill index service

**Files:**
- Create: `mockup_generator/services/__init__.py` (empty, if `services/` doesn't exist)
- Create: `mockup_generator/services/backfill_service.py`
- Test: `tests/test_backfill_service.py`

**Interfaces:**
- Consumes: `drive_client.scan_folder_of_folders` (Task 4), `settings.generated_mockups_folder_id` (Task 1).
- Produces:
  - `get_index(root_id: str, *, refresh: bool = False) -> list[dict]`
  - `paginate(items: list[dict], offset: int, limit: int) -> list[dict]`
  - `evict(file_id: str, *, root_id: str | None = None) -> None`
  - `clear_cache() -> None` (test seam)

- [ ] **Step 1: Write the failing test**

Create `tests/test_backfill_service.py`:

```python
from mockup_generator.services import backfill_service as svc


def _item(fid, pid="BC25001"):
    return {"productid": pid, "alpha": None, "file_id": fid, "name": f"{pid}.png",
            "subfolder_id": None, "subfolder_name": None, "thumbnail_link": f"l-{fid}"}


def setup_function():
    svc.clear_cache()


def test_get_index_scans_once_and_caches(monkeypatch):
    scans = {"n": 0}

    def fake_scan(root):
        scans["n"] += 1
        return [_item("a"), _item("b")]

    monkeypatch.setattr(svc.drive_client, "scan_folder_of_folders", fake_scan)
    first = svc.get_index("ROOT")
    second = svc.get_index("ROOT")
    assert [i["file_id"] for i in first] == ["a", "b"]
    assert second == first
    assert scans["n"] == 1                      # cached, not re-scanned


def test_refresh_forces_rescan(monkeypatch):
    scans = {"n": 0}
    monkeypatch.setattr(svc.drive_client, "scan_folder_of_folders",
                        lambda root: scans.__setitem__("n", scans["n"] + 1) or [_item("a")])
    svc.get_index("ROOT")
    svc.get_index("ROOT", refresh=True)
    assert scans["n"] == 2


def test_paginate():
    items = [_item(str(i)) for i in range(5)]
    assert [i["file_id"] for i in svc.paginate(items, 2, 2)] == ["2", "3"]


def test_evict_removes_item(monkeypatch):
    monkeypatch.setattr(svc.drive_client, "scan_folder_of_folders",
                        lambda root: [_item("a"), _item("b")])
    svc.get_index("ROOT")
    svc.evict("a", root_id="ROOT")
    assert [i["file_id"] for i in svc.get_index("ROOT")] == ["b"]   # served from cache, a gone
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run python -m pytest tests/test_backfill_service.py -q`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the service**

Create `mockup_generator/services/__init__.py` (empty) if missing, then `mockup_generator/services/backfill_service.py`:

```python
"""In-memory index of the generated Drive folder for the backfill review tab.

One expensive ``scan_folder_of_folders`` is amortized across all paging
(TTL 300s + manual refresh). Approve/flag evict the handled file. The cache is
per-process and simply re-scans after a restart — the Drive folder is the source
of truth for what still needs review.
"""

from __future__ import annotations

import time

from mockup_generator.integrations import drive_client

_TTL = 300.0
_cache: dict[str, tuple[float, list[dict]]] = {}


def clear_cache() -> None:
    _cache.clear()


def get_index(root_id: str, *, refresh: bool = False) -> list[dict]:
    """Return the cached flat index for ``root_id``, scanning Drive if stale/forced."""
    now = time.monotonic()
    cached = _cache.get(root_id)
    if not refresh and cached and (now - cached[0]) < _TTL:
        return cached[1]
    items = drive_client.scan_folder_of_folders(root_id)
    _cache[root_id] = (now, items)
    return items


def paginate(items: list[dict], offset: int, limit: int) -> list[dict]:
    return items[offset:offset + limit]


def evict(file_id: str, *, root_id: str | None = None) -> None:
    """Drop a handled file from the cached index (keeps the page counts honest)."""
    keys = [root_id] if root_id else list(_cache.keys())
    for k in keys:
        cached = _cache.get(k)
        if cached:
            ts, items = cached
            _cache[k] = (ts, [i for i in items if i["file_id"] != file_id])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run python -m pytest tests/test_backfill_service.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add mockup_generator/services/__init__.py mockup_generator/services/backfill_service.py tests/test_backfill_service.py
git commit -m "feat(backfill): in-memory index service (scan cache, paginate, evict)"
```

---

## Task 7: Backfill router — items / sources / approve / flag

**Files:**
- Modify: `backend/schemas.py`
- Create: `backend/routers/backfill.py`
- Modify: `backend/main.py:18-43` (register router)
- Test: `tests/test_backfill_api.py`

**Interfaces:**
- Consumes: `backfill_service` (Task 6), `drive_client` (Tasks 2–4), `publish.publish_image` (Task 5), `products_repo.get_product`, `variants_repo.list_colors`, `mockups_repo.set_base_mockup`, `settings.generated_mockups_folder_id`.
- Produces endpoints:
  - `GET /api/backfill/items?offset=&limit=&refresh=` → `{total, remaining, items: [...]}`
  - `GET /api/backfill/{file_id}/sources?productid=` → `{originals, generated_preview, suggested_aspect}`
  - `POST /api/backfill/approve` (JSON `{file_id, productid, color, theme_name?, aspect_ratio?}`) → `{status, image_url, variation_id, warning?}`
  - `POST /api/backfill/flag` (JSON `{file_id, productid?}`) → `{status}`

- [ ] **Step 1: Add schemas**

In `backend/schemas.py`, add (match the existing Pydantic style in that file):

```python
class BackfillItem(BaseModel):
    productid: str | None
    product_name: str | None
    alpha: str | None
    file_id: str
    filename: str
    thumbnail_url: str | None
    colors: list[str]
    unknown_product: bool


class BackfillItemsResponse(BaseModel):
    total: int
    remaining: int
    items: list[BackfillItem]


class BackfillApproveRequest(BaseModel):
    file_id: str
    productid: str
    color: str | None = None
    theme_name: str | None = None
    aspect_ratio: str | None = None


class BackfillFlagRequest(BaseModel):
    file_id: str
    productid: str | None = None
```

(If `BaseModel` isn't already imported in `schemas.py`, add `from pydantic import BaseModel`.)

- [ ] **Step 2: Write the failing test**

Create `tests/test_backfill_api.py`:

```python
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.main import app
from backend.auth import get_current_user, CurrentUser
from backend.deps import get_db
from backend.routers import backfill as bf
from mockup_generator.db.profiles_repo import Profile
from mockup_generator.db.products_repo import Product  # used as a simple stub


def _png(w=4, h=4) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (w, h), (5, 5, 5)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def client():
    u = CurrentUser(id="u1", email="a@b.c", role="user",
                    profile=Profile(id="u1", email="a@b.c", role="user", is_active=True))
    app.dependency_overrides[get_current_user] = lambda: u
    app.dependency_overrides[get_db] = lambda: object()
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _item(fid, pid="BC25001"):
    return {"productid": pid, "alpha": None, "file_id": fid, "name": f"{pid}.png",
            "subfolder_id": None, "subfolder_name": None, "thumbnail_link": f"l-{fid}"}


def test_items_paginates_and_enriches(client, monkeypatch):
    monkeypatch.setattr(bf.backfill_service, "get_index",
                        lambda root, refresh=False: [_item("a"), _item("b", "BCBAD")])
    monkeypatch.setattr(bf.drive_client, "thumbnails_for",
                        lambda items: {i["file_id"]: f"data:{i['file_id']}" for i in items})

    def fake_get_product(db, pid):
        return Product(productid=pid, name="Saree", categoryid="c1",
                       category_name="Sarees", base_mockup=False, producturl="u") if pid == "BC25001" else None

    monkeypatch.setattr(bf.products_repo, "get_product", fake_get_product)
    monkeypatch.setattr(bf.variants_repo, "list_colors", lambda db, pid: ["Red", "Blue"])

    r = client.get("/api/backfill/items?offset=0&limit=10")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    a = next(i for i in body["items"] if i["file_id"] == "a")
    assert a["product_name"] == "Saree" and a["unknown_product"] is False
    assert a["colors"] == ["Red", "Blue"] and a["thumbnail_url"] == "data:a"
    bad = next(i for i in body["items"] if i["file_id"] == "b")
    assert bad["unknown_product"] is True and bad["colors"] == []


def test_approve_publishes_then_deletes_drive(client, monkeypatch):
    calls = {}
    monkeypatch.setattr(bf.drive_client, "download_file", lambda fid: _png())
    monkeypatch.setattr(bf.publish, "publish_image",
                        lambda db, **kw: (calls.update(kw) or {"image_url": "https://pub/x.png", "variation_id": 9}))
    monkeypatch.setattr(bf.drive_client, "delete_file", lambda fid: calls.__setitem__("deleted", fid))
    monkeypatch.setattr(bf.backfill_service, "evict", lambda fid, **kw: calls.__setitem__("evicted", fid))

    r = client.post("/api/backfill/approve",
                    json={"file_id": "a", "productid": "BC25001", "color": "Red"})
    assert r.status_code == 200
    body = r.json()
    assert body["image_url"] == "https://pub/x.png" and body["variation_id"] == 9
    assert calls["productid"] == "BC25001" and calls["color"] == "Red"
    assert calls["prompt_text"] is None
    assert calls["deleted"] == "a" and calls["evicted"] == "a"


def test_approve_warns_when_drive_delete_fails(client, monkeypatch):
    monkeypatch.setattr(bf.drive_client, "download_file", lambda fid: _png())
    monkeypatch.setattr(bf.publish, "publish_image",
                        lambda db, **kw: {"image_url": "https://pub/x.png", "variation_id": 1})
    monkeypatch.setattr(bf.drive_client, "delete_file",
                        lambda fid: (_ for _ in ()).throw(RuntimeError("drive boom")))
    monkeypatch.setattr(bf.backfill_service, "evict", lambda fid, **kw: None)

    r = client.post("/api/backfill/approve",
                    json={"file_id": "a", "productid": "BC25001", "color": "Red"})
    assert r.status_code == 200
    assert r.json()["warning"]                    # published, but Drive cleanup failed


def test_flag_sets_pending_and_moves(client, monkeypatch):
    calls = {}
    monkeypatch.setattr(bf.mockups_repo, "set_base_mockup",
                        lambda db, pid, value: calls.__setitem__("flag", (pid, value)))
    monkeypatch.setattr(bf.drive_client, "ensure_subfolder", lambda root, name: "REJECTED")
    monkeypatch.setattr(bf.drive_client, "move_file",
                        lambda fid, parent: calls.__setitem__("moved", (fid, parent)))
    monkeypatch.setattr(bf.backfill_service, "evict", lambda fid, **kw: calls.__setitem__("evicted", fid))

    r = client.post("/api/backfill/flag", json={"file_id": "a", "productid": "BC25001"})
    assert r.status_code == 200
    assert calls["flag"] == ("BC25001", False)
    assert calls["moved"][1] == "REJECTED"
    assert calls["evicted"] == "a"


def test_flag_unknown_product_moves_only(client, monkeypatch):
    calls = {}
    monkeypatch.setattr(bf.mockups_repo, "set_base_mockup",
                        lambda db, pid, value: calls.__setitem__("flag", True))
    monkeypatch.setattr(bf.drive_client, "ensure_subfolder", lambda root, name: "REJECTED")
    monkeypatch.setattr(bf.drive_client, "move_file", lambda fid, parent: calls.__setitem__("moved", fid))
    monkeypatch.setattr(bf.backfill_service, "evict", lambda fid, **kw: None)

    r = client.post("/api/backfill/flag", json={"file_id": "a"})   # no productid
    assert r.status_code == 200
    assert "flag" not in calls and calls["moved"] == "a"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `poetry run python -m pytest tests/test_backfill_api.py -q`
Expected: FAIL — `backend.routers.backfill` not importable / route 404.

- [ ] **Step 4: Implement the router**

Create `backend/routers/backfill.py`:

```python
"""Backfill review endpoints.

Walk the previously-generated Drive mockups: list paginated review cards
(``items``), load a card's originals + preview (``sources``), then publish
(``approve`` → Supabase + delete the Drive original) or send back for
regeneration (``flag`` → base_mockup=false + move to ``rejected/``).
"""

from __future__ import annotations

import base64
import logging
from io import BytesIO

from fastapi import APIRouter, Depends, HTTPException
from PIL import Image
from supabase import Client

from backend.auth import CurrentUser, get_current_user
from backend.deps import get_db
from backend.schemas import (
    BackfillApproveRequest, BackfillFlagRequest, BackfillItem, BackfillItemsResponse,
)
from mockup_generator.config import settings
from mockup_generator.db import mockups_repo, products_repo, variants_repo
from mockup_generator.generation import publish
from mockup_generator.integrations import drive_client
from mockup_generator.integrations.drive_client import DriveNotConfigured
from mockup_generator.services import backfill_service

router = APIRouter(prefix="/api/backfill", tags=["backfill"])
log = logging.getLogger(__name__)

_STD_ASPECTS = [("1:1", 1.0), ("4:5", 0.8), ("3:4", 0.75), ("9:16", 0.5625), ("16:9", 1.7778)]


def _suggest_aspect(w: int, h: int) -> str:
    if not h:
        return "1:1"
    ratio = w / h
    return min(_STD_ASPECTS, key=lambda a: abs(a[1] - ratio))[0]


@router.get("/items", response_model=BackfillItemsResponse)
def list_items(offset: int = 0, limit: int = 20, refresh: bool = False,
               user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    try:
        index = backfill_service.get_index(settings.generated_mockups_folder_id, refresh=refresh)
    except DriveNotConfigured as exc:
        raise HTTPException(status_code=503, detail="Drive access is not configured on the server") from exc
    page = backfill_service.paginate(index, offset, limit)
    thumbs = drive_client.thumbnails_for(page)

    items: list[BackfillItem] = []
    for it in page:
        pid = it["productid"]
        product = products_repo.get_product(db, pid) if pid else None
        colors = variants_repo.list_colors(db, pid) if product else []
        items.append(BackfillItem(
            productid=pid,
            product_name=getattr(product, "name", None),
            alpha=it["alpha"],
            file_id=it["file_id"],
            filename=it["name"],
            thumbnail_url=thumbs.get(it["file_id"]),
            colors=colors,
            unknown_product=product is None,
        ))
    return BackfillItemsResponse(total=len(index), remaining=len(index), items=items)


@router.get("/{file_id}/sources")
def card_sources(file_id: str, productid: str | None = None,
                 user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    originals = {"loose": [], "groups": []}
    product = products_repo.get_product(db, productid) if productid else None
    if product and getattr(product, "producturl", None):
        fid = drive_client.extract_folder_id(product.producturl)
        if fid:
            try:
                originals = drive_client.list_folder_image_groups(fid)
            except DriveNotConfigured:
                raise HTTPException(status_code=503, detail="Drive access is not configured on the server")
            except Exception as exc:  # noqa: BLE001 - originals are reference-only
                log.warning("could not list originals for %s: %s", productid, exc)

    try:
        png = drive_client.download_file(file_id)
    except DriveNotConfigured as exc:
        raise HTTPException(status_code=503, detail="Drive access is not configured on the server") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not load the generated image: {exc}") from exc

    try:
        w, h = Image.open(BytesIO(png)).size
    except Exception:  # noqa: BLE001
        w, h = 1, 1
    preview = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    return {"originals": originals, "generated_preview": preview,
            "suggested_aspect": _suggest_aspect(w, h)}


@router.post("/approve")
def approve(req: BackfillApproveRequest,
            user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    try:
        png = drive_client.download_file(req.file_id)
    except DriveNotConfigured as exc:
        raise HTTPException(status_code=503, detail="Drive access is not configured on the server") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not load the generated image: {exc}") from exc

    try:
        result = publish.publish_image(
            db, productid=req.productid, png=png, color=req.color,
            theme_name=req.theme_name, aspect_ratio=req.aspect_ratio,
            created_by=user.id, prompt_text=None,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Could not publish the mockup: {exc}") from exc

    warning = None
    try:
        drive_client.delete_file(req.file_id)
    except Exception as exc:  # noqa: BLE001 - published already; Drive cleanup is non-fatal
        log.warning("published %s but Drive delete failed: %s", req.file_id, exc)
        warning = "Published, but the Drive original could not be removed (will reappear on refresh)."
    backfill_service.evict(req.file_id)

    return {"status": "ok", "image_url": result["image_url"],
            "variation_id": result["variation_id"], "warning": warning}


@router.post("/flag")
def flag(req: BackfillFlagRequest,
         user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    if req.productid:
        mockups_repo.set_base_mockup(db, req.productid, False)
    rejected = drive_client.ensure_subfolder(settings.generated_mockups_folder_id, "rejected")
    try:
        drive_client.move_file(req.file_id, rejected)
    except DriveNotConfigured as exc:
        raise HTTPException(status_code=503, detail="Drive access is not configured on the server") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not move the image to rejected/: {exc}") from exc
    backfill_service.evict(req.file_id)
    return {"status": "ok"}
```

- [ ] **Step 5: Register the router**

In `backend/main.py`, add after the other router imports (line ~20):
`from backend.routers import backfill as backfill_router`
and after `app.include_router(prompts_router.router)` (line ~43):
`app.include_router(backfill_router.router)`

- [ ] **Step 6: Run test to verify it passes**

Run: `poetry run python -m pytest tests/test_backfill_api.py -q`
Expected: PASS (5 tests). If `variants_repo` isn't imported under that exact name in the router, ensure the import in Step 4 matches (`from mockup_generator.db import ... variants_repo`).

- [ ] **Step 7: Run the full suite**

Run: `poetry run python -m pytest -q`
Expected: PASS (all).

- [ ] **Step 8: Commit**

```bash
git add backend/schemas.py backend/routers/backfill.py backend/main.py tests/test_backfill_api.py
git commit -m "feat(backfill): items/sources/approve/flag endpoints"
```

---

## Task 8: Frontend API wrappers + types

**Files:**
- Modify: `frontend/src/api.ts`

**Interfaces:**
- Produces: `BackfillItem`, `BackfillItems`, `BackfillSources` types; `listBackfill`, `getBackfillSources`, `approveBackfill`, `flagBackfill` functions.

- [ ] **Step 1: Add types and wrappers**

Append to `frontend/src/api.ts` (reuse the existing `ProductImages` interface for originals):

```typescript
export interface BackfillItem {
  productid: string | null;
  product_name: string | null;
  alpha: string | null;
  file_id: string;
  filename: string;
  thumbnail_url: string | null;
  colors: string[];
  unknown_product: boolean;
}

export interface BackfillItems {
  total: number;
  remaining: number;
  items: BackfillItem[];
}

export interface BackfillSources {
  originals: ProductImages;
  generated_preview: string;
  suggested_aspect: string;
}

export function listBackfill(p: { offset?: number; limit?: number; refresh?: boolean }) {
  const q = new URLSearchParams();
  if (p.offset != null) q.set("offset", String(p.offset));
  if (p.limit != null) q.set("limit", String(p.limit));
  if (p.refresh) q.set("refresh", "true");
  return apiFetch<BackfillItems>(`/api/backfill/items?${q.toString()}`);
}

export const getBackfillSources = (fileId: string, productid: string | null) =>
  apiFetch<BackfillSources>(
    `/api/backfill/${encodeURIComponent(fileId)}/sources` +
      (productid ? `?productid=${encodeURIComponent(productid)}` : "")
  );

export const approveBackfill = (b: {
  file_id: string;
  productid: string;
  color?: string;
  theme_name?: string;
  aspect_ratio?: string;
}) =>
  apiFetch<{ status: string; image_url: string; variation_id?: number; warning?: string | null }>(
    "/api/backfill/approve",
    { method: "POST", body: JSON.stringify(b) }
  );

export const flagBackfill = (b: { file_id: string; productid: string | null }) =>
  apiFetch<{ status: string }>("/api/backfill/flag", {
    method: "POST",
    body: JSON.stringify(b),
  });
```

- [ ] **Step 2: Verify the build compiles**

Run: `cd frontend && npm run build`
Expected: build succeeds (no TS errors).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api.ts
git commit -m "feat(backfill): frontend api wrappers + types"
```

---

## Task 9: Frontend Backfill tab (card grid + review panel)

**Files:**
- Create: `frontend/src/components/BackfillTab.tsx`
- Modify: `frontend/src/App.tsx:98-101` (TABS), `:135` (render)

**Interfaces:**
- Consumes: `listBackfill`, `getBackfillSources`, `approveBackfill`, `flagBackfill` (Task 8).

**Design note:** Before writing this component, invoke the `ui-ux-pro-max:ui-ux-pro-max` skill and apply its rules (≥44px touch targets, visible focus states, hover-vs-tap, contrast). Reuse existing CSS classes/tokens from `index.css` (`.card`, `.btn-primary`, `.tabs`, `.tab`, `var(--accent)`, `.muted`, `.alert`) so the tab matches Products/Prompts.

- [ ] **Step 1: Write the component**

Create `frontend/src/components/BackfillTab.tsx`:

```tsx
import { useEffect, useState } from "react";
import {
  listBackfill, getBackfillSources, approveBackfill, flagBackfill,
  type BackfillItem, type BackfillSources, ApiError,
} from "../api";

const PAGE = 20;
const ASPECTS = ["1:1", "4:5", "3:4", "9:16", "16:9"];

export default function BackfillTab() {
  const [items, setItems] = useState<BackfillItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [active, setActive] = useState<BackfillItem | null>(null);

  const load = (refresh = false) => {
    setLoading(true);
    setError(null);
    listBackfill({ offset: 0, limit: PAGE, refresh })
      .then((r) => {
        setItems(r.items);
        setTotal(r.total);
      })
      .catch((e: ApiError) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => load(), []);

  const onResolved = (fileId: string) => {
    setItems((xs) => xs.filter((i) => i.file_id !== fileId));
    setTotal((t) => Math.max(0, t - 1));
    setActive(null);
  };

  if (loading) return <p className="muted">Loading mockups…</p>;
  if (error)
    return (
      <div className="stack">
        <p className="alert alert-error">{error}</p>
        <button onClick={() => load()}>Retry</button>
      </div>
    );

  return (
    <div className="stack">
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
        <span className="muted">{total} remaining</span>
        <button onClick={() => load(true)}>Refresh</button>
      </div>

      {items.length === 0 ? (
        <p className="muted">Nothing left to review. 🎉</p>
      ) : (
        <div className="grid">
          {items.map((it) => (
            <div key={it.file_id} className="card stack">
              {it.thumbnail_url ? (
                <img src={it.thumbnail_url} alt={it.filename} style={{ width: "100%", borderRadius: 8 }} />
              ) : (
                <div className="muted">no preview</div>
              )}
              <div className="row" style={{ justifyContent: "space-between" }}>
                <strong>{it.productid ?? it.filename}</strong>
                {it.unknown_product && <span className="alert alert-error">unknown product</span>}
              </div>
              <button className="btn-primary" onClick={() => setActive(it)}>Review</button>
            </div>
          ))}
        </div>
      )}

      {active && (
        <ReviewPanel item={active} onClose={() => setActive(null)} onResolved={onResolved} />
      )}
    </div>
  );
}

function ReviewPanel({
  item, onClose, onResolved,
}: {
  item: BackfillItem;
  onClose: () => void;
  onResolved: (fileId: string) => void;
}) {
  const [data, setData] = useState<BackfillSources | null>(null);
  const [color, setColor] = useState<string>(item.colors.length === 1 ? item.colors[0] : "");
  const [theme, setTheme] = useState("Default");
  const [aspect, setAspect] = useState("1:1");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    getBackfillSources(item.file_id, item.productid)
      .then((d) => {
        setData(d);
        if (d.suggested_aspect) setAspect(d.suggested_aspect);
      })
      .catch((e: ApiError) => setMsg(e.message));
  }, [item.file_id, item.productid]);

  const doApprove = () => {
    if (!item.productid) return;
    setBusy(true);
    setMsg(null);
    approveBackfill({
      file_id: item.file_id, productid: item.productid,
      color: color || undefined, theme_name: theme, aspect_ratio: aspect,
    })
      .then((r) => {
        if (r.warning) setMsg(r.warning);
        onResolved(item.file_id);
      })
      .catch((e: ApiError) => setMsg(e.message))
      .finally(() => setBusy(false));
  };

  const doFlag = () => {
    setBusy(true);
    setMsg(null);
    flagBackfill({ file_id: item.file_id, productid: item.productid })
      .then(() => onResolved(item.file_id))
      .catch((e: ApiError) => setMsg(e.message))
      .finally(() => setBusy(false));
  };

  return (
    <div className="card stack" role="dialog" aria-label={`Review ${item.productid ?? item.filename}`}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <strong>{item.productid ?? item.filename}{item.product_name ? ` — ${item.product_name}` : ""}</strong>
        <button onClick={onClose}>Close</button>
      </div>

      <div className="row" style={{ gap: 16, alignItems: "flex-start" }}>
        <div style={{ flex: 1 }}>
          <p className="muted">Original images</p>
          {!data ? (
            <p className="muted">Loading…</p>
          ) : (
            <div className="grid">
              {[...data.originals.loose, ...data.originals.groups.flatMap((g) => g.images)].map((im) => (
                <img key={im.id} src={im.thumbnail_url} alt={im.name} style={{ width: "100%", borderRadius: 6 }} />
              ))}
            </div>
          )}
        </div>
        <div style={{ flex: 1 }}>
          <p className="muted">Generated</p>
          {data ? (
            <img src={data.generated_preview} alt="generated" style={{ width: "100%", borderRadius: 6 }} />
          ) : (
            <img src={item.thumbnail_url ?? ""} alt="generated" style={{ width: "100%", borderRadius: 6 }} />
          )}
        </div>
      </div>

      <label>Color
        <select value={color} onChange={(e) => setColor(e.target.value)}>
          <option value="">— select —</option>
          {item.colors.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
      </label>
      <label>Theme
        <input value={theme} onChange={(e) => setTheme(e.target.value)} />
      </label>
      <label>Aspect
        <select value={aspect} onChange={(e) => setAspect(e.target.value)}>
          {ASPECTS.map((a) => <option key={a} value={a}>{a}</option>)}
        </select>
      </label>

      {msg && <p className="alert">{msg}</p>}

      <div className="row" style={{ gap: 8 }}>
        <button className="btn-primary" disabled={busy || item.unknown_product || !color} onClick={doApprove}>
          Approve &amp; publish
        </button>
        <button disabled={busy} onClick={doFlag}>Flag for regeneration</button>
      </div>
      {item.unknown_product && <p className="muted">Unknown product — approve disabled; flag will move to rejected/.</p>}
    </div>
  );
}
```

- [ ] **Step 2: Wire the tab into App.tsx**

In `frontend/src/App.tsx`:
1. Add import: `import BackfillTab from "./components/BackfillTab";`
2. Extend `TABS` (line 98–101) to include backfill:

```tsx
const TABS = [
  { id: "products", label: "Products" },
  { id: "prompts", label: "Prompts" },
  { id: "backfill", label: "Backfill" },
] as const;
```

3. Replace the tabpanel render (line 135) with:

```tsx
      <div role="tabpanel">
        {tab === "products" ? <ProductsTab /> : tab === "prompts" ? <PromptsTab /> : <BackfillTab />}
      </div>
```

- [ ] **Step 3: Verify the build compiles**

Run: `cd frontend && npm run build`
Expected: build succeeds. If `.grid` / `.row` classes aren't in `index.css`, swap to existing layout classes used by `ProductsTab.tsx` (check that file) or add minimal inline styles — do NOT invent new class names that don't exist.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/BackfillTab.tsx frontend/src/App.tsx
git commit -m "feat(backfill): review tab UI (card grid + split review panel)"
```

---

## Task 10: Full verification + plan sync

**Files:**
- Modify: `docs/plans/2026-06-21-implementation-plan.md:86-88` (mark Phase 7 done)

- [ ] **Step 1: Run the entire backend suite**

Run: `poetry run python -m pytest -q`
Expected: PASS (all — prior 97+ plus the new backfill/publish tests).

- [ ] **Step 2: Run the frontend build**

Run: `cd frontend && npm run build`
Expected: clean build.

- [ ] **Step 3: Update the master plan**

In `docs/plans/2026-06-21-implementation-plan.md`, update the Phase 7 section: check the backfill box, note it shipped as the interactive review tab, and reference `docs/superpowers/plans/2026-06-24-phase7-backfill-review.md`.

- [ ] **Step 4: Commit**

```bash
git add docs/plans/2026-06-21-implementation-plan.md
git commit -m "docs: mark Phase 7 backfill review tab shipped"
```

---

## Self-Review notes (for the implementer)

- **Manual smoke (do live before declaring done):** scope must be re-granted — the SA needs `…/auth/drive` (not readonly) and Editor sharing on `1FBDw…`. Verify: open the Backfill tab → a card renders with a thumbnail → Review loads originals + generated preview → Approve publishes (public URL renders, Drive original disappears) → Flag moves a different image into a new `rejected/` folder and sets `base_mockup=false`.
- **Drive scope is process-wide:** broadening `_SCOPES` also affects the existing read endpoints — they keep working (write scope is a superset of read). The `lru_cache` on `_clients()` means a running server must restart to pick up the new scope.
- **Idempotency:** approve deletes the Drive file, so a re-scan never re-surfaces it; if the delete fails, `publish_image` upserts + orphan-cleans, so re-approving the same image is safe.
