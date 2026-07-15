# Batch Generate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Batch Generate tab that generates mockups for many products at once (one card per product×color), backed by a persistent DB worklist and a background worker, with per-card accept/edit/reject review.

**Architecture:** Enqueue is instant — it plans one `batch_items` row per (product, color) and returns. A single background worker thread sweeps `queued` rows sequentially: generates via the existing `service.generate_mockup_bytes`, stages the PNG in a `_batch` subfolder of the Base Mockup Folder in Drive, and marks the row `ready`. Reviewers accept (publish to Supabase via the existing `publish.publish_image`, then delete the Drive copy), edit (re-queue with a new prompt/images), or reject (delete the Drive copy). All operator transitions are optimistic (guarded on expected status → 409 on race), mirroring the Backfill worklist.

**Tech Stack:** FastAPI, Supabase (PostgREST via `supabase-py`), Google Drive API (`googleapiclient`), Google Gemini image model, React + Vite + TypeScript, pytest.

## Global Constraints

- Python 3.10 (`>=3.10,<3.11`). Use `from __future__ import annotations`.
- Count range: integer **1–100** (number of product IDs).
- Product selection: **pending only** (`base_mockup=False`), ordered by product-id key.
- Skip products with **no Drive images**, **no Drive folder**, or whose **category has no resolvable prompt** (report each skip with a reason). Skip is per-product.
- One card **per color** from `variants_repo.list_colors`; **no colors → one colorless card**.
- Prompt prefix: `"Make the professional mockup of the {color} product."` (colorless: `"Make the professional mockup of the product."`), then `\n\n` + the category prompt body.
- Prompt resolution order: DB default (`prompts_repo.list_by_category` → `is_default`) → `mockup_generator.prompts.defaults.prompt_for_category(categoryid)` → else skip.
- Generation is **sequential** (worker concurrency 1). The generator already retries 429/5xx up to 8 times.
- Drive staging folder: subfolder `_batch` under `settings.generated_mockups_folder_id`; its name is added to `drive_client._RESERVED_SUBFOLDERS` so the Backfill scan ignores it.
- Batch staged files are uploaded by the service account (SA owns them), so `drive_client.delete_file` is valid for them — unlike the Backfill flow which only moves files.
- All new tables are service-role only (RLS enabled, no anon/authenticated policies).
- Follow the `ui-ux-pro-max:ui-ux-pro-max` skill for all frontend work (touch targets, focus states, contrast).

---

## File Structure

**New**
- `docs/migrations/2026-07-14-batch-items.sql` — `batch_items` table + indexes + RLS.
- `mockup_generator/db/batch_items_repo.py` — worklist read/write (dataclass, page, counts, get, transition, claim, reset, insert_many).
- `mockup_generator/services/batch_enqueue.py` — pure planning: resolve prompt, compose prompt, plan cards + skips.
- `backend/services/batch_worker.py` — background sweep (claim → generate → stage → mark).
- `backend/routers/batch.py` — `/api/batch` endpoints.
- `frontend/src/components/BatchTab.tsx` — the tab UI.
- Tests: `tests/test_batch_items_repo.py`, `tests/test_batch_drive.py`, `tests/test_batch_enqueue.py`, `tests/test_batch_worker.py`, `tests/test_batch_api.py`.

**Modified**
- `mockup_generator/integrations/drive_client.py` — `BATCH_STAGING_FOLDER` const (+ into `_RESERVED_SUBFOLDERS`), `upload_image`, `list_folder_image_ids`.
- `backend/schemas.py` — batch request/response models.
- `backend/main.py` — register router; startup orphan-reset + worker resume.
- `frontend/src/api.ts` — batch client fns + types.
- `frontend/src/App.tsx` — register the tab.

---

## Task 1: Migration + `batch_items_repo`

**Files:**
- Create: `docs/migrations/2026-07-14-batch-items.sql`
- Create: `mockup_generator/db/batch_items_repo.py`
- Test: `tests/test_batch_items_repo.py`

**Interfaces:**
- Consumes: `supabase.Client`.
- Produces:
  - Status constants `QUEUED="queued"`, `GENERATING="generating"`, `READY="ready"`, `FAILED="failed"`, `PUBLISHED="published"`, `REJECTED="rejected"`; `ALL_STATUSES: list[str]`.
  - `@dataclass BatchRow`: `id:int, batch_id:str, productid:str, color:str|None, image_ids:list[str], prompt_text:str, status:str, drive_file_id:str|None, thumbnail_link:str|None, error:str|None, model:str, resolution:str, aspect_ratio:str`.
  - `insert_many(client, rows: list[dict]) -> int`
  - `page(client, *, statuses: list[str], offset: int, limit: int) -> tuple[list[BatchRow], int]`
  - `counts(client) -> dict[str, int]` (keyed by every status in `ALL_STATUSES`)
  - `get(client, item_id: int) -> BatchRow | None`
  - `transition(client, *, item_id: int, expect: str, to: str, **fields) -> bool`
  - `claim_next_queued(client) -> BatchRow | None`
  - `reset_orphaned_generating(client) -> int`

- [ ] **Step 1: Write the migration SQL**

Create `docs/migrations/2026-07-14-batch-items.sql`:

```sql
-- Batch Generate worklist. One row per (product, color) card produced by a
-- batch enqueue. Cards persist here so review survives sessions and is
-- concurrency-safe (optimistic status transitions, mirroring backfill_items).
--
-- status lifecycle:
--   queued      -> awaiting generation by the background worker
--   generating  -> claimed by the worker (reset to queued on crash recovery)
--   ready        -> mockup generated + staged in Drive, awaiting review
--   failed      -> generation errored after retries (retryable -> queued)
--   published   -> accepted, copied to Supabase Storage, Drive copy deleted
--   rejected    -> discarded, Drive copy deleted
create table if not exists public.batch_items (
  id            bigint generated always as identity primary key,
  batch_id      uuid not null,
  productid     text not null references public.products(productid),
  color         text,                                  -- null = colorless product
  image_ids     jsonb not null,                        -- Drive source file ids used
  prompt_text   text not null,
  status        text not null default 'queued',
  drive_file_id text,                                  -- staged mockup in Drive (set when ready)
  thumbnail_link text,                                 -- Drive thumbnailLink of the staged mockup
  error         text,
  model         text not null,
  resolution    text not null,
  aspect_ratio  text not null,
  created_by    uuid references public.profiles(id),
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create index if not exists batch_items_status_idx on public.batch_items (status);
create index if not exists batch_items_batch_idx on public.batch_items (batch_id);

-- Server-side (service-role) access only, like every other table here.
alter table public.batch_items enable row level security;
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_batch_items_repo.py` (reuses the FakeClient pattern from `tests/test_batch_items_repo.py`'s sibling `test_backfill_items_repo.py`):

```python
from mockup_generator.db import batch_items_repo as repo


class FakeResp:
    def __init__(self, data, count=None):
        self.data, self.count = data, count


class FakeQuery:
    def __init__(self, sink, resp):
        self.sink, self._resp = sink, resp

    def select(self, *a, **k): self.sink.append(("select", a, k)); return self
    def eq(self, c, v): self.sink.append(("eq", c, v)); return self
    def in_(self, c, v): self.sink.append(("in", c, v)); return self
    def order(self, c, **k): self.sink.append(("order", c, k)); return self
    def range(self, lo, hi): self.sink.append(("range", lo, hi)); return self
    def limit(self, n): self.sink.append(("limit", n)); return self
    def update(self, payload): self.sink.append(("update", payload)); return self
    def insert(self, rows): self.sink.append(("insert", rows)); return self
    def execute(self): return self._resp


class FakeClient:
    def __init__(self, resp):
        self.sink, self._resp = [], resp
    def table(self, name):
        self.sink.append(("table", name)); return FakeQuery(self.sink, self._resp)


def _raw(id=1, status="queued"):
    return {"id": id, "batch_id": "b1", "productid": "BC25001", "color": "Red",
            "image_ids": ["f1", "f2"], "prompt_text": "p", "status": status,
            "drive_file_id": None, "thumbnail_link": None, "error": None,
            "model": "m", "resolution": "4K", "aspect_ratio": "1:1"}


def test_page_filters_by_statuses_and_returns_total():
    c = FakeClient(FakeResp([_raw()], count=5))
    rows, total = repo.page(c, statuses=[repo.QUEUED, repo.GENERATING], offset=0, limit=20)
    assert total == 5 and rows[0].id == 1 and rows[0].image_ids == ["f1", "f2"]
    assert ("table", "batch_items") in c.sink
    assert ("in", "status", [repo.QUEUED, repo.GENERATING]) in c.sink
    assert ("range", 0, 19) in c.sink


def test_transition_guards_on_expected_status_and_merges_fields():
    c = FakeClient(FakeResp([{"id": 1}]))
    assert repo.transition(c, item_id=1, expect=repo.GENERATING, to=repo.READY,
                           drive_file_id="drv", thumbnail_link="lnk") is True
    assert ("eq", "id", 1) in c.sink
    assert ("eq", "status", repo.GENERATING) in c.sink
    upd = next(p for tag, p in [(s[0], s[1]) for s in c.sink if s[0] == "update"])
    assert upd["status"] == repo.READY and upd["drive_file_id"] == "drv"
    assert upd["thumbnail_link"] == "lnk"


def test_transition_returns_false_when_no_row():
    c = FakeClient(FakeResp([]))
    assert repo.transition(c, item_id=1, expect=repo.READY, to=repo.PUBLISHED) is False


def test_insert_many_empty_is_noop():
    c = FakeClient(FakeResp([]))
    assert repo.insert_many(c, []) == 0
    assert c.sink == []


def test_reset_orphaned_moves_generating_to_queued():
    c = FakeClient(FakeResp([{"id": 1}, {"id": 2}]))
    assert repo.reset_orphaned_generating(c) == 2
    upd = next(p for tag, p in [(s[0], s[1]) for s in c.sink if s[0] == "update"])
    assert upd["status"] == repo.QUEUED
    assert ("eq", "status", repo.GENERATING) in c.sink
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `poetry run pytest tests/test_batch_items_repo.py -v`
Expected: FAIL (`ModuleNotFoundError: ... batch_items_repo`).

- [ ] **Step 4: Write the repo**

Create `mockup_generator/db/batch_items_repo.py`:

```python
"""Read/write access to ``batch_items`` — the Batch Generate worklist.

One row per (product, color) card. Status transitions are optimistic:
``transition`` issues a conditional update guarded on the expected current
status and reports whether it won the row, so concurrent reviewers (and the
worker) can't both act on the same card.
"""

from __future__ import annotations

from dataclasses import dataclass

from supabase import Client

QUEUED = "queued"
GENERATING = "generating"
READY = "ready"
FAILED = "failed"
PUBLISHED = "published"
REJECTED = "rejected"

ALL_STATUSES = [QUEUED, GENERATING, READY, FAILED, PUBLISHED, REJECTED]

_COLS = (
    "id, batch_id, productid, color, image_ids, prompt_text, status, "
    "drive_file_id, thumbnail_link, error, model, resolution, aspect_ratio"
)


@dataclass
class BatchRow:
    id: int
    batch_id: str
    productid: str
    color: str | None
    image_ids: list[str]
    prompt_text: str
    status: str
    drive_file_id: str | None
    thumbnail_link: str | None
    error: str | None
    model: str
    resolution: str
    aspect_ratio: str


def _row(r: dict) -> BatchRow:
    return BatchRow(
        id=int(r["id"]),
        batch_id=r["batch_id"],
        productid=r["productid"],
        color=r.get("color"),
        image_ids=list(r.get("image_ids") or []),
        prompt_text=r["prompt_text"],
        status=r["status"],
        drive_file_id=r.get("drive_file_id"),
        thumbnail_link=r.get("thumbnail_link"),
        error=r.get("error"),
        model=r["model"],
        resolution=r["resolution"],
        aspect_ratio=r["aspect_ratio"],
    )


def insert_many(client: Client, rows: list[dict]) -> int:
    if not rows:
        return 0
    resp = client.table("batch_items").insert(rows).execute()
    return len(resp.data or [])


def page(client: Client, *, statuses: list[str], offset: int, limit: int) -> tuple[list[BatchRow], int]:
    resp = (
        client.table("batch_items").select(_COLS, count="exact")
        .in_("status", statuses)
        .order("id", desc=False)
        .range(offset, offset + limit - 1)
        .execute()
    )
    rows = [_row(r) for r in (resp.data or [])]
    total = resp.count if resp.count is not None else len(rows)
    return rows, total


def counts(client: Client) -> dict[str, int]:
    out: dict[str, int] = {}
    for s in ALL_STATUSES:
        resp = (
            client.table("batch_items").select("id", count="exact")
            .eq("status", s).limit(1).execute()
        )
        out[s] = resp.count or 0
    return out


def get(client: Client, item_id: int) -> BatchRow | None:
    resp = client.table("batch_items").select(_COLS).eq("id", item_id).limit(1).execute()
    rows = resp.data or []
    return _row(rows[0]) if rows else None


def transition(client: Client, *, item_id: int, expect: str, to: str, **fields) -> bool:
    """Conditionally move a row ``expect -> to``, merging any extra column
    ``fields`` into the same update. Returns True iff this call won the row."""
    payload = {"status": to, "updated_at": "now()", **fields}
    resp = (
        client.table("batch_items").update(payload)
        .eq("id", item_id).eq("status", expect).execute()
    )
    return bool(resp.data)


def claim_next_queued(client: Client) -> BatchRow | None:
    """Claim the oldest ``queued`` row (queued -> generating). Race-safe: if the
    conditional update loses (another worker won), retry the next candidate.
    Returns the claimed row, or None when no queued rows remain."""
    while True:
        resp = (
            client.table("batch_items").select(_COLS)
            .eq("status", QUEUED).order("id", desc=False).limit(1).execute()
        )
        rows = resp.data or []
        if not rows:
            return None
        row = _row(rows[0])
        if transition(client, item_id=row.id, expect=QUEUED, to=GENERATING):
            row.status = GENERATING
            return row
        # lost the race; try the next candidate


def reset_orphaned_generating(client: Client) -> int:
    """Crash recovery: flip any ``generating`` rows back to ``queued``."""
    resp = (
        client.table("batch_items")
        .update({"status": QUEUED, "updated_at": "now()"})
        .eq("status", GENERATING).execute()
    )
    return len(resp.data or [])
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `poetry run pytest tests/test_batch_items_repo.py -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Commit**

```bash
git add docs/migrations/2026-07-14-batch-items.sql mockup_generator/db/batch_items_repo.py tests/test_batch_items_repo.py
git commit -m "feat(batch): add batch_items table + worklist repo"
```

---

## Task 2: Drive helpers (upload + id-lister + scan exclusion)

**Files:**
- Modify: `mockup_generator/integrations/drive_client.py`
- Test: `tests/test_batch_drive.py`

**Interfaces:**
- Consumes: existing `_clients()`, `_FOLDER_MIME`, `_RESERVED_SUBFOLDERS`, `_paged_files`.
- Produces:
  - `BATCH_STAGING_FOLDER = "_batch"` (added to `_RESERVED_SUBFOLDERS`).
  - `upload_image(folder_id: str, name: str, data: bytes, mime: str = "image/png") -> tuple[str, str | None]` → `(file_id, thumbnail_link)`.
  - `list_folder_image_ids(folder_id: str, limit: int = 14) -> list[str]` (loose + one level of subfolders, ids only, no thumbnails).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_batch_drive.py`:

```python
import pytest

from mockup_generator.integrations import drive_client


class FakeFiles:
    def __init__(self, recorder, create_ret=None, list_ret=None):
        self.rec, self._create_ret, self._list_ret = recorder, create_ret or {}, list_ret or {}

    def create(self, **kw):
        self.rec["create"] = kw
        return _Exec(self._create_ret)

    def list(self, **kw):
        self.rec.setdefault("list", []).append(kw)
        return _Exec(self._list_ret)


class _Exec:
    def __init__(self, ret): self._ret = ret
    def execute(self): return self._ret


class FakeSvc:
    def __init__(self, files): self._files = files
    def files(self): return self._files


def test_batch_folder_is_excluded_from_backfill_scan():
    assert drive_client.BATCH_STAGING_FOLDER in drive_client._RESERVED_SUBFOLDERS


def test_upload_image_creates_media_and_returns_id_and_thumb(monkeypatch):
    rec = {}
    files = FakeFiles(rec, create_ret={"id": "new123", "thumbnailLink": "http://t"})
    monkeypatch.setattr(drive_client, "_clients", lambda: (FakeSvc(files), object()))
    fid, thumb = drive_client.upload_image("parent1", "BC25001_red.png", b"PNGBYTES")
    assert fid == "new123" and thumb == "http://t"
    body = rec["create"]["body"]
    assert body["name"] == "BC25001_red.png" and body["parents"] == ["parent1"]


def test_list_folder_image_ids_returns_ids_capped(monkeypatch):
    monkeypatch.setattr(drive_client, "_clients", lambda: (object(), object()))
    calls = {"n": 0}

    def fake_paged(svc, q, fields):
        calls["n"] += 1
        # first call: top level (2 images + 1 subfolder); second call: subfolder images
        if calls["n"] == 1:
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_batch_drive.py -v`
Expected: FAIL (`AttributeError: ... BATCH_STAGING_FOLDER` / `upload_image`).

- [ ] **Step 3: Add the constant to the reserved set**

In `mockup_generator/integrations/drive_client.py`, find the reserved-subfolder block (currently near line 60-64):

```python
ARCHIVE_FOLDER = "published"
REJECTED_FOLDER = "rejected"
EDIT_FOLDER = "edit"
SKIPPED_FOLDER = "skipped"
_RESERVED_SUBFOLDERS = {ARCHIVE_FOLDER, REJECTED_FOLDER, EDIT_FOLDER, SKIPPED_FOLDER}
```

Change it to:

```python
ARCHIVE_FOLDER = "published"
REJECTED_FOLDER = "rejected"
EDIT_FOLDER = "edit"
SKIPPED_FOLDER = "skipped"
BATCH_STAGING_FOLDER = "_batch"  # unapproved Batch Generate mockups; excluded from the backfill scan
_RESERVED_SUBFOLDERS = {ARCHIVE_FOLDER, REJECTED_FOLDER, EDIT_FOLDER, SKIPPED_FOLDER, BATCH_STAGING_FOLDER}
```

- [ ] **Step 4: Add the upload import**

At the top of `drive_client.py`, the existing import is:

```python
from googleapiclient.http import MediaIoBaseDownload
```

Change it to:

```python
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
```

- [ ] **Step 5: Add `upload_image` and `list_folder_image_ids`**

Add both functions after `download_file` (near line 227):

```python
def upload_image(folder_id: str, name: str, data: bytes, mime: str = "image/png") -> tuple[str, str | None]:
    """Upload ``data`` as a new file ``name`` into ``folder_id``. Returns
    ``(file_id, thumbnail_link)``. The service account owns files it creates,
    so these can later be deleted with ``delete_file`` (unlike human-owned files)."""
    svc, _ = _clients()
    media = MediaIoBaseUpload(BytesIO(data), mimetype=mime, resumable=False)
    created = svc.files().create(
        body={"name": name, "parents": [folder_id]},
        media_body=media, fields="id,thumbnailLink", supportsAllDrives=True,
    ).execute()
    return created["id"], created.get("thumbnailLink")


def list_folder_image_ids(folder_id: str, limit: int = 14) -> list[str]:
    """Return image file ids in ``folder_id`` (loose + one level of subfolders),
    capped at ``limit``. Ids only — no thumbnails fetched, so this is cheap enough
    to call per product during batch enqueue."""
    svc, _ = _clients()
    ids: list[str] = []
    subfolders: list[str] = []
    top = _paged_files(
        svc, f"'{folder_id}' in parents and trashed = false", "files(id,name,mimeType)",
    )
    for f in top:
        if f.get("mimeType") == _FOLDER_MIME:
            subfolders.append(f["id"])
        elif (f.get("mimeType") or "").startswith("image/"):
            ids.append(f["id"])
    for sub in subfolders:
        if len(ids) >= limit:
            break
        for f in _paged_files(
            svc, f"'{sub}' in parents and mimeType contains 'image/' and trashed = false",
            "files(id,name,mimeType)",
        ):
            ids.append(f["id"])
    return ids[:limit]
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `poetry run pytest tests/test_batch_drive.py -v`
Expected: PASS (3 tests).

- [ ] **Step 7: Commit**

```bash
git add mockup_generator/integrations/drive_client.py tests/test_batch_drive.py
git commit -m "feat(batch): drive upload + id-lister helpers, exclude _batch from scan"
```

---

## Task 3: Enqueue planner service

**Files:**
- Create: `mockup_generator/services/batch_enqueue.py`
- Test: `tests/test_batch_enqueue.py`

**Interfaces:**
- Consumes: `products_repo.list_products`/`.Product`, `variants_repo.list_colors`, `prompts_repo.list_by_category`, `defaults.prompt_for_category`, `drive_client.extract_folder_id`/`list_folder_image_ids`, `batch_items_repo` status constants.
- Produces:
  - `resolve_category_prompt(db, categoryid: str) -> str | None`
  - `compose_prompt(color: str | None, body: str) -> str`
  - `plan_cards(db, *, category: str | None, count: int, model: str, resolution: str, aspect_ratio: str, batch_id: str, created_by: str | None) -> tuple[list[dict], list[dict]]` → `(rows, skipped)` where each skipped is `{"productid": str, "reason": str}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_batch_enqueue.py`:

```python
import pytest

from mockup_generator.services import batch_enqueue as be
from mockup_generator.db import batch_items_repo as repo
from mockup_generator.db.products_repo import Product
from mockup_generator.db.prompts_repo import Prompt


def _product(pid, cat="SA", url="https://drive.google.com/drive/folders/FID"):
    return Product(productid=pid, name=pid, categoryid=cat, category_name="Saree",
                   base_mockup=False, producturl=url)


def test_compose_prompt_prefixes_color():
    assert be.compose_prompt("Red", "BODY").startswith("Make the professional mockup of the Red product.")
    assert "BODY" in be.compose_prompt("Red", "BODY")


def test_compose_prompt_colorless():
    assert be.compose_prompt(None, "BODY").startswith("Make the professional mockup of the product.")


def test_resolve_prompt_prefers_db_default(monkeypatch):
    monkeypatch.setattr(be.prompts_repo, "list_by_category",
                        lambda db, cid: [Prompt(1, cid, "Default", "DBBODY", True)])
    assert be.resolve_category_prompt(object(), "SA") == "DBBODY"


def test_resolve_prompt_falls_back_to_constant(monkeypatch):
    monkeypatch.setattr(be.prompts_repo, "list_by_category", lambda db, cid: [])
    monkeypatch.setattr(be, "prompt_for_category", lambda cid: "CONSTBODY")
    assert be.resolve_category_prompt(object(), "SA") == "CONSTBODY"


def test_resolve_prompt_none_when_absent(monkeypatch):
    monkeypatch.setattr(be.prompts_repo, "list_by_category", lambda db, cid: [])
    monkeypatch.setattr(be, "prompt_for_category", lambda cid: None)
    assert be.resolve_category_prompt(object(), "SA") is None


def test_plan_cards_one_row_per_color_and_skips(monkeypatch):
    monkeypatch.setattr(be.products_repo, "list_products",
                        lambda db, **k: [_product("BC1"), _product("BC2"), _product("BC3", url=None)])
    monkeypatch.setattr(be, "resolve_category_prompt",
                        lambda db, cid: "BODY" if cid == "SA" else None)
    monkeypatch.setattr(be.drive_client, "extract_folder_id", lambda url: "FID" if url else None)
    imgs = {"BC1": ["a", "b"], "BC2": []}
    monkeypatch.setattr(be.drive_client, "list_folder_image_ids", lambda fid, limit=14: imgs.get("BC1"))
    # BC2 has no images -> skip; make list return per-product via productid lookup:
    def fake_ids(fid, limit=14):
        return imgs["BC1"]  # only BC1 reached (BC2 short-circuits via colors? see below)
    monkeypatch.setattr(be.variants_repo, "list_colors",
                        lambda db, pid: ["Red", "Blue"] if pid == "BC1" else [])

    rows, skipped = be.plan_cards(object(), category="SA", count=10, model="m",
                                  resolution="4K", aspect_ratio="1:1",
                                  batch_id="b1", created_by="u1")
    # BC1: 2 colors -> 2 rows; BC2: no images -> skip; BC3: no drive folder -> skip
    assert len(rows) == 2
    assert {r["color"] for r in rows} == {"Red", "Blue"}
    assert all(r["status"] == repo.QUEUED and r["image_ids"] == ["a", "b"] for r in rows)
    reasons = {s["productid"]: s["reason"] for s in skipped}
    assert "no images" in reasons["BC2"] and "drive folder" in reasons["BC3"]


def test_plan_cards_colorless_product_gets_one_row(monkeypatch):
    monkeypatch.setattr(be.products_repo, "list_products", lambda db, **k: [_product("BC1")])
    monkeypatch.setattr(be, "resolve_category_prompt", lambda db, cid: "BODY")
    monkeypatch.setattr(be.drive_client, "extract_folder_id", lambda url: "FID")
    monkeypatch.setattr(be.drive_client, "list_folder_image_ids", lambda fid, limit=14: ["a"])
    monkeypatch.setattr(be.variants_repo, "list_colors", lambda db, pid: [])
    rows, skipped = be.plan_cards(object(), category="SA", count=10, model="m",
                                  resolution="4K", aspect_ratio="1:1",
                                  batch_id="b1", created_by=None)
    assert len(rows) == 1 and rows[0]["color"] is None
    assert rows[0]["prompt_text"].startswith("Make the professional mockup of the product.")


def test_plan_cards_skips_product_with_no_prompt(monkeypatch):
    monkeypatch.setattr(be.products_repo, "list_products",
                        lambda db, **k: [_product("BC1", cat="ZZZ")])
    monkeypatch.setattr(be, "resolve_category_prompt", lambda db, cid: None)
    rows, skipped = be.plan_cards(object(), category="ZZZ", count=10, model="m",
                                  resolution="4K", aspect_ratio="1:1",
                                  batch_id="b1", created_by=None)
    assert rows == []
    assert "no prompt" in skipped[0]["reason"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_batch_enqueue.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write the service**

Create `mockup_generator/services/batch_enqueue.py`:

```python
"""Batch Generate enqueue planning.

Pure planning: given a category + count, resolve which products get cards, one
per color, with a composed prompt — and collect a reason for every product that
is skipped (no drive folder, no images, no prompt). Does not touch the DB writer
or the worker; the router persists ``rows`` and kicks the worker.
"""

from __future__ import annotations

from mockup_generator.db import batch_items_repo as repo
from mockup_generator.db import products_repo, prompts_repo, variants_repo
from mockup_generator.integrations import drive_client
from mockup_generator.prompts.defaults import prompt_for_category

_PREFIX_COLOR = "Make the professional mockup of the {color} product."
_PREFIX_PLAIN = "Make the professional mockup of the product."


def compose_prompt(color: str | None, body: str) -> str:
    prefix = _PREFIX_COLOR.format(color=color) if color else _PREFIX_PLAIN
    return f"{prefix}\n\n{body}"


def resolve_category_prompt(db, categoryid: str) -> str | None:
    """DB default -> hardcoded CATEGORY_PROMPTS -> None."""
    for p in prompts_repo.list_by_category(db, categoryid):
        if p.is_default:
            return p.body
    return prompt_for_category(categoryid)


def plan_cards(
    db, *, category: str | None, count: int, model: str, resolution: str,
    aspect_ratio: str, batch_id: str, created_by: str | None,
) -> tuple[list[dict], list[dict]]:
    products = products_repo.list_products(db, category=category, pending=True, limit=count)
    rows: list[dict] = []
    skipped: list[dict] = []

    for p in products:
        body = resolve_category_prompt(db, p.categoryid)
        if not body:
            skipped.append({"productid": p.productid, "reason": f"no prompt for category {p.categoryid}"})
            continue
        folder_id = drive_client.extract_folder_id(p.producturl)
        if not folder_id:
            skipped.append({"productid": p.productid, "reason": "no drive folder"})
            continue
        image_ids = drive_client.list_folder_image_ids(folder_id)
        if not image_ids:
            skipped.append({"productid": p.productid, "reason": "no images"})
            continue
        colors = variants_repo.list_colors(db, p.productid) or [None]
        for color in colors:
            rows.append({
                "batch_id": batch_id,
                "productid": p.productid,
                "color": color,
                "image_ids": image_ids,
                "prompt_text": compose_prompt(color, body),
                "status": repo.QUEUED,
                "model": model,
                "resolution": resolution,
                "aspect_ratio": aspect_ratio,
                "created_by": created_by,
            })

    return rows, skipped
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_batch_enqueue.py -v`
Expected: PASS (8 tests). (If `test_plan_cards_one_row_per_color_and_skips` needs the unused `fake_ids` helper removed, delete that dead local — it is not wired.)

- [ ] **Step 5: Commit**

```bash
git add mockup_generator/services/batch_enqueue.py tests/test_batch_enqueue.py
git commit -m "feat(batch): enqueue planner (prompt resolution, per-color cards, skips)"
```

---

## Task 4: Background worker

**Files:**
- Create: `backend/services/batch_worker.py`
- Test: `tests/test_batch_worker.py`

**Interfaces:**
- Consumes: `batch_items_repo` (`claim_next_queued`, `transition`, `reset_orphaned_generating`, status constants, `BatchRow`), `drive_client` (`download_file`, `ensure_subfolder`, `upload_image`, `BATCH_STAGING_FOLDER`), `generation.service.generate_mockup_bytes`, `settings.generated_mockups_folder_id`.
- Produces:
  - `_spawn(fn, *args) -> None` (test-overridable, mirrors `generate._spawn`)
  - `run_one(db) -> bool` (claim + generate one card; False when nothing queued)
  - `run_worker(db) -> None` (loop `run_one` until drained; single-instance via lock)
  - `ensure_running(db) -> None` (spawn `run_worker` if idle)
  - `reset_orphaned(db) -> int`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_batch_worker.py`:

```python
import pytest

from backend.services import batch_worker as bw
from mockup_generator.db import batch_items_repo as repo
from mockup_generator.db.batch_items_repo import BatchRow


def _row(id=1):
    return BatchRow(id=id, batch_id="b1", productid="BC1", color="Red",
                    image_ids=["a", "b"], prompt_text="p", status=repo.GENERATING,
                    drive_file_id=None, thumbnail_link=None, error=None,
                    model="m", resolution="4K", aspect_ratio="1:1")


def test_run_one_generates_stages_and_marks_ready(monkeypatch):
    claimed = {"n": 0}
    def fake_claim(db):
        claimed["n"] += 1
        return _row() if claimed["n"] == 1 else None
    monkeypatch.setattr(bw.repo, "claim_next_queued", fake_claim)
    monkeypatch.setattr(bw.drive_client, "download_file", lambda fid: _tiny_png())
    monkeypatch.setattr(bw.drive_client, "ensure_subfolder", lambda parent, name: "stagefolder")
    monkeypatch.setattr(bw.drive_client, "upload_image",
                        lambda folder, name, data, mime="image/png": ("drv9", "thumb9"))
    monkeypatch.setattr(bw.service, "generate_mockup_bytes", lambda images, prompt, **k: b"PNG")
    updates = []
    monkeypatch.setattr(bw.repo, "transition",
                        lambda db, *, item_id, expect, to, **f: updates.append((item_id, to, f)) or True)

    assert bw.run_one(object()) is True
    assert updates[0][1] == repo.READY
    assert updates[0][2]["drive_file_id"] == "drv9" and updates[0][2]["thumbnail_link"] == "thumb9"


def test_run_one_marks_failed_on_error(monkeypatch):
    monkeypatch.setattr(bw.repo, "claim_next_queued", lambda db: _row())
    monkeypatch.setattr(bw.drive_client, "download_file", lambda fid: _tiny_png())
    monkeypatch.setattr(bw.service, "generate_mockup_bytes",
                        lambda images, prompt, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    updates = []
    monkeypatch.setattr(bw.repo, "transition",
                        lambda db, *, item_id, expect, to, **f: updates.append((to, f)) or True)
    assert bw.run_one(object()) is True
    assert updates[0][0] == repo.FAILED and "boom" in updates[0][1]["error"]


def test_run_one_returns_false_when_nothing_queued(monkeypatch):
    monkeypatch.setattr(bw.repo, "claim_next_queued", lambda db: None)
    assert bw.run_one(object()) is False


def _tiny_png() -> bytes:
    from io import BytesIO
    from PIL import Image
    buf = BytesIO(); Image.new("RGB", (2, 2)).save(buf, "PNG"); return buf.getvalue()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_batch_worker.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write the worker**

Create `backend/services/batch_worker.py`:

```python
"""Batch Generate background worker.

A single worker thread drains ``queued`` rows one at a time: claim (queued ->
generating), download the product's source images, generate one mockup, stage
the PNG in the ``_batch`` Drive folder, and mark the row ``ready`` (or ``failed``
with the error). Sequential by design — the generator already retries rate
limits. Resumable: a crash leaves rows ``generating``; ``reset_orphaned`` (called
at startup) returns them to ``queued`` for the next sweep.
"""

from __future__ import annotations

import logging
import threading
from io import BytesIO

from PIL import Image
from supabase import Client

from mockup_generator.config import settings
from mockup_generator.db import batch_items_repo as repo
from mockup_generator.generation import service
from mockup_generator.integrations import drive_client

log = logging.getLogger(__name__)

_lock = threading.Lock()
_running = False


def _spawn(fn, *args) -> None:
    """Run ``fn`` off the request thread. Overridden in tests to run inline."""
    threading.Thread(target=fn, args=args, daemon=True).start()


def _staging_name(row: repo.BatchRow) -> str:
    color = (row.color or "nocolor").strip().lower().replace(" ", "-") or "nocolor"
    return f"{row.productid}_{color}_{row.id}.png"


def run_one(db: Client) -> bool:
    """Claim and process one queued card. Returns False when nothing is queued."""
    row = repo.claim_next_queued(db)
    if row is None:
        return False
    try:
        images = [Image.open(BytesIO(drive_client.download_file(fid))) for fid in row.image_ids]
        png = service.generate_mockup_bytes(
            images, row.prompt_text, model=row.model,
            resolution=row.resolution, aspect_ratio=row.aspect_ratio,
        )
        folder = drive_client.ensure_subfolder(
            settings.generated_mockups_folder_id, drive_client.BATCH_STAGING_FOLDER)
        file_id, thumb = drive_client.upload_image(folder, _staging_name(row), png)
        repo.transition(db, item_id=row.id, expect=repo.GENERATING, to=repo.READY,
                        drive_file_id=file_id, thumbnail_link=thumb, error=None)
    except Exception as exc:  # noqa: BLE001 - record the failure on the card and continue
        log.warning("batch item %s generation failed: %s", row.id, exc)
        repo.transition(db, item_id=row.id, expect=repo.GENERATING, to=repo.FAILED,
                        error=str(exc))
    return True


def run_worker(db: Client) -> None:
    """Drain the queue. Single-instance: a second call returns immediately."""
    global _running
    with _lock:
        if _running:
            return
        _running = True
    try:
        while run_one(db):
            pass
    finally:
        with _lock:
            _running = False


def ensure_running(db: Client) -> None:
    """Start the worker off-thread if it isn't already draining."""
    with _lock:
        if _running:
            return
    _spawn(run_worker, db)


def reset_orphaned(db: Client) -> int:
    return repo.reset_orphaned_generating(db)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_batch_worker.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/services/batch_worker.py tests/test_batch_worker.py
git commit -m "feat(batch): sequential background worker (claim/generate/stage)"
```

---

## Task 5: Schemas + router (enqueue, items, counts, sources)

**Files:**
- Modify: `backend/schemas.py`
- Create: `backend/routers/batch.py`
- Test: `tests/test_batch_api.py`

**Interfaces:**
- Consumes: `get_current_user`, `get_db`, `batch_items_repo`, `batch_enqueue`, `batch_worker`, `products_repo.names_for`, `drive_client.thumbnails_for`/`download_file`/`extract_folder_id`/`list_folder_image_groups`, `variants_repo.list_colors`.
- Produces (schemas): `BatchEnqueueRequest`, `BatchEnqueueResponse`, `BatchItemOut`, `BatchItemsResponse`, `BatchCountsResponse`, `BatchActionResponse`, `BatchAcceptRequest`, `BatchEditRequest`.
- Produces (routes): `POST /api/batch`, `GET /api/batch/items`, `GET /api/batch/counts`, `GET /api/batch/{item_id}/sources`.
- Router module exposes `items_repo`, `enqueue`, `worker`, `drive_client`, `products_repo`, `variants_repo` as module attributes for test monkeypatching (import them at module top).

- [ ] **Step 1: Add schemas**

Append to `backend/schemas.py` (match the existing Pydantic style — check whether the file uses `pydantic.BaseModel` v2; mirror neighboring models like `BackfillItemsResponse`):

```python
class BatchEnqueueRequest(BaseModel):
    category: str | None = None
    count: int = Field(ge=1, le=100)
    model: str | None = None
    resolution: str | None = None
    aspect_ratio: str | None = None


class BatchSkip(BaseModel):
    productid: str
    reason: str


class BatchEnqueueResponse(BaseModel):
    batch_id: str
    queued: int
    skipped: list[BatchSkip]


class BatchItemOut(BaseModel):
    id: int
    productid: str
    product_name: str | None
    color: str | None
    status: str
    image_ids: list[str]
    drive_file_id: str | None
    generated_thumb_url: str | None
    error: str | None


class BatchItemsResponse(BaseModel):
    total: int
    offset: int
    limit: int
    items: list[BatchItemOut]


class BatchCountsResponse(BaseModel):
    counts: dict[str, int]


class BatchActionResponse(BaseModel):
    status: str
    warning: str | None = None


class BatchAcceptRequest(BaseModel):
    color: str | None = None
    theme_name: str | None = None
    aspect_ratio: str | None = None


class BatchEditRequest(BaseModel):
    prompt_note: str | None = None
    image_ids: list[str] | None = None
```

(If `Field` isn't imported yet in `schemas.py`, add it to the `from pydantic import ...` line.)

- [ ] **Step 2: Write the failing tests (reads + enqueue)**

Create `tests/test_batch_api.py`:

```python
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.auth import get_current_user, CurrentUser
from backend.deps import get_db
from backend.routers import batch as bx
from mockup_generator.db.batch_items_repo import BatchRow
from mockup_generator.db.profiles_repo import Profile


@pytest.fixture
def client():
    u = CurrentUser(id="u1", email="a@b.c", role="user",
                    profile=Profile(id="u1", email="a@b.c", role="user", is_active=True))
    app.dependency_overrides[get_current_user] = lambda: u
    app.dependency_overrides[get_db] = lambda: object()
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _row(id=1, status="ready", drv="drv1"):
    return BatchRow(id=id, batch_id="b1", productid="BC25001", color="Red",
                    image_ids=["a", "b"], prompt_text="p", status=status,
                    drive_file_id=drv, thumbnail_link=f"l-{id}", error=None,
                    model="m", resolution="4K", aspect_ratio="1:1")


def test_enqueue_plans_inserts_and_starts_worker(client, monkeypatch):
    monkeypatch.setattr(bx.enqueue, "plan_cards",
                        lambda db, **k: ([{"productid": "BC1"}], [{"productid": "BC2", "reason": "no images"}]))
    inserted = {}
    monkeypatch.setattr(bx.items_repo, "insert_many",
                        lambda db, rows: inserted.setdefault("rows", rows) or len(rows))
    started = {"n": 0}
    monkeypatch.setattr(bx.worker, "ensure_running", lambda db: started.__setitem__("n", started["n"] + 1))

    r = client.post("/api/batch", json={"category": "SA", "count": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["queued"] == 1 and body["skipped"][0]["reason"] == "no images"
    assert started["n"] == 1 and len(inserted["rows"]) == 1


def test_enqueue_rejects_out_of_range_count(client):
    assert client.post("/api/batch", json={"count": 0}).status_code == 422
    assert client.post("/api/batch", json={"count": 101}).status_code == 422


def test_items_ready_tab_enriches(client, monkeypatch):
    monkeypatch.setattr(bx.items_repo, "page",
                        lambda db, *, statuses, offset, limit: ([_row()], 1))
    monkeypatch.setattr(bx.products_repo, "names_for", lambda db, pids: {"BC25001": "Saree"})
    monkeypatch.setattr(bx.drive_client, "thumbnails_for",
                        lambda items: {i["file_id"]: f"data:{i['file_id']}" for i in items})
    r = client.get("/api/batch/items?tab=ready&offset=0&limit=20")
    assert r.status_code == 200
    it = r.json()["items"][0]
    assert it["product_name"] == "Saree" and it["generated_thumb_url"] == "data:drv1"
    assert it["color"] == "Red"


def test_items_in_progress_tab_queries_two_statuses(client, monkeypatch):
    seen = {}
    def fake_page(db, *, statuses, offset, limit):
        seen["statuses"] = statuses
        return [], 0
    monkeypatch.setattr(bx.items_repo, "page", fake_page)
    monkeypatch.setattr(bx.products_repo, "names_for", lambda db, pids: {})
    monkeypatch.setattr(bx.drive_client, "thumbnails_for", lambda items: {})
    r = client.get("/api/batch/items?tab=in_progress")
    assert r.status_code == 200
    assert set(seen["statuses"]) == {"queued", "generating"}


def test_items_rejects_unknown_tab(client):
    assert client.get("/api/batch/items?tab=bogus").status_code == 400


def test_counts(client, monkeypatch):
    monkeypatch.setattr(bx.items_repo, "counts",
                        lambda db: {"ready": 2, "queued": 1, "generating": 0,
                                    "failed": 0, "published": 3, "rejected": 1})
    r = client.get("/api/batch/counts")
    assert r.status_code == 200 and r.json()["counts"]["ready"] == 2
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `poetry run pytest tests/test_batch_api.py -v`
Expected: FAIL (router not found / not mounted).

- [ ] **Step 4: Write the router (reads + enqueue + sources)**

Create `backend/routers/batch.py`:

```python
"""Batch Generate endpoints (DB-backed worklist + background worker)."""

from __future__ import annotations

import base64
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from supabase import Client

from backend.auth import CurrentUser, get_current_user
from backend.deps import get_db
from backend.schemas import (
    BatchAcceptRequest, BatchActionResponse, BatchCountsResponse, BatchEditRequest,
    BatchEnqueueRequest, BatchEnqueueResponse, BatchItemOut, BatchItemsResponse,
)
from mockup_generator.config import settings
from mockup_generator.db import batch_items_repo as items_repo
from mockup_generator.db import products_repo, variants_repo
from mockup_generator.generation import publish
from mockup_generator.integrations import drive_client
from mockup_generator.integrations.drive_client import DriveNotConfigured
from mockup_generator.services import batch_enqueue as enqueue
from backend.services import batch_worker as worker

router = APIRouter(prefix="/api/batch", tags=["batch"])
log = logging.getLogger(__name__)

_ALREADY_HANDLED = "This card was already handled."

# tab name -> statuses queried for that sub-tab
_TABS: dict[str, list[str]] = {
    "ready": [items_repo.READY],
    "in_progress": [items_repo.QUEUED, items_repo.GENERATING],
    "failed": [items_repo.FAILED],
    "history": [items_repo.PUBLISHED, items_repo.REJECTED],
}


def _claim(db: Client, item_id: int, expect: str, to: str, **fields) -> None:
    if not items_repo.transition(db, item_id=item_id, expect=expect, to=to, **fields):
        raise HTTPException(status_code=409, detail=_ALREADY_HANDLED)


def _discard_drive_file(file_id: str) -> str | None:
    """Remove a staged batch file from the staging area. Delete it (the SA owns
    files it uploaded); if deletion isn't permitted (e.g. a Shared Drive role
    without delete rights), fall back to moving it into the ``published/`` archive
    folder so it still leaves ``_batch``. Returns a warning or None."""
    try:
        drive_client.delete_file(file_id)
        return None
    except Exception as exc:  # noqa: BLE001 - fall back to archiving instead
        log.warning("batch staged %s delete failed, moving to %s: %s",
                    file_id, drive_client.ARCHIVE_FOLDER, exc)
    try:
        archive = drive_client.ensure_subfolder(
            settings.generated_mockups_folder_id, drive_client.ARCHIVE_FOLDER)
        drive_client.move_file(file_id, archive)
        return None
    except Exception as exc:  # noqa: BLE001 - neither delete nor archive worked
        log.warning("batch staged %s could not be deleted or archived: %s", file_id, exc)
        return "Done, but the staged Drive file could not be removed."


@router.post("", response_model=BatchEnqueueResponse)
def enqueue_batch(req: BatchEnqueueRequest,
                  user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    batch_id = str(uuid.uuid4())
    rows, skipped = enqueue.plan_cards(
        db, category=req.category, count=req.count,
        model=req.model or settings.gemini_image_model,
        resolution=req.resolution or "4K",
        aspect_ratio=req.aspect_ratio or "1:1",
        batch_id=batch_id, created_by=user.id,
    )
    items_repo.insert_many(db, rows)
    if rows:
        worker.ensure_running(db)
    return BatchEnqueueResponse(batch_id=batch_id, queued=len(rows), skipped=skipped)


@router.get("/items", response_model=BatchItemsResponse)
def list_items(tab: str = "ready", offset: int = 0, limit: int = 20,
               user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    statuses = _TABS.get(tab)
    if statuses is None:
        raise HTTPException(status_code=400, detail=f"Unknown tab: {tab}")
    rows, total = items_repo.page(db, statuses=statuses, offset=offset, limit=limit)
    names = products_repo.names_for(db, [r.productid for r in rows])
    thumb_src = [{"file_id": r.drive_file_id, "thumbnail_link": r.thumbnail_link}
                 for r in rows if r.drive_file_id]
    thumbs = drive_client.thumbnails_for(thumb_src) if thumb_src else {}
    items = [
        BatchItemOut(
            id=r.id, productid=r.productid, product_name=names.get(r.productid),
            color=r.color, status=r.status, image_ids=r.image_ids,
            drive_file_id=r.drive_file_id,
            generated_thumb_url=thumbs.get(r.drive_file_id) if r.drive_file_id else None,
            error=r.error,
        )
        for r in rows
    ]
    return BatchItemsResponse(total=total, offset=offset, limit=limit, items=items)


@router.get("/counts", response_model=BatchCountsResponse)
def counts(user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    return BatchCountsResponse(counts=items_repo.counts(db))


@router.get("/{item_id}/sources")
def card_sources(item_id: int,
                 user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    row = items_repo.get(db, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Card not found.")
    colors = variants_repo.list_colors(db, row.productid)
    sources = []
    for fid in row.image_ids:
        try:
            data = drive_client.download_file(fid)
            sources.append({"id": fid, "data_uri": "data:image/*;base64," + base64.b64encode(data).decode()})
        except DriveNotConfigured as exc:
            raise HTTPException(status_code=503, detail="Drive access is not configured on the server") from exc
        except Exception as exc:  # noqa: BLE001 - a missing source shouldn't break the card
            log.warning("batch source %s could not load: %s", fid, exc)
    generated = None
    if row.drive_file_id:
        try:
            g = drive_client.download_file(row.drive_file_id)
            generated = "data:image/png;base64," + base64.b64encode(g).decode()
        except Exception as exc:  # noqa: BLE001
            log.warning("batch generated %s could not load: %s", row.drive_file_id, exc)
    return {"sources": sources, "generated_preview": generated,
            "colors": colors, "color": row.color, "image_ids": row.image_ids}
```

(Leave the accept/edit/reject/retry endpoints for Task 6 — they are added to this same file.)

- [ ] **Step 5: Mount the router (temporary, finalized in Task 7)**

In `backend/main.py`, add the import near the other router imports and register it so the tests can reach it:

```python
from backend.routers import batch as batch_router
```
and after the other `app.include_router(...)` calls:
```python
app.include_router(batch_router.router)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `poetry run pytest tests/test_batch_api.py -v`
Expected: PASS (6 tests).

- [ ] **Step 7: Commit**

```bash
git add backend/schemas.py backend/routers/batch.py backend/main.py tests/test_batch_api.py
git commit -m "feat(batch): enqueue/items/counts/sources endpoints + schemas"
```

---

## Task 6: Accept / Edit / Reject / Retry endpoints

**Files:**
- Modify: `backend/routers/batch.py`
- Test: `tests/test_batch_api.py` (add cases)

**Interfaces:**
- Consumes: `publish.publish_image`, `drive_client.download_file`/`delete_file`, `items_repo.get`/`transition`, `worker.ensure_running`.
- Produces routes: `POST /api/batch/{item_id}/accept`, `/edit`, `/reject`, `/retry`.

- [ ] **Step 1: Write the failing tests (append to `tests/test_batch_api.py`)**

```python
def test_accept_publishes_deletes_drive_and_marks_published(client, monkeypatch):
    monkeypatch.setattr(bx.items_repo, "get", lambda db, i: _row(id=i))
    monkeypatch.setattr(bx.items_repo, "transition",
                        lambda db, *, item_id, expect, to, **f: True)
    monkeypatch.setattr(bx.drive_client, "download_file", lambda fid: b"PNG")
    published = {}
    monkeypatch.setattr(bx.publish, "publish_image",
                        lambda db, **k: published.update(k) or {"image_url": "u", "variation_id": 7})
    deleted = {}
    monkeypatch.setattr(bx.drive_client, "delete_file", lambda fid: deleted.setdefault("id", fid))
    r = client.post("/api/batch/1/accept", json={})
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert deleted["id"] == "drv1" and published["color"] == "Red"


def test_accept_conflict_when_not_ready(client, monkeypatch):
    monkeypatch.setattr(bx.items_repo, "get", lambda db, i: _row(id=i))
    monkeypatch.setattr(bx.items_repo, "transition",
                        lambda db, *, item_id, expect, to, **f: False)  # lost the row
    r = client.post("/api/batch/1/accept", json={})
    assert r.status_code == 409


def test_reject_deletes_drive_and_marks_rejected(client, monkeypatch):
    monkeypatch.setattr(bx.items_repo, "get", lambda db, i: _row(id=i))
    moved = {"to": None}
    monkeypatch.setattr(bx.items_repo, "transition",
                        lambda db, *, item_id, expect, to, **f: moved.__setitem__("to", to) or True)
    deleted = {}
    monkeypatch.setattr(bx.drive_client, "delete_file", lambda fid: deleted.setdefault("id", fid))
    r = client.post("/api/batch/1/reject")
    assert r.status_code == 200 and moved["to"] == "rejected" and deleted["id"] == "drv1"


def test_accept_archives_when_delete_forbidden(client, monkeypatch):
    monkeypatch.setattr(bx.items_repo, "get", lambda db, i: _row(id=i))
    monkeypatch.setattr(bx.items_repo, "transition", lambda db, *, item_id, expect, to, **f: True)
    monkeypatch.setattr(bx.drive_client, "download_file", lambda fid: b"PNG")
    monkeypatch.setattr(bx.publish, "publish_image",
                        lambda db, **k: {"image_url": "u", "variation_id": 7})
    def _forbidden(fid): raise PermissionError("no delete rights")
    monkeypatch.setattr(bx.drive_client, "delete_file", _forbidden)
    monkeypatch.setattr(bx.drive_client, "ensure_subfolder", lambda parent, name: "pubfolder")
    moved = {}
    monkeypatch.setattr(bx.drive_client, "move_file", lambda fid, parent: moved.update({"fid": fid, "to": parent}))
    r = client.post("/api/batch/1/accept", json={})
    assert r.status_code == 200 and r.json()["warning"] is None  # archived cleanly
    assert moved["fid"] == "drv1" and moved["to"] == "pubfolder"


def test_edit_requeues_with_note_and_clears_drive(client, monkeypatch):
    monkeypatch.setattr(bx.items_repo, "get", lambda db, i: _row(id=i))
    captured = {}
    monkeypatch.setattr(bx.items_repo, "transition",
                        lambda db, *, item_id, expect, to, **f: captured.update({"to": to, **f}) or True)
    monkeypatch.setattr(bx.drive_client, "delete_file", lambda fid: None)
    started = {"n": 0}
    monkeypatch.setattr(bx.worker, "ensure_running", lambda db: started.__setitem__("n", 1))
    r = client.post("/api/batch/1/edit", json={"prompt_note": "brighter", "image_ids": ["a"]})
    assert r.status_code == 200
    assert captured["to"] == "queued" and "brighter" in captured["prompt_text"]
    assert captured["image_ids"] == ["a"] and captured["drive_file_id"] is None
    assert started["n"] == 1


def test_retry_requeues_failed(client, monkeypatch):
    monkeypatch.setattr(bx.items_repo, "get", lambda db, i: _row(id=i, status="failed", drv=None))
    captured = {}
    monkeypatch.setattr(bx.items_repo, "transition",
                        lambda db, *, item_id, expect, to, **f: captured.update({"expect": expect, "to": to}) or True)
    monkeypatch.setattr(bx.worker, "ensure_running", lambda db: None)
    r = client.post("/api/batch/1/retry")
    assert r.status_code == 200 and captured["expect"] == "failed" and captured["to"] == "queued"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_batch_api.py -k "accept or reject or edit or retry" -v`
Expected: FAIL (404 — routes not defined).

- [ ] **Step 3: Add the endpoints to `backend/routers/batch.py`**

Append these routes (and add `worker` is already imported; ensure `publish` import is present — it is):

```python
@router.post("/{item_id}/accept", response_model=BatchActionResponse)
def accept(item_id: int, req: BatchAcceptRequest,
           user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    row = items_repo.get(db, item_id)
    if row is None or not row.drive_file_id:
        raise HTTPException(status_code=404, detail="Card not found or not ready.")
    # Reserve the row so a second reviewer can't also publish it.
    _claim(db, item_id, items_repo.READY, items_repo.PUBLISHED)
    try:
        png = drive_client.download_file(row.drive_file_id)
        result = publish.publish_image(
            db, productid=row.productid, png=png,
            color=req.color if req.color is not None else row.color,
            theme_name=req.theme_name,
            aspect_ratio=req.aspect_ratio or row.aspect_ratio,
            created_by=user.id, prompt_text=row.prompt_text,
        )
    except Exception as exc:  # noqa: BLE001 - revert the claim so the card returns for retry
        items_repo.transition(db, item_id=item_id, expect=items_repo.PUBLISHED, to=items_repo.READY)
        raise HTTPException(status_code=502, detail=f"Could not publish the mockup: {exc}") from exc

    warning = _discard_drive_file(row.drive_file_id)
    log.info("batch %s published as %s", item_id, result["image_url"])
    return BatchActionResponse(status="ok", warning=warning)


@router.post("/{item_id}/reject", response_model=BatchActionResponse)
def reject(item_id: int,
           user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    row = items_repo.get(db, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Card not found.")
    _claim(db, item_id, items_repo.READY, items_repo.REJECTED)
    warning = _discard_drive_file(row.drive_file_id) if row.drive_file_id else None
    return BatchActionResponse(status="ok", warning=warning)


@router.post("/{item_id}/edit", response_model=BatchActionResponse)
def edit(item_id: int, req: BatchEditRequest,
         user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    row = items_repo.get(db, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Card not found.")
    note = (req.prompt_note or "").strip()
    prompt_text = f"{row.prompt_text}\n\nRevision note: {note}" if note else row.prompt_text
    image_ids = req.image_ids if req.image_ids else row.image_ids
    # ready -> queued with the updated prompt/images; clear the stale staged file id.
    _claim(db, item_id, items_repo.READY, items_repo.QUEUED,
           prompt_text=prompt_text, image_ids=image_ids, drive_file_id=None, error=None)
    warning = _discard_drive_file(row.drive_file_id) if row.drive_file_id else None
    worker.ensure_running(db)
    return BatchActionResponse(status="ok", warning=warning)


@router.post("/{item_id}/retry", response_model=BatchActionResponse)
def retry(item_id: int,
          user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    _claim(db, item_id, items_repo.FAILED, items_repo.QUEUED, error=None)
    worker.ensure_running(db)
    return BatchActionResponse(status="ok", warning=None)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_batch_api.py -v`
Expected: PASS (all cases, ~11 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/routers/batch.py tests/test_batch_api.py
git commit -m "feat(batch): accept/edit/reject/retry endpoints"
```

---

## Task 7: main.py wiring (startup resume)

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_batch_api.py` (add one startup case)

**Interfaces:**
- Consumes: `batch_worker.reset_orphaned`, `batch_worker.ensure_running`, `service_client`.
- The router was already mounted in Task 5; this task adds the startup orphan-reset + resume and confirms mounting.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_batch_api.py`:

```python
def test_router_is_mounted(client):
    # /counts requires the router to be registered; 200 (not 404) proves it.
    import backend.routers.batch as bx2
    from unittest.mock import patch
    with patch.object(bx2.items_repo, "counts", lambda db: {s: 0 for s in bx2.items_repo.ALL_STATUSES}):
        assert client.get("/api/batch/counts").status_code == 200
```

- [ ] **Step 2: Add startup resume to the lifespan**

In `backend/main.py`, inside the `lifespan` function, after the Supabase connectivity check and before `yield`, add:

```python
    # Resume any Batch Generate work left over from a previous process: reset
    # rows stuck in 'generating' (a crashed worker) back to 'queued', then kick
    # the worker if anything is pending. Never block startup on this.
    try:
        from backend.services import batch_worker
        db = service_client()
        if db is not None:
            reset = batch_worker.reset_orphaned(db)
            if reset:
                log.info("batch: reset %d orphaned 'generating' rows to 'queued'", reset)
            batch_worker.ensure_running(db)
    except Exception as exc:  # noqa: BLE001 - batch resume is best-effort; boot must not fail
        log.warning("batch resume skipped: %s", exc)
```

(Confirm `service_client` is imported in `main.py` — it is used by the existing lifespan. If only `anon_client`/`service_client` come from a specific module, reuse that same import.)

- [ ] **Step 3: Run the full backend suite**

Run: `poetry run pytest tests/test_batch_api.py tests/test_batch_worker.py -v`
Expected: PASS. Also confirm nothing else broke: `poetry run pytest -q`.

- [ ] **Step 4: Commit**

```bash
git add backend/main.py tests/test_batch_api.py
git commit -m "feat(batch): resume worklist + drain worker on startup"
```

---

## Task 8: Frontend API client

**Files:**
- Modify: `frontend/src/api.ts`

**Interfaces:**
- Consumes: existing `apiFetch<T>` JSON helper and `ApiError`.
- Produces: types `BatchTab`, `BatchItem`, `BatchItems`, `BatchCounts`, `BatchSources`, `BatchEnqueueResult`; functions `enqueueBatch`, `listBatchItems`, `getBatchCounts`, `getBatchSources`, `acceptBatch`, `editBatch`, `rejectBatch`, `retryBatch`.

- [ ] **Step 1: Add types and functions to `frontend/src/api.ts`**

Mirror the existing backfill client block (`listBackfill`, `approveBackfill`, etc.). Add:

```typescript
export type BatchTabId = "ready" | "in_progress" | "failed" | "history";

export interface BatchItem {
  id: number;
  productid: string;
  product_name: string | null;
  color: string | null;
  status: string;
  image_ids: string[];
  drive_file_id: string | null;
  generated_thumb_url: string | null;
  error: string | null;
}

export interface BatchItems {
  total: number;
  offset: number;
  limit: number;
  items: BatchItem[];
}

export interface BatchEnqueueResult {
  batch_id: string;
  queued: number;
  skipped: { productid: string; reason: string }[];
}

export interface BatchSources {
  sources: { id: string; data_uri: string }[];
  generated_preview: string | null;
  colors: string[];
  color: string | null;
  image_ids: string[];
}

export function enqueueBatch(body: {
  category: string | null; count: number;
}): Promise<BatchEnqueueResult> {
  return apiFetch<BatchEnqueueResult>("/api/batch", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function listBatchItems(p: { tab: BatchTabId; offset: number; limit: number }): Promise<BatchItems> {
  const q = new URLSearchParams({ tab: p.tab, offset: String(p.offset), limit: String(p.limit) });
  return apiFetch<BatchItems>(`/api/batch/items?${q}`);
}

export function getBatchCounts(): Promise<{ counts: Record<string, number> }> {
  return apiFetch<{ counts: Record<string, number> }>("/api/batch/counts");
}

export function getBatchSources(id: number): Promise<BatchSources> {
  return apiFetch<BatchSources>(`/api/batch/${id}/sources`);
}

export function acceptBatch(id: number, body: { color?: string | null; theme_name?: string | null; aspect_ratio?: string | null } = {}): Promise<{ status: string; warning: string | null }> {
  return apiFetch(`/api/batch/${id}/accept`, { method: "POST", body: JSON.stringify(body) });
}

export function editBatch(id: number, body: { prompt_note?: string; image_ids?: string[] }): Promise<{ status: string; warning: string | null }> {
  return apiFetch(`/api/batch/${id}/edit`, { method: "POST", body: JSON.stringify(body) });
}

export function rejectBatch(id: number): Promise<{ status: string; warning: string | null }> {
  return apiFetch(`/api/batch/${id}/reject`, { method: "POST" });
}

export function retryBatch(id: number): Promise<{ status: string; warning: string | null }> {
  return apiFetch(`/api/batch/${id}/retry`, { method: "POST" });
}
```

(Check the exact `apiFetch` signature in `api.ts` — if POST bodies elsewhere pass `{ method, body }` with a shared JSON header helper, match that call shape exactly. Reuse the `getCategories` export already present.)

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api.ts
git commit -m "feat(batch): frontend api client for batch endpoints"
```

---

## Task 9: Frontend BatchTab + registration

**Files:**
- Create: `frontend/src/components/BatchTab.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `getCategories`, `enqueueBatch`, `listBatchItems`, `getBatchCounts`, `getBatchSources`, `acceptBatch`, `editBatch`, `rejectBatch`, `retryBatch`, `ApiError`, `useImageLightbox`.
- Produces: `BatchTab` default-exported React component; a `{ id: "batch", label: "Batch Generate" }` tab.

> **Before writing this component, invoke the `ui-ux-pro-max:ui-ux-pro-max` skill** and apply its rules (touch targets ≥44px, visible focus states, contrast, hover-vs-tap). Reuse existing CSS classes from `BackfillTab` (`bf-*`, `tab`, `img-zoom`) so the tab matches the app; add new classes to `frontend/src/index.css` only where needed.

- [ ] **Step 1: Register the tab in `App.tsx`**

Add the import with the other tab imports:
```typescript
import BatchTab from "./components/BatchTab";
```
Add to the `TABS` array (after `products` is fine):
```typescript
{ id: "batch", label: "Batch Generate" },
```
Add a branch to the panel ternary (before the fallback branch):
```tsx
tab === "batch" ? <BatchTab /> :
```

- [ ] **Step 2: Write `BatchTab.tsx`**

Create `frontend/src/components/BatchTab.tsx`. This mirrors `BackfillTab` (status sub-tabs + count badges + offset/limit pagination + optimistic `afterAction`/`run` + 409 handling) and adds the enqueue bar, polling, and a review modal.

```tsx
import { useCallback, useEffect, useState } from "react";
import {
  ApiError, getCategories,
  enqueueBatch, listBatchItems, getBatchCounts, getBatchSources,
  acceptBatch, editBatch, rejectBatch, retryBatch,
  type BatchItem, type BatchTabId, type BatchSources,
} from "../api";
import { useImageLightbox } from "./Lightbox";

const PAGE = 20;
const TABS: { id: BatchTabId; label: string; statuses: string[] }[] = [
  { id: "ready", label: "Ready", statuses: ["ready"] },
  { id: "in_progress", label: "In progress", statuses: ["queued", "generating"] },
  { id: "failed", label: "Failed", statuses: ["failed"] },
  { id: "history", label: "History", statuses: ["published", "rejected"] },
];

function countFor(tabId: BatchTabId, counts: Record<string, number>): number {
  const t = TABS.find((x) => x.id === tabId)!;
  return t.statuses.reduce((n, s) => n + (counts[s] || 0), 0);
}

export default function BatchTab() {
  const [cats, setCats] = useState<{ categoryid: string; name: string }[]>([]);
  const [category, setCategory] = useState<string>("");
  const [count, setCount] = useState<number>(10);
  const [enqueuing, setEnqueuing] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const [tab, setTab] = useState<BatchTabId>("ready");
  const [items, setItems] = useState<BatchItem[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [review, setReview] = useState<BatchItem | null>(null);

  const lightbox = useImageLightbox();

  useEffect(() => { getCategories().then(setCats).catch(() => {}); }, []);

  const loadCounts = useCallback(() => {
    getBatchCounts().then((r) => setCounts(r.counts)).catch(() => {});
  }, []);

  const load = useCallback((t: BatchTabId, off: number) => {
    setLoading(true);
    listBatchItems({ tab: t, offset: off, limit: PAGE })
      .then((r) => { setItems(r.items); setTotal(r.total); setOffset(r.offset); })
      .catch((e) => setNotice(e instanceof ApiError ? e.message : "Failed to load."))
      .finally(() => setLoading(false));
  }, []);

  // Items are fetched 20 at a time, ONLY on tab/page change (and manual refresh
  // or after an action) — no background polling of the list.
  useEffect(() => { load(tab, 0); loadCounts(); }, [tab, load, loadCounts]);

  function refresh() { load(tab, offset); loadCounts(); }

  async function onEnqueue() {
    setEnqueuing(true); setNotice(null);
    try {
      const r = await enqueueBatch({ category: category || null, count });
      const skips = r.skipped.length ? ` · skipped ${r.skipped.length}` : "";
      setNotice(`Queued ${r.queued} card(s)${skips}.`);
      setTab("in_progress"); loadCounts();
    } catch (e) {
      setNotice(e instanceof ApiError ? e.message : "Enqueue failed.");
    } finally {
      setEnqueuing(false);
    }
  }

  function afterAction(id: number) {
    setItems((xs) => xs.filter((x) => x.id !== id));
    setTotal((t) => Math.max(0, t - 1));
    loadCounts();
  }

  async function run(id: number, fn: () => Promise<{ warning: string | null }>) {
    setBusyId(id);
    try {
      const r = await fn();
      afterAction(id);
      setReview(null);
      if (r.warning) setNotice(r.warning);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setNotice("This card was already handled.");
        afterAction(id);
        setReview(null);
      } else {
        setNotice(e instanceof ApiError ? e.message : "Action failed.");
      }
    } finally {
      setBusyId(null);
    }
  }

  const end = Math.min(offset + PAGE, total);

  return (
    <div className="bf">
      <div className="bf-enqueue">
        <select value={category} onChange={(e) => setCategory(e.target.value)} aria-label="Category">
          <option value="">All categories</option>
          {cats.map((c) => <option key={c.categoryid} value={c.categoryid}>{c.name}</option>)}
        </select>
        <input type="number" min={1} max={100} value={count}
               onChange={(e) => setCount(Math.max(1, Math.min(100, Number(e.target.value) || 1)))}
               aria-label="Number of products" />
        <button className="btn primary" onClick={onEnqueue} disabled={enqueuing}>
          {enqueuing ? "Queuing…" : "Generate"}
        </button>
        <button className="btn" onClick={refresh} disabled={loading} aria-label="Refresh">Refresh</button>
      </div>

      {notice && <div className="bf-notice" role="status">{notice}</div>}

      <nav className="tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t.id} className="tab" role="tab" aria-selected={tab === t.id}
                  onClick={() => setTab(t.id)}>
            {t.label} <span className="badge">{countFor(t.id, counts)}</span>
          </button>
        ))}
      </nav>

      <div className="bf-range">{total ? `${offset + 1}–${end} of ${total}` : "0"}</div>

      {loading ? <div className="bf-loading">Loading…</div> : (
        <div className="bf-grid">
          {items.map((it) => (
            <div className="bf-card" key={it.id}>
              {it.generated_thumb_url ? (
                <button className="img-zoom" onClick={() => it.drive_file_id && lightbox.showDrive(it.drive_file_id, it.productid, it.generated_thumb_url || undefined)}>
                  <img src={it.generated_thumb_url} alt={`${it.productid} mockup`} />
                </button>
              ) : (
                <div className="bf-card-status">{it.status === "failed" ? "⚠ failed" : it.status}</div>
              )}
              <div className="bf-card-meta">
                <span className="mono">{it.productid}</span>
                {it.color && <span className="pill">{it.color}</span>}
              </div>
              {it.error && <div className="bf-card-error">{it.error}</div>}
              <div className="bf-card-actions">
                {it.status === "ready" && (
                  <>
                    <button className="btn" disabled={busyId === it.id}
                            onClick={() => setReview(it)}>Review</button>
                    <button className="btn primary" disabled={busyId === it.id}
                            onClick={() => run(it.id, () => acceptBatch(it.id))}>Accept</button>
                    <button className="btn danger" disabled={busyId === it.id}
                            onClick={() => run(it.id, () => rejectBatch(it.id))}>Reject</button>
                  </>
                )}
                {it.status === "failed" && (
                  <button className="btn" disabled={busyId === it.id}
                          onClick={() => run(it.id, () => retryBatch(it.id))}>Retry</button>
                )}
              </div>
            </div>
          ))}
          {!items.length && <div className="bf-empty">Nothing here.</div>}
        </div>
      )}

      <div className="bf-pager">
        <button className="btn" disabled={offset === 0} onClick={() => load(tab, Math.max(0, offset - PAGE))}>Prev</button>
        <span>Page {Math.floor(offset / PAGE) + 1} of {Math.max(1, Math.ceil(total / PAGE))}</span>
        <button className="btn" disabled={end >= total} onClick={() => load(tab, offset + PAGE)}>Next</button>
      </div>

      {review && <ReviewModal item={review} busy={busyId === review.id}
                              onClose={() => setReview(null)}
                              onAccept={(c) => run(review.id, () => acceptBatch(review.id, { color: c }))}
                              onEdit={(note, ids) => run(review.id, () => editBatch(review.id, { prompt_note: note, image_ids: ids }))}
                              onReject={() => run(review.id, () => rejectBatch(review.id))}
                              lightbox={lightbox} />}
      {lightbox.node}
    </div>
  );
}

function ReviewModal(props: {
  item: BatchItem; busy: boolean; onClose: () => void;
  onAccept: (color: string | null) => void;
  onEdit: (note: string, imageIds: string[]) => void;
  onReject: () => void;
  lightbox: ReturnType<typeof useImageLightbox>;
}) {
  const { item, busy, onClose, onAccept, onEdit, onReject, lightbox } = props;
  const [src, setSrc] = useState<BatchSources | null>(null);
  const [note, setNote] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [color, setColor] = useState<string | null>(item.color);

  useEffect(() => {
    getBatchSources(item.id).then((s) => {
      setSrc(s); setPicked(new Set(s.image_ids)); setColor(s.color);
    }).catch(() => {});
  }, [item.id]);

  function toggle(id: string) {
    setPicked((p) => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n; });
  }

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <span className="mono">{item.productid}{item.color ? ` · ${item.color}` : ""}</span>
          <button className="btn" onClick={onClose} aria-label="Close">✕</button>
        </header>

        <div className="modal-body">
          {src?.generated_preview && (
            <button className="img-zoom" onClick={() => lightbox.show(src.generated_preview!, "generated")}>
              <img src={src.generated_preview} alt="generated mockup" className="review-main" />
            </button>
          )}
          <div className="review-sources">
            {(src?.sources || []).map((s) => (
              <label key={s.id} className={`review-src ${picked.has(s.id) ? "on" : ""}`}>
                <input type="checkbox" checked={picked.has(s.id)} onChange={() => toggle(s.id)} />
                <button type="button" className="img-zoom" onClick={() => lightbox.show(s.data_uri, s.id)}>
                  <img src={s.data_uri} alt="source" />
                </button>
              </label>
            ))}
          </div>
          {src && src.colors.length > 0 && (
            <label className="review-color">Color
              <select value={color ?? ""} onChange={(e) => setColor(e.target.value || null)}>
                <option value="">— no color —</option>
                {src.colors.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </label>
          )}
          <textarea placeholder="Revision note (for Edit → regenerate)…"
                    value={note} onChange={(e) => setNote(e.target.value)} rows={2} />
        </div>

        <footer className="modal-actions">
          <button className="btn primary" disabled={busy} onClick={() => onAccept(color)}>Accept &amp; publish</button>
          <button className="btn" disabled={busy || !note.trim()} onClick={() => onEdit(note, Array.from(picked))}>Edit &amp; regenerate</button>
          <button className="btn danger" disabled={busy} onClick={onReject}>Reject</button>
        </footer>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Add any missing CSS**

Reuse existing classes where present. Add minimal rules to `frontend/src/index.css` for any new class not already defined (`.bf-enqueue`, `.bf-grid`, `.bf-card`, `.bf-card-actions`, `.review-sources`, `.review-src`, `.badge`, `.pill`). Keep them consistent with existing `bf-*`/modal styling (spacing, borders, focus outlines).

- [ ] **Step 4: Typecheck + build**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: no type errors; build succeeds.

- [ ] **Step 5: Manual smoke (local)**

Start backend (`poetry run uvicorn backend.main:app --reload`) and frontend (`cd frontend && npm run dev`). Sign in, open **Batch Generate**, pick a category + small count (e.g. 2), click **Generate**. Verify: cards appear under **In progress**, move to **Ready** as they finish (poll), Review modal shows sources + generated, Accept publishes (card leaves Ready, appears in History; product gets a published image), Reject/Edit behave. Confirm the `_batch` folder appears in Drive and staged files are removed on Accept/Reject.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/BatchTab.tsx frontend/src/App.tsx frontend/src/index.css
git commit -m "feat(batch): Batch Generate tab UI (cards, review modal, polling)"
```

---

## Self-Review

**Spec coverage** (each spec section → task):
- §3 data model → Task 1. §4 state machine → Tasks 1 (transitions), 4/6 (usage). §5 enqueue → Task 3 + Task 5 route. §6 worker → Task 4 + Task 7 startup. §7 Drive staging → Task 2 + Task 4. §8 accept/edit/reject → Task 6. §9 API/router → Tasks 5–6, client Task 8. §10 UI → Task 9. §11 out-of-scope respected (no parallelism, no bulk actions, model/res/aspect fixed at enqueue). Prompt-resolution rule → Task 3.
- Gap check: **retry** for failed cards was implied by "failed retryable → queued" in the state machine but not an explicit spec endpoint — added as `POST /{id}/retry` (Task 6) + UI Retry (Task 9). Noted here as an intentional addition.

**Placeholder scan:** No TBD/TODO. Every code step carries full code. Test steps carry real assertions.

**Type consistency:** `transition(**fields)` used consistently (Tasks 1/4/6). `claim_next_queued`/`reset_orphaned_generating` names match repo (Task 1) and callers (Tasks 4/7). `upload_image` returns `(id, thumbnail_link)` (Task 2) and is unpacked that way in the worker (Task 4). `page(statuses=...)` matches router tab mapping (Task 5). `BatchItem`/`BatchSources` fields match `BatchItemOut`/sources payload (Tasks 5/8/9).

**Drive removal:** accept/edit/reject remove the staged file via `_discard_drive_file` — delete first (the SA owns files it uploaded), and if deletion isn't permitted (e.g. a Shared Drive role without delete rights), fall back to moving it into the `published/` archive folder so it always leaves `_batch`. A warning surfaces only if both fail.

**List refresh:** the card list is fetched 20 at a time on tab/page change, manual **Refresh**, or after an action — there is no background polling. In-progress cards move to Ready only when the operator refreshes or changes page.
