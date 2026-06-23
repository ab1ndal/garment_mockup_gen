# Variant-Aware Generate → Approve/Publish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make image generation variant-aware and preview-only, then let a human approve (publish to a public bucket + flip flags + write product image) or discard, with an optional corrected-image upload.

**Architecture:** Generate becomes stateless — it returns a base64 PNG and writes nothing. A new `/api/generate/approve` multipart endpoint is the sole writer: it uploads the (generated-echo or corrected) image to the now-public `mockups` Storage bucket, records a `mockup_variations` audit row, flips `mockups.base_mockup`, and inserts a `productimages` row. New thin repos (`variants_repo`, `productimages_repo`) and a `mockups_repo` helper back it. React shows the preview with Approve / Disapprove / Download / Upload-corrected actions.

**Tech Stack:** Python 3.10, FastAPI, Supabase (Postgres + Storage), Pillow, React + TypeScript (Vite), pytest.

## Global Constraints

- Python version: `>=3.10, <3.11`.
- DB changes are **additive only**: `alter table mockup_variations add column color text;`. No existing table altered; inventory color data is read-only (trim/dedup happens read-side).
- `mockups` Storage bucket is **public, view-only for anon**: anonymous GET by URL only; no anon insert/update/delete/list policy. All writes go through the service-role client.
- Variant granularity = **color only**. Color is **optional** on generate/approve.
- Image generation **requires ≥1 source image** (`image_ids`); the previous fallback-to-all is removed.
- Published image name: `{productid}/{slug(color)}_{shorthex}.png`; when no color: `{productid}/{shorthex}.png`. `shorthex` = first 8 chars of a uuid4 hex.
- **Generate writes nothing** (no Storage, no DB). **Approve is the only writer.** No regeneration in this work.
- `productimages.imageurl` stores the **permanent public URL**.
- **No DB redundancy:** at most one `productimages` row per `(productid, color)`. Re-approve **replaces** that row (delete-then-insert) and **deletes the previously-published Storage object** (orphan cleanup) after the new upload succeeds. `mockup_variations` stays **append-only** (it is the audit/history log — multiple rows are intentional, not redundant).
- All endpoints require an active profile via the existing `get_current_user` dependency.

---

### Task 1: DB migration + public bucket (infra)

Supabase MCP tools require re-auth each session (token expires). Re-authenticate before running these.

**Files:** none (remote Supabase via MCP). Project id: `epotsxdugwfhyeiudjox`.

- [ ] **Step 1: Add the `color` column (additive migration)**

Use MCP `apply_migration` (name: `add_color_to_mockup_variations`):

```sql
alter table mockup_variations add column if not exists color text;
```

- [ ] **Step 2: Make the `mockups` bucket public**

Use MCP `execute_sql`:

```sql
update storage.buckets set public = true where id = 'mockups';
```

- [ ] **Step 3: Verify column + bucket + no anon write policies**

Use MCP `execute_sql`:

```sql
select column_name from information_schema.columns
  where table_name = 'mockup_variations' and column_name = 'color';
select id, public from storage.buckets where id = 'mockups';
select policyname, cmd, roles from pg_policies
  where schemaname = 'storage' and tablename = 'objects';
```

Expected: one `color` row; `public = true`; **no** policy granting `insert`/`update`/`delete` on `storage.objects` to `anon`/`authenticated` for the `mockups` bucket (read-by-URL needs no policy on a public bucket). If such a write policy exists, stop and flag it.

- [ ] **Step 4: Commit a migration note**

```bash
mkdir -p docs/migrations
printf '%s\n' \
  '# 2026-06-23 add color to mockup_variations + public mockups bucket' \
  '' \
  '- `alter table mockup_variations add column if not exists color text;`' \
  '- `update storage.buckets set public = true where id = ''mockups'';`' \
  '- Applied via Supabase MCP (project epotsxdugwfhyeiudjox).' \
  > docs/migrations/2026-06-23-color-and-public-bucket.md
git add docs/migrations/2026-06-23-color-and-public-bucket.md
git commit -m "chore(db): add mockup_variations.color + make mockups bucket public"
```

---

### Task 2: `variants_repo.list_colors`

**Files:**
- Create: `mockup_generator/db/variants_repo.py`
- Test: `tests/test_variants_repo.py`

**Interfaces:**
- Produces: `list_colors(client, productid: str) -> list[str]` — distinct colors, trimmed, case-insensitive deduped (keep first canonical spelling), empties dropped, sorted case-insensitively.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_variants_repo.py
from mockup_generator.db import variants_repo


class _Q:
    def __init__(self, rows):
        self._rows = rows
        self.selected = None
        self.eqd = None

    def select(self, cols):
        self.selected = cols
        return self

    def eq(self, col, val):
        self.eqd = (col, val)
        return self

    def execute(self):
        return type("R", (), {"data": self._rows})()


class _Db:
    def __init__(self, rows):
        self._q = _Q(rows)
        self.table_name = None

    def table(self, name):
        self.table_name = name
        return self._q


def test_list_colors_trims_dedups_drops_empty_and_sorts():
    rows = [
        {"color": "Grey "}, {"color": "grey"}, {"color": "Grey"},
        {"color": ""}, {"color": None}, {"color": "Black"},
        {"color": "  Red  "},
    ]
    db = _Db(rows)
    out = variants_repo.list_colors(db, "BC25001")
    assert db.table_name == "productsizecolors"
    assert db._q.eqd == ("productid", "BC25001")
    # case-insensitive dedup keeps first canonical spelling ("Grey "->"Grey")
    assert out == ["Black", "Grey", "Red"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_variants_repo.py -v`
Expected: FAIL with `ModuleNotFoundError: mockup_generator.db.variants_repo`.

- [ ] **Step 3: Write minimal implementation**

```python
# mockup_generator/db/variants_repo.py
"""Read product variant colors from the existing ``productsizecolors`` table.

Color is the visual variation for a mockup (size does not change appearance).
Inventory data has stray whitespace and case dupes, so we normalize read-side.
"""

from __future__ import annotations

from supabase import Client


def list_colors(client: Client, productid: str) -> list[str]:
    """Distinct colors for a product: trimmed, case-insensitive deduped, sorted."""
    resp = (
        client.table("productsizecolors").select("color")
        .eq("productid", productid).execute()
    )
    seen: dict[str, str] = {}
    for r in (resp.data or []):
        raw = (r.get("color") or "").strip()
        if not raw:
            continue
        key = raw.lower()
        if key not in seen:
            seen[key] = raw
    return sorted(seen.values(), key=str.lower)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_variants_repo.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mockup_generator/db/variants_repo.py tests/test_variants_repo.py
git commit -m "feat(db): variants_repo.list_colors (normalized product colors)"
```

---

### Task 3: `GET /api/products/{productid}/colors`

**Files:**
- Modify: `backend/routers/products.py`
- Test: `tests/test_products_api.py` (append)

**Interfaces:**
- Consumes: `variants_repo.list_colors`.
- Produces: `GET /api/products/{productid}/colors` → `{"colors": [...]}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_products_api.py  (append)
from backend.routers import products as products_router


def test_list_product_colors(client, monkeypatch):
    monkeypatch.setattr(products_router.variants_repo, "list_colors",
                        lambda db, pid: ["Black", "Red"])
    r = client.get("/api/products/BC25001/colors")
    assert r.status_code == 200
    assert r.json() == {"colors": ["Black", "Red"]}
```

If `tests/test_products_api.py` has no shared `client` fixture, mirror the fixture from `tests/test_generate_api.py` (lines 28-35) at the top of this file.

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_products_api.py::test_list_product_colors -v`
Expected: FAIL with 404 (route missing) or AttributeError on `variants_repo`.

- [ ] **Step 3: Add the import and route**

In `backend/routers/products.py`, add to the repo import line:

```python
from mockup_generator.db import products_repo, variants_repo
```

Append the route:

```python
@router.get("/products/{productid}/colors")
def list_product_colors(
    productid: str,
    user: CurrentUser = Depends(get_current_user),
    db: Client = Depends(get_db),
):
    """Distinct variant colors for the product (for the generation selector)."""
    return {"colors": variants_repo.list_colors(db, productid)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_products_api.py::test_list_product_colors -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/routers/products.py tests/test_products_api.py
git commit -m "feat(api): GET /products/{id}/colors"
```

---

### Task 4: `mockup_variations_repo.insert` accepts `color`

**Files:**
- Modify: `mockup_generator/db/mockup_variations_repo.py`
- Test: `tests/test_storage_and_variations.py` (append)

**Interfaces:**
- Produces: `insert(client, *, productid, prompt_text, image_url, kind="image", prompt_id=None, created_by=None, color=None) -> dict` — `color` omitted from payload when `None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_storage_and_variations.py  (append; reuses _FakeDb)
def test_insert_includes_color_when_set():
    sink = {}
    mockup_variations_repo.insert(
        _FakeDb(sink), productid="BC1", prompt_text="p", image_url="u", color="Red"
    )
    assert sink["payload"]["color"] == "Red"


def test_insert_omits_color_when_none():
    sink = {}
    mockup_variations_repo.insert(
        _FakeDb(sink), productid="BC1", prompt_text="p", image_url="u"
    )
    assert "color" not in sink["payload"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_storage_and_variations.py::test_insert_includes_color_when_set -v`
Expected: FAIL with `TypeError: insert() got an unexpected keyword argument 'color'`.

- [ ] **Step 3: Add the `color` parameter**

In `mockup_generator/db/mockup_variations_repo.py`, add `color: str | None = None` to the signature (after `created_by`) and, alongside the other optional fields:

```python
    if color is not None:
        payload["color"] = color
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_storage_and_variations.py -v`
Expected: PASS (all, including the two new tests).

- [ ] **Step 5: Commit**

```bash
git add mockup_generator/db/mockup_variations_repo.py tests/test_storage_and_variations.py
git commit -m "feat(db): mockup_variations_repo.insert accepts color"
```

---

### Task 5: `mockups_repo.set_base_mockup`

**Files:**
- Modify: `mockup_generator/db/mockups_repo.py`
- Test: `tests/test_mockups_repo.py` (create)

**Interfaces:**
- Produces: `set_base_mockup(client, productid: str, value: bool = True) -> None` — updates the product's `mockups.base_mockup`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mockups_repo.py
from mockup_generator.db import mockups_repo


class _Upd:
    def __init__(self, sink):
        self.sink = sink

    def update(self, payload):
        self.sink["payload"] = payload
        return self

    def eq(self, col, val):
        self.sink["eq"] = (col, val)
        return self

    def execute(self):
        self.sink["executed"] = True
        return type("R", (), {"data": []})()


class _Db:
    def __init__(self, sink):
        self.sink = sink

    def table(self, name):
        self.sink["table"] = name
        return _Upd(self.sink)


def test_set_base_mockup_updates_flag():
    sink = {}
    mockups_repo.set_base_mockup(_Db(sink), "BC25001")
    assert sink["table"] == "mockups"
    assert sink["payload"] == {"base_mockup": True}
    assert sink["eq"] == ("productid", "BC25001")
    assert sink["executed"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_mockups_repo.py -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'set_base_mockup'`.

- [ ] **Step 3: Add the function**

Append to `mockup_generator/db/mockups_repo.py`:

```python
def set_base_mockup(client: Client, productid: str, value: bool = True) -> None:
    """Flip the product's ``base_mockup`` flag (row exists per product)."""
    client.table("mockups").update({"base_mockup": value}).eq("productid", productid).execute()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_mockups_repo.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mockup_generator/db/mockups_repo.py tests/test_mockups_repo.py
git commit -m "feat(db): mockups_repo.set_base_mockup"
```

---

### Task 6: `productimages_repo` — insert + list_for + delete_for (no-redundancy support)

**Files:**
- Create: `mockup_generator/db/productimages_repo.py`
- Test: `tests/test_productimages_repo.py`

**Interfaces:**
- Produces:
  - `insert(client, *, productid, imageurl, caption=None, displayorder=None) -> dict` — when `displayorder is None`, compute it as the current row count for the product.
  - `list_for(client, productid, caption: str | None) -> list[dict]` — existing rows matching `productid` AND `caption` (when `caption is None`, match SQL `NULL`). Returns dicts with at least `imageid` and `imageurl`. Used by approve to find the prior published object for orphan cleanup.
  - `delete_for(client, productid, caption: str | None) -> None` — delete rows matching `productid` AND `caption` (NULL-aware). Enforces "one row per (productid, color)".

**Why:** Approve must not pile up duplicate rows. The handler (Task 9) calls `list_for` → upload new → delete old Storage objects → `delete_for` → `insert`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_productimages_repo.py
from mockup_generator.db import productimages_repo


class _CountQ:
    def __init__(self, count):
        self._count = count

    def select(self, cols, count=None):
        return self

    def eq(self, col, val):
        return self

    def execute(self):
        return type("R", (), {"data": [], "count": self._count})()


class _InsertQ:
    def __init__(self, sink):
        self.sink = sink

    def insert(self, payload):
        self.sink["payload"] = payload
        return self

    def execute(self):
        return type("R", (), {"data": [{"imageid": 1, **self.sink["payload"]}]})()


class _InsertDb:
    """Two table() calls: first the count query, second the insert."""
    def __init__(self, sink, count):
        self.sink = sink
        self._count = count
        self._calls = 0

    def table(self, name):
        self.sink["table"] = name
        self._calls += 1
        return _CountQ(self._count) if self._calls == 1 else _InsertQ(self.sink)


def test_insert_computes_displayorder_from_count_and_sets_caption():
    sink = {}
    row = productimages_repo.insert(
        _InsertDb(sink, count=2), productid="BC1", imageurl="https://public/x.png", caption="Red"
    )
    assert sink["table"] == "productimages"
    p = sink["payload"]
    assert p == {"productid": "BC1", "imageurl": "https://public/x.png",
                 "displayorder": 2, "caption": "Red"}
    assert row["imageid"] == 1


def test_insert_omits_caption_when_none_and_honors_explicit_displayorder():
    sink = {}
    productimages_repo.insert(
        _InsertDb(sink, count=99), productid="BC1", imageurl="u", displayorder=5
    )
    assert sink["payload"] == {"productid": "BC1", "imageurl": "u", "displayorder": 5}


# ---- list_for / delete_for: capture the query chain (eq vs is_ for NULL) ----

class _ChainQ:
    """Records select/eq/is_/delete calls; returns rows on execute."""
    def __init__(self, sink, rows):
        self.sink = sink
        self._rows = rows

    def select(self, cols):
        self.sink.setdefault("ops", []).append(("select", cols))
        return self

    def delete(self):
        self.sink.setdefault("ops", []).append(("delete",))
        return self

    def eq(self, col, val):
        self.sink.setdefault("filters", []).append(("eq", col, val))
        return self

    def is_(self, col, val):
        self.sink.setdefault("filters", []).append(("is_", col, val))
        return self

    def execute(self):
        return type("R", (), {"data": self._rows})()


class _ChainDb:
    def __init__(self, sink, rows=None):
        self.sink = sink
        self._rows = rows or []

    def table(self, name):
        self.sink["table"] = name
        return _ChainQ(self.sink, self._rows)


def test_list_for_filters_by_productid_and_color():
    sink = {}
    rows = productimages_repo.list_for(
        _ChainDb(sink, rows=[{"imageid": 7, "imageurl": "https://public/old.png"}]),
        "BC1", "Red",
    )
    assert sink["table"] == "productimages"
    assert ("eq", "productid", "BC1") in sink["filters"]
    assert ("eq", "caption", "Red") in sink["filters"]
    assert rows[0]["imageurl"] == "https://public/old.png"


def test_list_for_uses_is_null_when_caption_none():
    sink = {}
    productimages_repo.list_for(_ChainDb(sink), "BC1", None)
    assert ("eq", "productid", "BC1") in sink["filters"]
    assert ("is_", "caption", "null") in sink["filters"]


def test_delete_for_filters_by_productid_and_color():
    sink = {}
    productimages_repo.delete_for(_ChainDb(sink), "BC1", "Red")
    assert ("delete",) in sink["ops"]
    assert ("eq", "productid", "BC1") in sink["filters"]
    assert ("eq", "caption", "Red") in sink["filters"]


def test_delete_for_uses_is_null_when_caption_none():
    sink = {}
    productimages_repo.delete_for(_ChainDb(sink), "BC1", None)
    assert ("delete",) in sink["ops"]
    assert ("is_", "caption", "null") in sink["filters"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_productimages_repo.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# mockup_generator/db/productimages_repo.py
"""Publish mockup images into the existing ``productimages`` table.

At most one row per ``(productid, caption)`` where caption holds the variant
color — re-publishing replaces it (see ``delete_for``), so no duplicates pile
up. ``list_for`` lets the caller find the prior row's URL for Storage cleanup.
"""

from __future__ import annotations

from supabase import Client


def _filter_color(query, caption: str | None):
    """Apply a productid is implied by the caller; add the NULL-aware color filter."""
    return query.eq("caption", caption) if caption is not None else query.is_("caption", "null")


def list_for(client: Client, productid: str, caption: str | None) -> list[dict]:
    """Existing rows for one product + color (NULL-aware on caption)."""
    q = client.table("productimages").select("imageid, imageurl").eq("productid", productid)
    resp = _filter_color(q, caption).execute()
    return list(resp.data or [])


def delete_for(client: Client, productid: str, caption: str | None) -> None:
    """Delete rows for one product + color (NULL-aware) — keeps one row per pair."""
    q = client.table("productimages").delete().eq("productid", productid)
    _filter_color(q, caption).execute()


def insert(
    client: Client,
    *,
    productid: str,
    imageurl: str,
    caption: str | None = None,
    displayorder: int | None = None,
) -> dict:
    """Insert one product image row. Appends after existing images by default."""
    if displayorder is None:
        cnt = (
            client.table("productimages").select("imageid", count="exact")
            .eq("productid", productid).execute()
        )
        displayorder = cnt.count or 0

    payload: dict = {"productid": productid, "imageurl": imageurl, "displayorder": displayorder}
    if caption is not None:
        payload["caption"] = caption

    resp = client.table("productimages").insert(payload).execute()
    return (resp.data or [{}])[0]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_productimages_repo.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mockup_generator/db/productimages_repo.py tests/test_productimages_repo.py
git commit -m "feat(db): productimages_repo insert/list_for/delete_for (one row per product+color)"
```

---

### Task 7: `storage_client` — public URL + `slugify` + `short_hex`

**Files:**
- Modify: `mockup_generator/integrations/storage_client.py`
- Test: `tests/test_storage_and_variations.py` (modify existing upload test + add helper tests)

**Interfaces:**
- Produces:
  - `upload_mockup(productid, data, key, *, bucket="mockups") -> tuple[str, str]` now returns `(object_path, public_url)` (public URL, not signed).
  - `slugify(text: str | None) -> str` — lowercase, non-alphanumeric runs → single `-`, trimmed of leading/trailing `-`.
  - `short_hex() -> str` — 8 hex chars from a uuid4.
  - `delete_object(object_path, *, bucket="mockups") -> None` — remove one object (best-effort orphan cleanup on re-approve).
  - `path_from_public_url(url, *, bucket="mockups") -> str | None` — extract the stored object path from a public URL (so the caller can delete a prior published object known only by its URL).

- [ ] **Step 1: Update the existing upload test + add helper tests**

In `tests/test_storage_and_variations.py`, the `_FakeBucket` already has `get_public_url`. Replace `test_upload_mockup_uploads_png_and_returns_path_and_signed_url` with:

```python
def test_upload_mockup_uploads_png_and_returns_path_and_public_url(monkeypatch):
    sink = {}
    monkeypatch.setattr(storage_client, "service_client", lambda: _FakeServiceClient(sink))

    path, url = storage_client.upload_mockup("BC25001", b"PNGDATA", "abc123")

    assert sink["bucket"] == "mockups"
    assert path == "BC25001/abc123.png"
    assert sink["upload"]["path"] == "BC25001/abc123.png"
    assert sink["upload"]["file"] == b"PNGDATA"
    assert sink["upload"]["opts"]["content-type"] == "image/png"
    assert url == "https://public/BC25001/abc123.png"
```

Add helper tests:

```python
def test_slugify_normalizes():
    assert storage_client.slugify("Parrot Green") == "parrot-green"
    assert storage_client.slugify("  Light  Grey ") == "light-grey"
    assert storage_client.slugify("Navy_blue") == "navy-blue"
    assert storage_client.slugify(None) == ""


def test_short_hex_is_8_hex_chars():
    h = storage_client.short_hex()
    assert len(h) == 8
    int(h, 16)  # raises if not hex


def test_path_from_public_url_extracts_object_path():
    url = "https://proj.supabase.co/storage/v1/object/public/mockups/BC1/parrot-green_deadbeef.png"
    assert storage_client.path_from_public_url(url) == "BC1/parrot-green_deadbeef.png"
    # strips query string if present
    assert storage_client.path_from_public_url(url + "?t=1") == "BC1/parrot-green_deadbeef.png"
    # unrecognized URL -> None
    assert storage_client.path_from_public_url("https://example.com/x.png") is None


def test_delete_object_removes_path(monkeypatch):
    sink = {}
    monkeypatch.setattr(storage_client, "service_client", lambda: _FakeServiceClient(sink))
    storage_client.delete_object("BC1/old.png")
    assert sink["bucket"] == "mockups"
    assert sink["removed"] == ["BC1/old.png"]
```

Add a `remove` method to `_FakeBucket` (alongside its existing methods):

```python
    def remove(self, paths):
        self.sink["removed"] = paths
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_storage_and_variations.py -v`
Expected: FAIL — old assertion expected a signed URL; `slugify`/`short_hex`/`path_from_public_url`/`delete_object` missing.

- [ ] **Step 3: Update `storage_client.py`**

Replace the signed-URL body of `upload_mockup` and add helpers. The module top adds `import re`, `import uuid`. New `upload_mockup`:

```python
def upload_mockup(
    productid: str,
    data: bytes,
    key: str,
    *,
    bucket: str = _BUCKET,
) -> tuple[str, str]:
    """Upload PNG ``data`` under ``{productid}/{key}.png`` to the public bucket.

    Returns ``(object_path, public_url)``: persist the stable path; hand the
    permanent public URL to the browser / store it in productimages.
    """
    client = service_client()
    if client is None:
        raise StorageNotConfigured("SUPABASE_SECRET_KEY is required to upload mockups")

    path = f"{productid}/{key}.png"
    store = client.storage.from_(bucket)
    store.upload(path, data, {"content-type": "image/png", "upsert": "true"})
    return path, store.get_public_url(path)


def slugify(text: str | None) -> str:
    """Filesystem/URL-safe slug: lowercase, non-alphanumeric runs -> single '-'."""
    if not text:
        return ""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def short_hex() -> str:
    """8 hex chars (uuid4) — uniqueness so re-approves don't overwrite."""
    return uuid.uuid4().hex[:8]


def path_from_public_url(url: str, *, bucket: str = _BUCKET) -> str | None:
    """Recover the stored object path from a Supabase public URL, else None."""
    marker = f"/object/public/{bucket}/"
    i = url.find(marker)
    if i == -1:
        return None
    return url[i + len(marker):].split("?")[0]


def delete_object(object_path: str, *, bucket: str = _BUCKET) -> None:
    """Remove one object from the bucket (orphan cleanup). Service-role only."""
    client = service_client()
    if client is None:
        raise StorageNotConfigured("SUPABASE_SECRET_KEY is required to delete objects")
    client.storage.from_(bucket).remove([object_path])
```

The `_SIGNED_TTL` constant is now unused — delete it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_storage_and_variations.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mockup_generator/integrations/storage_client.py tests/test_storage_and_variations.py
git commit -m "feat(storage): public-URL uploads + slugify/short_hex/delete_object/path_from_public_url"
```

---

### Task 8: `/api/generate/image` → preview-only + require source images

**Files:**
- Modify: `backend/schemas.py`
- Modify: `backend/routers/generate.py`
- Test: `tests/test_generate_api.py` (modify)

**Interfaces:**
- Consumes: `service.generate_mockup_bytes`, `drive_client.download_file`, `products_repo.get_product`.
- Produces:
  - `GenerateRequest` gains `color: str | None = None`.
  - `GeneratePreview(BaseModel)`: `status: str`, `detail: str`, `image_b64: str`.
  - `POST /api/generate/image` returns `GeneratePreview`; **no** Storage/DB writes; **400** when `image_ids` empty.

- [ ] **Step 1: Update the generate tests**

In `tests/test_generate_api.py`:

1. Add `color: str | None = None` is not needed in `Product`; leave `_product` as is.
2. Replace `_wire_happy` so it no longer mocks storage/variations (generate writes nothing):

```python
def _wire_happy(monkeypatch, *, calls):
    monkeypatch.setattr(gen.products_repo, "get_product", lambda db, pid: _product())
    monkeypatch.setattr(gen.drive_client, "download_file",
                        lambda fid: (calls.setdefault("downloaded", []).append(fid), _png_bytes())[1])

    def fake_generate(images, prompt, **kw):
        calls["gen"] = {"n_images": len(images), "prompt": prompt, "kw": kw}
        return _png_bytes()

    monkeypatch.setattr(gen.service, "generate_mockup_bytes", fake_generate)
```

3. Replace `test_generate_image_success_with_explicit_ids` body assertions:

```python
def test_generate_image_success_with_explicit_ids(client, monkeypatch):
    calls = {}
    _wire_happy(monkeypatch, calls=calls)

    r = client.post("/api/generate/image",
                    json={"productid": "BC25001", "prompt": "a luxe saree",
                          "image_ids": ["f1", "f2"]})

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert isinstance(body["image_b64"], str) and len(body["image_b64"]) > 0
    assert "image_url" not in body
    assert calls["downloaded"] == ["f1", "f2"]
    assert calls["gen"]["n_images"] == 2
```

4. Delete `test_generate_image_falls_back_to_folder_listing` and `test_generate_image_storage_not_configured_503` (no fallback, no storage in `/image`).

5. Replace `test_generate_image_no_images_400` with a direct check:

```python
def test_generate_image_requires_source_images_400(client, monkeypatch):
    monkeypatch.setattr(gen.products_repo, "get_product", lambda db, pid: _product())
    r = client.post("/api/generate/image",
                    json={"productid": "BC25001", "prompt": "p", "image_ids": []})
    assert r.status_code == 400
```

6. Keep `test_generate_image_caps_references_at_14`, `..._product_not_found_404`, `..._no_folder_400`, `..._gemini_failure_502`, `..._threads_model_resolution_aspect`, and the bad-model/resolution/aspect tests unchanged (they pass explicit `image_ids` or fail before generation).

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_generate_api.py -v`
Expected: FAIL — response still has `image_url`; empty-ids path differs.

- [ ] **Step 3: Update schemas**

In `backend/schemas.py`, add `color` to `GenerateRequest` and add `GeneratePreview`:

```python
class GenerateRequest(BaseModel):
    productid: str
    prompt: str
    image_ids: list[str] = []
    model: str | None = None
    resolution: str | None = None
    aspect_ratio: str | None = None
    color: str | None = None


class GeneratePreview(BaseModel):
    status: str
    detail: str
    image_b64: str
```

(Leave `GenerateResponse` in place; Task 9 reuses a new `ApproveResponse`.)

- [ ] **Step 4: Rewrite the `/image` handler**

In `backend/routers/generate.py`: add `import base64` at the top; import `GeneratePreview` from `backend.schemas`; delete the `_resolve_ref_ids` helper. Replace the `generate_image` function:

```python
@router.post("/image", response_model=GeneratePreview)
def generate_image(req: GenerateRequest, user: CurrentUser = Depends(get_current_user),
                   db: Client = Depends(get_db)):
    if req.model is not None and req.model not in ALLOWED_MODELS:
        raise HTTPException(status_code=400, detail=f"Unsupported model: {req.model}")
    if req.resolution is not None and req.resolution not in ALLOWED_RESOLUTIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported resolution: {req.resolution}")
    if req.aspect_ratio is not None and req.aspect_ratio not in ALLOWED_ASPECTS:
        raise HTTPException(status_code=400, detail=f"Unsupported aspect ratio: {req.aspect_ratio}")

    if not req.image_ids:
        raise HTTPException(status_code=400, detail="Select at least one source image.")

    product = products_repo.get_product(db, req.productid)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    folder_id = drive_client.extract_folder_id(product.producturl)
    if not folder_id:
        raise HTTPException(status_code=400, detail="Product has no linked Drive folder")

    ref_ids = req.image_ids[:_MAX_REFS]
    try:
        images = [Image.open(BytesIO(drive_client.download_file(fid))) for fid in ref_ids]
    except DriveNotConfigured as exc:
        raise HTTPException(status_code=503, detail="Drive access is not configured on the server") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not download Drive images: {exc}") from exc

    try:
        png = service.generate_mockup_bytes(
            images, req.prompt,
            model=req.model, resolution=req.resolution, aspect_ratio=req.aspect_ratio,
        )
    except service.NoImageReturned as exc:
        raise HTTPException(status_code=502, detail="The model returned no image. Try again.") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Image generation failed: {exc}") from exc

    return GeneratePreview(
        status="ok", detail="Preview generated.",
        image_b64=base64.b64encode(png).decode("ascii"),
    )
```

Imports `uuid`, `JSONResponse`, `storage_client`, `mockup_variations_repo`, `GenerateResponse` may now be unused by `/image` but are still used by `/approve` (Task 9) and `/video`. Leave them; Task 9 adds the approve handler in the same file.

- [ ] **Step 5: Run tests to verify they pass**

Run: `poetry run pytest tests/test_generate_api.py -v`
Expected: PASS (the kept + rewritten tests).

- [ ] **Step 6: Commit**

```bash
git add backend/schemas.py backend/routers/generate.py tests/test_generate_api.py
git commit -m "feat(generate): /image is preview-only and requires >=1 source image"
```

---

### Task 9: `POST /api/generate/approve` (publish)

**Files:**
- Modify: `backend/schemas.py`
- Modify: `backend/routers/generate.py`
- Test: `tests/test_approve_publish.py` (create)

**Interfaces:**
- Consumes: `storage_client.upload_mockup`, `storage_client.slugify`, `storage_client.short_hex`, `storage_client.path_from_public_url`, `storage_client.delete_object`, `mockup_variations_repo.insert`, `mockups_repo.set_base_mockup`, `productimages_repo.list_for`, `productimages_repo.delete_for`, `productimages_repo.insert`.
- Produces:
  - `ApproveResponse(BaseModel)`: `status: str`, `detail: str`, `image_url: str`, `variation_id: int | None`.
  - `POST /api/generate/approve` (multipart: `productid`, `color?`, `prompt_text?`, `source` default `"generated"`, file `image`) → publishes, returns `ApproveResponse`.

**No-redundancy behavior (Global Constraints):** after the new upload succeeds, the handler finds any prior `productimages` row for `(productid, color)`, deletes its Storage object (best-effort — a cleanup failure must NOT fail the publish), deletes the old row, then inserts the new one. `mockup_variations` is appended every time (audit log).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_approve_publish.py
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.main import app
from backend.auth import get_current_user, CurrentUser
from backend.deps import get_db
from backend.routers import generate as gen
from mockup_generator.db.profiles_repo import Profile
from mockup_generator.integrations.storage_client import StorageNotConfigured


def _png_bytes() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (4, 4), (9, 9, 9)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def client():
    u = CurrentUser(id="u1", email="a@b.c", role="user",
                    profile=Profile(id="u1", email="a@b.c", role="user", is_active=True))
    app.dependency_overrides[get_current_user] = lambda: u
    app.dependency_overrides[get_db] = lambda: object()
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _wire(monkeypatch, calls):
    monkeypatch.setattr(gen.storage_client, "short_hex", lambda: "deadbeef")
    monkeypatch.setattr(gen.storage_client, "upload_mockup",
                        lambda pid, data, key, **kw: (calls.__setitem__("key", key)
                                                      or (f"{pid}/{key}.png", f"https://public/{pid}/{key}.png")))
    monkeypatch.setattr(gen.mockup_variations_repo, "insert",
                        lambda db, **kw: (calls.__setitem__("variation", kw) or {"variation_id": 42}))
    monkeypatch.setattr(gen.mockups_repo, "set_base_mockup",
                        lambda db, pid, value=True: calls.__setitem__("flag", (pid, value)))
    monkeypatch.setattr(gen.productimages_repo, "insert",
                        lambda db, **kw: (calls.__setitem__("image", kw) or {"imageid": 1}))
    # no-redundancy seam: prior rows (default none), delete row, delete object
    monkeypatch.setattr(gen.productimages_repo, "list_for",
                        lambda db, pid, cap: calls.get("existing", []))
    monkeypatch.setattr(gen.productimages_repo, "delete_for",
                        lambda db, pid, cap: calls.__setitem__("deleted_for", (pid, cap)))
    monkeypatch.setattr(gen.storage_client, "delete_object",
                        lambda path, **kw: calls.setdefault("removed", []).append(path))
    # path_from_public_url is the real pure function (not mocked)


def test_approve_generated_publishes(client, monkeypatch):
    calls = {}
    _wire(monkeypatch, calls)

    r = client.post("/api/generate/approve",
                    data={"productid": "BC25001", "color": "Parrot Green",
                          "prompt_text": "a saree", "source": "generated"},
                    files={"image": ("m.png", _png_bytes(), "image/png")})

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["image_url"] == "https://public/BC25001/parrot-green_deadbeef.png"
    assert body["variation_id"] == 42
    assert calls["key"] == "parrot-green_deadbeef"
    assert calls["variation"]["color"] == "Parrot Green"
    assert calls["variation"]["prompt_text"] == "a saree"
    assert calls["variation"]["image_url"] == "https://public/BC25001/parrot-green_deadbeef.png"
    assert calls["flag"] == ("BC25001", True)
    assert calls["image"]["imageurl"] == "https://public/BC25001/parrot-green_deadbeef.png"
    assert calls["image"]["caption"] == "Parrot Green"
    assert calls["deleted_for"] == ("BC25001", "Parrot Green")
    assert "removed" not in calls  # no prior row -> nothing deleted from storage


def test_approve_replaces_existing_row_and_deletes_old_object(client, monkeypatch):
    calls = {}
    _wire(monkeypatch, calls)
    calls["existing"] = [{
        "imageid": 5,
        "imageurl": "https://proj.supabase.co/storage/v1/object/public/mockups/BC25001/old_cafef00d.png",
    }]
    r = client.post("/api/generate/approve",
                    data={"productid": "BC25001", "color": "Red", "source": "generated"},
                    files={"image": ("m.png", _png_bytes(), "image/png")})
    assert r.status_code == 200
    assert calls["removed"] == ["BC25001/old_cafef00d.png"]   # old object cleaned up
    assert calls["deleted_for"] == ("BC25001", "Red")          # old row replaced


def test_approve_corrected_defaults_prompt_text(client, monkeypatch):
    calls = {}
    _wire(monkeypatch, calls)
    r = client.post("/api/generate/approve",
                    data={"productid": "BC1", "source": "corrected"},
                    files={"image": ("m.png", _png_bytes(), "image/png")})
    assert r.status_code == 200
    assert calls["variation"]["prompt_text"] == "(manual upload)"
    assert "color" not in calls["variation"]  # None omitted by repo
    assert calls["key"] == "deadbeef"  # no color -> hex only


def test_approve_rejects_non_image_400(client, monkeypatch):
    _wire(monkeypatch, calls={})
    r = client.post("/api/generate/approve",
                    data={"productid": "BC1", "source": "generated"},
                    files={"image": ("x.txt", b"not an image", "text/plain")})
    assert r.status_code == 400


def test_approve_storage_not_configured_503(client, monkeypatch):
    calls = {}
    _wire(monkeypatch, calls)
    monkeypatch.setattr(gen.storage_client, "upload_mockup",
                        lambda *a, **k: (_ for _ in ()).throw(StorageNotConfigured("no key")))
    r = client.post("/api/generate/approve",
                    data={"productid": "BC1", "source": "generated"},
                    files={"image": ("m.png", _png_bytes(), "image/png")})
    assert r.status_code == 503
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_approve_publish.py -v`
Expected: FAIL with 404/405 (route missing) or AttributeError on `gen.mockups_repo`/`gen.productimages_repo`.

- [ ] **Step 3: Add schema + imports + handler**

In `backend/schemas.py`, add:

```python
class ApproveResponse(BaseModel):
    status: str
    detail: str
    image_url: str
    variation_id: int | None = None
```

In `backend/routers/generate.py`, extend imports:

```python
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from backend.schemas import ApproveResponse, GeneratePreview, GenerateRequest
from mockup_generator.db import mockup_variations_repo, mockups_repo, productimages_repo, products_repo
```

Add a size constant near `_MAX_REFS`:

```python
_MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB
```

Add the handler (above the `/video` stub):

```python
@router.post("/approve", response_model=ApproveResponse)
async def approve_mockup(
    productid: str = Form(...),
    color: str | None = Form(None),
    prompt_text: str | None = Form(None),
    source: str = Form("generated"),
    image: UploadFile = File(...),
    user: CurrentUser = Depends(get_current_user),
    db: Client = Depends(get_db),
):
    raw = await image.read()
    if len(raw) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Image is too large.")
    try:
        Image.open(BytesIO(raw)).verify()              # cheap validity check
        png_img = Image.open(BytesIO(raw)).convert("RGB")  # reopen (verify exhausts it)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Uploaded file is not a valid image.") from exc

    buf = BytesIO()
    png_img.save(buf, format="PNG")
    png = buf.getvalue()

    slug = storage_client.slugify(color)
    key = f"{slug}_{storage_client.short_hex()}" if slug else storage_client.short_hex()
    try:
        _path, public_url = storage_client.upload_mockup(productid, png, key)
    except storage_client.StorageNotConfigured as exc:
        raise HTTPException(status_code=503, detail="Storage is not configured on the server") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not store the mockup: {exc}") from exc

    # No redundancy: one productimages row per (productid, color). Replace any
    # prior row and clean up its orphaned Storage object (best-effort — a
    # cleanup failure must not fail an otherwise-successful publish).
    for prior in productimages_repo.list_for(db, productid, color):
        old_path = storage_client.path_from_public_url(prior.get("imageurl") or "")
        if old_path:
            try:
                storage_client.delete_object(old_path)
            except Exception:  # noqa: BLE001 - orphan cleanup is non-fatal
                pass
    productimages_repo.delete_for(db, productid, color)

    text = prompt_text or ("(manual upload)" if source == "corrected" else "")
    row = mockup_variations_repo.insert(
        db, productid=productid, prompt_text=text, image_url=public_url,
        color=color, created_by=user.id,
    )
    mockups_repo.set_base_mockup(db, productid, True)
    productimages_repo.insert(db, productid=productid, imageurl=public_url, caption=color)

    return ApproveResponse(
        status="ok", detail="Published.",
        image_url=public_url, variation_id=row.get("variation_id"),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_approve_publish.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full backend suite**

Run: `poetry run pytest -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add backend/schemas.py backend/routers/generate.py tests/test_approve_publish.py
git commit -m "feat(generate): /approve publishes mockup (storage + flag + productimages)"
```

---

### Task 10: Frontend API client — colors, preview, approve

**Files:**
- Modify: `frontend/src/api.ts`

**Interfaces:**
- Produces:
  - `apiUpload<T>(path, form: FormData): Promise<T>` — token-authed multipart (no JSON `Content-Type`).
  - `getProductColors(id): Promise<{ colors: string[] }>`.
  - `GenPreview { status: string; detail: string; image_b64: string }`; `generateImage(...)` returns `GenPreview` and accepts `color?`.
  - `ApproveResult { status: string; detail: string; image_url: string; variation_id?: number }`; `approveMockup(form: FormData): Promise<ApproveResult>`.

- [ ] **Step 1: Add the multipart helper**

After `apiFetch` in `frontend/src/api.ts`, add:

```ts
/** Like apiFetch but for multipart/form-data — lets the browser set the boundary. */
export async function apiUpload<T>(path: string, form: FormData): Promise<T> {
  const {
    data: { session },
  } = await supabase.auth.getSession();
  const token = session?.access_token;

  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      method: "POST",
      headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: form,
    });
  } catch {
    throw new ApiError(0, STATUS_HINTS[0]);
  }
  if (!res.ok) {
    let detail = "";
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
      else if (body?.detail) detail = JSON.stringify(body.detail);
    } catch {
      /* non-JSON body */
    }
    throw new ApiError(res.status, detail || STATUS_HINTS[res.status] || res.statusText);
  }
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}
```

- [ ] **Step 2: Add colors + update generate types + approve**

Add the colors call (near `listProductImages`):

```ts
export const getProductColors = (id: string) =>
  apiFetch<{ colors: string[] }>(`/api/products/${encodeURIComponent(id)}/colors`);
```

Replace the `GenResult` interface usage for generate. Add:

```ts
export interface GenPreview {
  status: string;
  detail: string;
  image_b64: string;
}

export interface ApproveResult {
  status: string;
  detail: string;
  image_url: string;
  variation_id?: number;
}
```

Change `generateImage` to send `color` and return `GenPreview`:

```ts
export const generateImage = (b: {
  productid: string;
  prompt: string;
  image_ids?: string[];
  model?: string;
  resolution?: string;
  aspect_ratio?: string;
  color?: string;
}) =>
  apiFetch<GenPreview>("/api/generate/image", {
    method: "POST",
    body: JSON.stringify(b),
  });

export const approveMockup = (form: FormData) =>
  apiUpload<ApproveResult>("/api/generate/approve", form);
```

(`generateVideo` keeps returning `GenResult`; leave it.)

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npx tsc -b`
Expected: errors only in `ProductsTab.tsx` (it still consumes the old generate shape) — those are fixed in Tasks 11-12. If errors appear in other files, fix the type usage here.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api.ts
git commit -m "feat(web): api client for colors, preview, approve/upload"
```

---

### Task 11: Frontend — color selector, require selection, preview state

**Files:**
- Modify: `frontend/src/components/ProductsTab.tsx`

**Interfaces:**
- Consumes: `getProductColors`, `generateImage` (returns `GenPreview`).
- Produces: `GenerationStage` holds `color`, `colors`, and `previewB64` state; Generate is gated on `pickedCount > 0`; the result section renders the preview from base64.

- [ ] **Step 1: Update imports + state**

In `frontend/src/components/ProductsTab.tsx`, extend the api import to include `getProductColors`, `approveMockup`, and types `GenPreview`, `ApproveResult`. Replace the `resultUrl` state in `GenerationStage` with preview + publish state:

```tsx
  const [previewB64, setPreviewB64] = useState<string | null>(null);
  const [publishedUrl, setPublishedUrl] = useState<string | null>(null);
  const [colors, setColors] = useState<string[]>([]);
  const [color, setColor] = useState("");
```

Reset them in the product-change effect (the one that calls `listProductImages`): set `setPreviewB64(null); setPublishedUrl(null);` where `setResultUrl(null)` was.

- [ ] **Step 2: Fetch colors on product load**

Add an effect after the prompts effect:

```tsx
  useEffect(() => {
    setColor("");
    getProductColors(product.productid)
      .then((r) => setColors(r.colors))
      .catch(() => setColors([]));  // optional; generation works without color
  }, [product.productid]);
```

- [ ] **Step 3: Generate → preview only; gate on selection**

Replace the image branch of `run` so it stores the preview, and remove `generateVideo`'s `image` result handling for images:

```tsx
  const run = (kind: "image" | "video") => {
    setBusy(kind);
    setMsg(null);
    if (kind === "image") { setPreviewB64(null); setPublishedUrl(null); }
    const image_ids = [...picked];
    if (kind === "image") {
      generateImage({
        productid: product.productid, prompt: promptText, image_ids,
        color: color || undefined,
        model: model || undefined, resolution: resolution || undefined,
        aspect_ratio: aspect || undefined,
      })
        .then((r) => { setMsg({ kind: "info", text: r.detail }); setPreviewB64(r.image_b64); })
        .catch((e: Error) => setMsg({ kind: "error", text: e.message.replace(/^\d+:\s*/, "") }))
        .finally(() => setBusy(null));
    } else {
      generateVideo({ productid: product.productid, prompt: videoPrompt, image_ids })
        .then((r) => setMsg({ kind: "info", text: r.detail }))
        .catch((e: Error) => setMsg({ kind: "error", text: e.message.replace(/^\d+:\s*/, "") }))
        .finally(() => setBusy(null));
    }
  };
```

Gate the Generate button — change its `disabled`:

```tsx
          disabled={busy !== null || !promptText.trim() || pickedCount === 0}
```

And add a hint under the button when nothing is picked:

```tsx
        {pickedCount === 0 && (
          <p className="mt-2 text-xs text-subtle">Select at least one source image to generate.</p>
        )}
```

- [ ] **Step 4: Add the color dropdown**

Inside the options block (where model/quality/aspect selects are), add a color field (render only when colors exist):

```tsx
        {colors.length > 0 && (
          <label className="field mb-0! mt-4">
            <span className="text-xs font-semibold text-subtle">Variant color</span>
            <select aria-label="Variant color" value={color} onChange={(e) => setColor(e.target.value)}>
              <option value="">— no color —</option>
              {colors.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </label>
        )}
```

- [ ] **Step 5: Render the preview from base64**

Replace the old "Generated mockup" `resultUrl` section with a preview that uses the data URI (actions come in Task 12 — for now just render image + a placeholder div for actions):

```tsx
      {previewB64 && (
        <section className="mt-5">
          <p className="section-label mt-0!">Preview</p>
          <img
            src={`data:image/png;base64,${previewB64}`}
            alt="Generated preview"
            className="mt-2 w-full rounded-lg border border-line"
          />
        </section>
      )}
```

- [ ] **Step 6: Typecheck**

Run: `cd frontend && npx tsc -b`
Expected: PASS (no type errors).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/ProductsTab.tsx
git commit -m "feat(web): color selector, required source selection, preview-only generate"
```

---

### Task 12: Frontend — Approve / Disapprove / Download / Upload-corrected

**Files:**
- Modify: `frontend/src/components/ProductsTab.tsx`

**Interfaces:**
- Consumes: `approveMockup(form: FormData)`.
- Produces: preview action bar; Approve (generated or corrected) publishes and shows the public URL; Disapprove clears; Download saves the preview locally.

- [ ] **Step 1: Add a publish helper + action handlers in `GenerationStage`**

```tsx
  const [publishing, setPublishing] = useState(false);

  const publish = (blob: Blob, src: "generated" | "corrected") => {
    setPublishing(true);
    setMsg(null);
    const fd = new FormData();
    fd.append("productid", product.productid);
    if (color) fd.append("color", color);
    if (promptText) fd.append("prompt_text", promptText);
    fd.append("source", src);
    fd.append("image", blob, "mockup.png");
    approveMockup(fd)
      .then((r) => { setPublishedUrl(r.image_url); setMsg({ kind: "info", text: r.detail }); })
      .catch((e: Error) => setMsg({ kind: "error", text: e.message.replace(/^\d+:\s*/, "") }))
      .finally(() => setPublishing(false));
  };

  const approveGenerated = async () => {
    if (!previewB64) return;
    const blob = await (await fetch(`data:image/png;base64,${previewB64}`)).blob();
    publish(blob, "generated");
  };

  const downloadPreview = () => {
    if (!previewB64) return;
    const a = document.createElement("a");
    a.href = `data:image/png;base64,${previewB64}`;
    a.download = `${product.productid}_${color ? color.replace(/\s+/g, "-") : "mockup"}.png`;
    a.click();
  };

  const onCorrectedFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) publish(f, "corrected");
    e.target.value = "";
  };
```

- [ ] **Step 2: Replace the preview section with the action bar**

```tsx
      {previewB64 && (
        <section className="mt-5">
          <p className="section-label mt-0!">Preview — review before publishing</p>
          <img
            src={`data:image/png;base64,${previewB64}`}
            alt="Generated preview"
            className="mt-2 w-full rounded-lg border border-line"
          />
          <div className="mt-3 flex flex-wrap gap-2">
            <button className="btn-primary" onClick={approveGenerated} disabled={publishing}>
              {publishing && <span className="spinner" aria-hidden />}
              {publishing ? "Publishing…" : "Approve & publish"}
            </button>
            <button onClick={() => { setPreviewB64(null); setMsg(null); }} disabled={publishing}>
              Disapprove
            </button>
            <button onClick={downloadPreview} disabled={publishing}>Download</button>
            <label className="btn cursor-pointer">
              Upload corrected
              <input type="file" accept="image/*" className="hidden" onChange={onCorrectedFile} />
            </label>
          </div>
          {publishedUrl && (
            <p className="alert alert-info mt-3" role="status">
              Published: <a href={publishedUrl} target="_blank" rel="noreferrer">{publishedUrl}</a>
            </p>
          )}
        </section>
      )}
```

(If the project's button classes differ, match an existing secondary button's classes used elsewhere in this file.)

- [ ] **Step 3: Typecheck + build**

Run: `cd frontend && npm run build`
Expected: build succeeds (tsc clean, vite bundles).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/ProductsTab.tsx
git commit -m "feat(web): approve/disapprove/download/upload-corrected on preview"
```

---

### Task 13: Full verification

**Files:** none (manual + suite).

- [ ] **Step 1: Backend suite**

Run: `poetry run pytest -q`
Expected: all green.

- [ ] **Step 2: Frontend build**

Run: `cd frontend && npm run build`
Expected: success.

- [ ] **Step 3: Manual smoke (against deployed/local backend with real Supabase)**

- Pick a pending product → colors load → pick a color → pick ≥1 source image → **Generate** → preview appears; confirm nothing was written (no new `mockup_variations`/`productimages` row, `base_mockup` unchanged).
- Generate button disabled with 0 source images selected.
- **Disapprove** → preview clears, still no writes.
- **Approve & publish** → response returns a public URL that renders in a fresh browser tab (anon, no auth); verify `mockup_variations` row has `color`, `mockups.base_mockup = true`, and a `productimages` row with `imageurl` (public URL) + `caption = color`.
- **Upload corrected** a different PNG → publishes the uploaded image the same way.
- **Download** saves the previewed PNG locally.

- [ ] **Step 4: Update the phased plan status**

In `docs/plans/2026-06-21-implementation-plan.md`, update the Phase 3 section to mark generate-preview + approve/publish + variant color done, and note this plan as the companion. Commit:

```bash
git add docs/plans/2026-06-21-implementation-plan.md
git commit -m "docs(plan): Phase 3 generate-preview + approve/publish complete"
```

---

## Self-Review

- **Spec coverage:** require ≥1 source (Task 8), colors surfaced (Tasks 2-3, 11), generate preview-only (Task 8, 11), approve publishes to public bucket + flip flag + productimages + audit row (Tasks 1, 4-7, 9), disapprove discards (Task 12), corrected upload (Tasks 9, 12), meaningful name (Task 7, 9), download (Task 12), public/view-only bucket (Task 1), **no DB redundancy — one productimages row per (productid, color) with old-object cleanup (Tasks 6, 7, 9)**. All covered.
- **Placeholders:** none — every code step shows full code; the only soft note is "match existing button classes," which is a styling parity instruction, not missing logic.
- **Type consistency:** `GeneratePreview.image_b64` / `GenPreview.image_b64`, `ApproveResponse`/`ApproveResult` (`image_url`, `variation_id`), `upload_mockup -> (path, public_url)`, `slugify`/`short_hex`, `set_base_mockup(client, productid, value=True)`, `productimages_repo.insert(productid, imageurl, caption, displayorder)`, `mockup_variations_repo.insert(..., color=None)` — consistent across backend tasks, tests, and frontend.
