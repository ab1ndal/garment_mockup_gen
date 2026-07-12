# Product Shot Cutout Cache + Product ID Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compute the BiRefNet cutout once per Drive image and cache it, so every subsequent adjustment re-renders cheaply (no Drive re-download, no re-segmentation); show the Product ID in the editor and in published filenames.

**Architecture:** Split the pure image pipeline into `compute_cutout` (expensive, cached) and `render` (cheap, per-adjustment), reordering so segmentation precedes geometry/colour. The import router holds an in-process LRU cache keyed by Drive `file_id`; `/preview`, `/publish`, and a new `/warm` endpoint all render from the cached cutout. Frontend pre-warms on editor open and displays the Product ID.

**Tech Stack:** Python 3.10, FastAPI, Pillow, rembg/BiRefNet, pytest + FastAPI TestClient; React + Vite + TypeScript frontend.

## Global Constraints

- Python floor: `>=3.10,<3.11`.
- No new dependencies — use stdlib (`collections.OrderedDict`, `threading`) + existing Pillow.
- Preview must stay pixel-identical to publish: both go through the single `render` function.
- `edit_pipeline.py` stays pure (no I/O, no network); the cache and all Drive/Storage I/O live in the router.
- Frontend design changes go through the `ui-ux-pro-max` skill (project convention).
- Filename change is new-shots-only; do not rename or backfill existing objects.

---

### Task 1: Split the edit pipeline into `compute_cutout` + `render`

**Files:**
- Modify: `mockup_generator/generation/edit_pipeline.py`
- Test: `tests/test_edit_pipeline_bg.py` (existing tests must stay green; add new ones)
- Test: `tests/test_edit_pipeline_geometry.py` (migrate — it tests the deleted `apply_geometry_and_colour`)

**Interfaces:**
- Produces:
  - `compute_cutout(src_bytes: bytes) -> PIL.Image.Image` (RGBA cutout; raises `BackgroundRemovalUnavailable`)
  - `render(cutout: PIL.Image.Image, params: EditParams) -> bytes` (RGB PNG bytes)
  - `apply_edits(src_bytes: bytes, params: EditParams) -> bytes` (unchanged signature; now `render(compute_cutout(src_bytes), params)`)

- [ ] **Step 1: Add failing tests for the new split**

Append to `tests/test_edit_pipeline_bg.py`:

```python
def test_compute_cutout_returns_rgba(monkeypatch):
    monkeypatch.setattr(ep, "_remove_background", _fake_cutout)
    cut = ep.compute_cutout(_png_bytes())
    assert cut.mode == "RGBA"
    assert cut.getpixel((40, 40))[3] == 255      # opaque centre
    assert cut.getpixel((1, 1))[3] == 0          # transparent border


def test_render_composites_from_cutout():
    # render takes an already-computed cutout; no rembg involved
    cut = _fake_cutout(Image.new("RGB", (80, 80), (120, 60, 30)))
    out = Image.open(BytesIO(ep.render(cut, EditParams(bg="cream"))))
    assert out.mode == "RGB"
    assert out.getpixel((1, 1)) == (250, 247, 240)   # transparent border -> cream


def test_render_rotate_quarter_swaps_dims():
    cut = _fake_cutout(Image.new("RGB", (100, 40), (120, 60, 30)))
    out = Image.open(BytesIO(ep.render(cut, EditParams(rotate_quarter=1))))
    assert out.size == (40, 100)                     # 90deg swaps w/h
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `poetry run pytest tests/test_edit_pipeline_bg.py -v`
Expected: the three new tests FAIL with `AttributeError: module ... has no attribute 'compute_cutout'` / `'render'`; the three existing `test_apply_edits_*` tests still PASS.

- [ ] **Step 3: Implement the split (reorder: cutout before geometry/colour)**

In `mockup_generator/generation/edit_pipeline.py`, replace `apply_geometry_and_colour` and `apply_edits` (lines 56–77 and 129–146) with:

```python
def compute_cutout(src_bytes: bytes) -> Image.Image:
    """EXIF-normalise the source and return the RGBA BiRefNet cutout.

    The single expensive step (rembg); its result is what callers cache.
    Raises BackgroundRemovalUnavailable if rembg/the model cannot run.
    """
    src = Image.open(BytesIO(src_bytes))
    normalised = ImageOps.exif_transpose(src).convert("RGB")
    return _remove_background(normalised)


def render(cutout: Image.Image, params: EditParams) -> bytes:
    """Apply cheap, params-driven ops to a precomputed RGBA cutout.

    Colour/tonal ops run on the RGB channels with alpha preserved; white
    balance uses the cutout alpha as its mask so it balances on garment
    pixels only. Then quarter-rotate/straighten, composite, optional shadow.
    Returns RGB PNG bytes. No rembg, no I/O — safe to run per adjustment.
    """
    rgba = cutout.convert("RGBA")
    alpha = rgba.getchannel("A")
    rgb = rgba.convert("RGB")

    if params.white_balance:
        rgb = _gray_world(rgb, mask=alpha)
    if params.autocontrast:
        rgb = ImageOps.autocontrast(rgb, cutoff=1, preserve_tone=True)
    if params.brightness != 1.0:
        rgb = ImageEnhance.Brightness(rgb).enhance(params.brightness)
    if params.saturation != 1.0:
        rgb = ImageEnhance.Color(rgb).enhance(params.saturation)

    rgba = rgb.convert("RGBA")
    rgba.putalpha(alpha)

    q = params.rotate_quarter % 4
    if q:
        rgba = rgba.transpose(_QUARTER_CW[q])
    if params.straighten_deg:
        rgba = rgba.rotate(params.straighten_deg, resample=Image.Resampling.BICUBIC,
                           expand=True, fillcolor=(0, 0, 0, 0))

    bg_rgb = _BG_COLOURS.get(params.bg, WHITE)
    if params.shadow:
        composited = _add_drop_shadow(rgba, bg_rgb)
    else:
        base = Image.new("RGBA", rgba.size, bg_rgb + (255,))
        composited = Image.alpha_composite(base, rgba).convert("RGB")
    buf = BytesIO()
    composited.save(buf, format="PNG")
    return buf.getvalue()


def apply_edits(src_bytes: bytes, params: EditParams) -> bytes:
    """Full pipeline convenience wrapper: cutout then render."""
    return render(compute_cutout(src_bytes), params)
```

Leave `_gray_world`, `_remove_background`, `_get_session`, `_add_drop_shadow`, `EditParams`, `_QUARTER_CW`, `_BG_COLOURS`, and `BackgroundRemovalUnavailable` unchanged. Delete the now-unused `apply_geometry_and_colour`.

- [ ] **Step 4: Migrate the geometry test file to `render`**

`tests/test_edit_pipeline_geometry.py` imports and tests `apply_geometry_and_colour`, which Step 3 deletes. That behavior now lives in `render` (operating on a cutout). Replace the whole file with the same assertions expressed through `render`, using a fully-opaque cutout so colour/geometry effects survive the composite:

```python
from io import BytesIO
from PIL import Image
from mockup_generator.generation.edit_pipeline import EditParams, render


def _cutout(w=100, h=60, colour=(120, 60, 30)):
    # fully-opaque RGBA cutout so colour/geometry ops show after composite
    return Image.new("RGBA", (w, h), colour + (255,))


def _out(cut, params):
    return Image.open(BytesIO(render(cut, params)))


def test_quarter_rotate_swaps_dimensions():
    assert _out(_cutout(100, 60), EditParams(rotate_quarter=1)).size == (60, 100)


def test_no_rotation_keeps_dimensions():
    assert _out(_cutout(100, 60), EditParams()).size == (100, 60)


def test_straighten_expands_canvas():
    out = _out(_cutout(100, 60), EditParams(straighten_deg=10))
    assert out.size[0] > 100 and out.size[1] > 60   # expand=True grows canvas


def test_brightness_increases_pixel_values():
    base = _out(_cutout(colour=(100, 100, 100)), EditParams(autocontrast=False))
    bright = _out(_cutout(colour=(100, 100, 100)),
                  EditParams(autocontrast=False, brightness=1.4))
    assert bright.getpixel((0, 0))[0] > base.getpixel((0, 0))[0]


def test_gray_world_neutralises_colour_cast():
    out = _out(_cutout(colour=(160, 120, 120)),
               EditParams(autocontrast=False, white_balance=True))
    r, g, _b = out.getpixel((0, 0))
    assert abs(r - g) < 160 - 120          # red cast reduced vs original 40-gap
```

- [ ] **Step 5: Run both pipeline test files**

Run: `poetry run pytest tests/test_edit_pipeline_bg.py tests/test_edit_pipeline_geometry.py -v`
Expected: all pass (three existing + three new in bg; five migrated in geometry).

- [ ] **Step 6: Commit**

```bash
git add mockup_generator/generation/edit_pipeline.py tests/test_edit_pipeline_bg.py tests/test_edit_pipeline_geometry.py
git commit -m "refactor(edit): split pipeline into compute_cutout + render

Segmentation now precedes geometry/colour so the cutout depends only on
the source bytes and can be cached across adjustments. apply_edits kept
as a wrapper."
```

---

### Task 2: Cutout cache + wire preview/publish to it

**Files:**
- Modify: `backend/routers/import_shots.py`
- Test: `tests/test_import_cache.py` (create)
- Test: `tests/test_import_shots_api.py` (update — it monkeypatches the removed `apply_edits` call path)

**Interfaces:**
- Consumes: `edit_pipeline.compute_cutout`, `edit_pipeline.render` (Task 1).
- Produces (module-level in `import_shots`): `_get_cutout(file_id: str) -> PIL.Image.Image`, `_render(file_id: str, params_model) -> bytes`, `_CUTOUT_CACHE`, `_CACHE_CAP = 12`.

- [ ] **Step 1: Write failing tests for cache-once behavior**

Create `tests/test_import_cache.py`:

```python
from io import BytesIO

import pytest
from PIL import Image

import backend.routers.import_shots as mod
from backend.schemas import EditParamsModel


def _rgba(colour=(120, 60, 30), size=(40, 40)):
    return Image.new("RGBA", size, colour + (255,))


@pytest.fixture(autouse=True)
def _clear_cache():
    mod._CUTOUT_CACHE.clear()
    yield
    mod._CUTOUT_CACHE.clear()


def test_get_cutout_computes_once_per_file_id(monkeypatch):
    downloads, computes = [], []
    monkeypatch.setattr(mod, "_download", lambda fid: downloads.append(fid) or b"bytes")
    monkeypatch.setattr(mod.edit_pipeline, "compute_cutout",
                        lambda b: computes.append(b) or _rgba())

    a = mod._get_cutout("file-A")
    b = mod._get_cutout("file-A")
    assert a is b                      # same cached object
    assert downloads == ["file-A"]     # Drive hit exactly once
    assert len(computes) == 1          # BiRefNet ran exactly once


def test_cache_evicts_least_recently_used(monkeypatch):
    monkeypatch.setattr(mod, "_download", lambda fid: b"bytes")
    monkeypatch.setattr(mod.edit_pipeline, "compute_cutout", lambda b: _rgba())
    for i in range(mod._CACHE_CAP + 3):
        mod._get_cutout(f"file-{i}")
    assert len(mod._CUTOUT_CACHE) == mod._CACHE_CAP
    assert "file-0" not in mod._CUTOUT_CACHE     # oldest evicted


def test_render_uses_cached_cutout(monkeypatch):
    computes = []
    monkeypatch.setattr(mod, "_download", lambda fid: b"bytes")
    monkeypatch.setattr(mod.edit_pipeline, "compute_cutout",
                        lambda b: computes.append(1) or _rgba())
    p = EditParamsModel()
    mod._render("file-X", p)
    mod._render("file-X", p)
    assert len(computes) == 1          # second render is a cache hit
```

- [ ] **Step 2: Run to verify failure**

Run: `poetry run pytest tests/test_import_cache.py -v`
Expected: FAIL — `AttributeError: module 'backend.routers.import_shots' has no attribute '_CUTOUT_CACHE'`.

- [ ] **Step 3: Implement the cache and render helper**

In `backend/routers/import_shots.py`:

Add imports near the top (after existing imports):

```python
import threading
from collections import OrderedDict

from PIL import Image

from mockup_generator.generation import edit_pipeline
```

Replace the `_edit` helper (lines 44–48) with the cache + render helper:

```python
_CUTOUT_CACHE: "OrderedDict[str, Image.Image]" = OrderedDict()
_CACHE_CAP = 12
_CACHE_LOCK = threading.Lock()


def _get_cutout(file_id: str) -> Image.Image:
    """Return the RGBA cutout for a Drive file, computing+caching on a miss.

    The expensive Drive download + BiRefNet run happen at most once per
    file_id until eviction. Compute happens outside the lock so a slow run
    never blocks cache hits.
    """
    with _CACHE_LOCK:
        cached = _CUTOUT_CACHE.get(file_id)
        if cached is not None:
            _CUTOUT_CACHE.move_to_end(file_id)
            return cached
    try:
        cutout = edit_pipeline.compute_cutout(_download(file_id))
    except BackgroundRemovalUnavailable as exc:
        raise HTTPException(status_code=503, detail="Background removal is unavailable on the server") from exc
    with _CACHE_LOCK:
        _CUTOUT_CACHE[file_id] = cutout
        _CUTOUT_CACHE.move_to_end(file_id)
        while len(_CUTOUT_CACHE) > _CACHE_CAP:
            _CUTOUT_CACHE.popitem(last=False)
    return cutout


def _render(file_id: str, params_model) -> bytes:
    return edit_pipeline.render(_get_cutout(file_id),
                                EditParams(**params_model.model_dump()))
```

Update `preview` (line 70) and `publish_shot` (line 77) to use `_render`:

```python
    png = _render(req.file_id, req.params)
```
```python
    webp = publish._encode_webp(_render(req.file_id, req.params))
```

Remove the now-unused `edit_pipeline.apply_edits` reference from the old `_edit`. Keep the existing `EditParams` / `BackgroundRemovalUnavailable` import from `edit_pipeline` (line 25).

- [ ] **Step 4: Update the existing router tests to the new call path**

`tests/test_import_shots_api.py` monkeypatches `im.edit_pipeline.apply_edits`, which `/preview` and `/publish` no longer call. Update the three affected tests to the new seam and add an autouse fixture that clears the module cache between tests (the cache is a process global; without clearing, a `file_id` cached by one test leaks into the next).

Add after the `client` fixture:

```python
@pytest.fixture(autouse=True)
def _clear_cutout_cache():
    im._CUTOUT_CACHE.clear()
    yield
    im._CUTOUT_CACHE.clear()
```

In `test_publish_uploads_webp_only_and_inserts_one_row`, replace the two lines
```python
    monkeypatch.setattr(im.drive_client, "download_file", lambda fid: b"SRC")
    monkeypatch.setattr(im.edit_pipeline, "apply_edits", lambda src, params: b"PNG")
```
with:
```python
    monkeypatch.setattr(im, "_render", lambda fid, params: b"PNG")
```

In `test_preview_returns_data_uri`, replace its two setup lines with:
```python
    monkeypatch.setattr(im, "_render", lambda fid, params: b"PNGBYTES")
```

In `test_preview_503_when_bg_unavailable`, exercise the real cache→503 path:
```python
    monkeypatch.setattr(im, "_download", lambda fid: b"SRC")
    def _boom(src_bytes):
        raise im.edit_pipeline.BackgroundRemovalUnavailable("no model")
    monkeypatch.setattr(im.edit_pipeline, "compute_cutout", _boom)
    r = client.post("/api/import/preview", json={"file_id": "f1", "params": {}})
    assert r.status_code == 503
```

Leave `test_drive_images_lists_folder` unchanged.

- [ ] **Step 5: Run both router test files to verify pass**

Run: `poetry run pytest tests/test_import_cache.py tests/test_import_shots_api.py -v`
Expected: all pass (cache tests + the four router tests).

- [ ] **Step 6: Commit**

```bash
git add backend/routers/import_shots.py tests/test_import_cache.py tests/test_import_shots_api.py
git commit -m "feat(import): cache BiRefNet cutout per Drive file_id

Preview/publish now render from a cached cutout, so adjustments no longer
re-download from Drive or re-run background removal. LRU capped at 12."
```

---

### Task 3: `/warm` endpoint to pre-compute the cutout on editor open

**Files:**
- Modify: `backend/schemas.py`, `backend/routers/import_shots.py`
- Test: `tests/test_import_cache.py` (add endpoint test)

**Interfaces:**
- Consumes: `_get_cutout` (Task 2).
- Produces: `POST /api/import/warm` accepting `{ "file_id": str }`, returning `{ "status": "ok" }`; `WarmRequest` schema.

- [ ] **Step 1: Write the failing endpoint test**

Append to `tests/test_import_cache.py`:

```python
from fastapi.testclient import TestClient
from backend.main import app
from backend.auth import get_current_user, CurrentUser
from backend.deps import get_db
from mockup_generator.db.profiles_repo import Profile


@pytest.fixture
def client():
    u = CurrentUser(id="u1", email="a@b.c", role="user",
                    profile=Profile(id="u1", email="a@b.c", role="user", is_active=True))
    app.dependency_overrides[get_current_user] = lambda: u
    app.dependency_overrides[get_db] = lambda: object()
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def test_warm_populates_cache(client, monkeypatch):
    monkeypatch.setattr(mod, "_download", lambda fid: b"bytes")
    monkeypatch.setattr(mod.edit_pipeline, "compute_cutout", lambda b: _rgba())
    r = client.post("/api/import/warm", json={"file_id": "file-W"})
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    assert "file-W" in mod._CUTOUT_CACHE
```

- [ ] **Step 2: Run to verify failure**

Run: `poetry run pytest tests/test_import_cache.py::test_warm_populates_cache -v`
Expected: FAIL with `404` (route not registered).

- [ ] **Step 3: Add the schema and endpoint**

In `backend/schemas.py`, after `PreviewRequest` (line 202) add:

```python
class WarmRequest(BaseModel):
    file_id: str
```

In `backend/routers/import_shots.py`, add `WarmRequest` to the `backend.schemas` import list, and add the endpoint after `preview`:

```python
@router.post("/warm")
def warm(req: WarmRequest, user: CurrentUser = Depends(get_current_user)):
    """Pre-compute + cache the cutout so the first adjustment is instant too."""
    _get_cutout(req.file_id)
    return {"status": "ok"}
```

- [ ] **Step 4: Run to verify pass**

Run: `poetry run pytest tests/test_import_cache.py -v`
Expected: all cache + warm tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/schemas.py backend/routers/import_shots.py tests/test_import_cache.py
git commit -m "feat(import): add /warm endpoint to pre-cache the cutout on open"
```

---

### Task 4: Include Product ID in published filenames

**Files:**
- Modify: `backend/routers/import_shots.py:80`
- Test: `tests/test_import_publish_name.py` (create)

**Interfaces:**
- Consumes: `publish_shot` internals; `storage_client.upload_mockup(productid, data, key, ...)`.
- Produces: object key stem `{productid}_{color-slug}_{order}` (color/order omitted when empty).

- [ ] **Step 1: Write the failing test**

Create `tests/test_import_publish_name.py`:

```python
import pytest
from fastapi.testclient import TestClient

import backend.routers.import_shots as mod
from backend.main import app
from backend.auth import get_current_user, CurrentUser
from backend.deps import get_db
from mockup_generator.db.profiles_repo import Profile


@pytest.fixture
def client():
    u = CurrentUser(id="u1", email="a@b.c", role="user",
                    profile=Profile(id="u1", email="a@b.c", role="user", is_active=True))
    app.dependency_overrides[get_current_user] = lambda: u
    app.dependency_overrides[get_db] = lambda: object()
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def test_published_key_contains_product_id(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(mod, "_render", lambda fid, params: b"webp-source")
    monkeypatch.setattr(mod.publish, "_encode_webp", lambda b: b)
    monkeypatch.setattr(mod.productimages_repo, "next_product_shot_order", lambda db, pid: 20)
    monkeypatch.setattr(mod.storage_client, "slugify", lambda c: "red")
    monkeypatch.setattr(mod.storage_client, "short_hex", lambda: "deadbeef")
    monkeypatch.setattr(mod.productimages_repo, "insert",
                        lambda db, **kw: None)

    def _fake_upload(productid, data, key, *, ext, content_type):
        captured["productid"] = productid
        captured["key"] = key
        return f"{productid}/{key}.{ext}", f"https://x/{productid}/{key}.{ext}"

    monkeypatch.setattr(mod.storage_client, "upload_mockup", _fake_upload)

    r = client.post("/api/import/publish",
                    json={"productid": "BC25001", "file_id": "f1", "color": "Red", "params": {}})
    assert r.status_code == 200
    assert captured["key"] == "BC25001_red_20_deadbeef"
```

- [ ] **Step 2: Run to verify failure**

Run: `poetry run pytest tests/test_import_publish_name.py -v`
Expected: FAIL — key is `red_20_deadbeef` (no product ID).

- [ ] **Step 3: Prepend the product ID to the stem**

In `backend/routers/import_shots.py`, in `publish_shot`, change the stem line (line 80):

```python
    stem = "_".join(p for p in (req.productid, slug, str(order)) if p)
```

- [ ] **Step 4: Run to verify pass**

Run: `poetry run pytest tests/test_import_publish_name.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/routers/import_shots.py tests/test_import_publish_name.py
git commit -m "feat(import): include product ID in published shot filenames"
```

---

### Task 5: Frontend — pre-warm on open + show Product ID

**Files:**
- Modify: `frontend/src/api.ts`, `frontend/src/components/ProductShotsTab.tsx`
- Verify: `cd frontend && npm run build` (no frontend test harness; typecheck + manual visual check)

**Interfaces:**
- Consumes: `POST /api/import/warm` (Task 3); `selected.productid`, `active` in `ProductShotsTab`.
- Produces: `warmImportShot(file_id: string)` in `api.ts`.

> **Before editing the header, invoke the `ui-ux-pro-max` skill** for the Product ID display (spacing, type scale, contrast). The markup below is the intent; apply the skill's classes/rules.

- [ ] **Step 1: Add the warm API client**

In `frontend/src/api.ts`, after `previewImportShot` (line 583):

```typescript
export const warmImportShot = (file_id: string) =>
  apiFetch<{ status: string }>("/api/import/warm", {
    method: "POST",
    body: JSON.stringify({ file_id }),
  });
```

- [ ] **Step 2: Pre-warm the cutout when the editor opens**

In `frontend/src/components/ProductShotsTab.tsx`, add `warmImportShot` to the existing import from `../api`, then in `openEditor` (line 162–171) add a fire-and-forget warm call after `setActive(img)`:

```typescript
  const openEditor = useCallback(
    (img: ImportImage) => {
      setActive(img);
      warmImportShot(img.id).catch(() => {}); // best-effort: preview still computes on miss
      setPreview(null);
      setColor("");
      setParams(defaultPreset ? { ...defaultPreset.params } : DEFAULT_EDIT_PARAMS);
    },
    [defaultPreset],
  );
```

- [ ] **Step 3: Show the Product ID in the editor header**

In `ProductShotsTab.tsx`, replace the toolbar heading (line 398) so the Product ID is visible while editing (apply `ui-ux-pro-max` classes):

```tsx
                <div>
                  <h2 className="section-label">Preview · {active.name}</h2>
                  <p className="text-xs text-muted">Product ID: {selected.productid}</p>
                </div>
```

- [ ] **Step 4: Typecheck / build**

Run: `cd frontend && npm run build`
Expected: build succeeds with no TypeScript errors.

- [ ] **Step 5: Manual verification**

Run backend (`poetry run uvicorn backend.main:app --reload`) + frontend (`cd frontend && npm run dev`). Open a product, open an image: confirm (a) Product ID shows in the editor header, (b) first adjustment is fast (cutout pre-warmed), (c) dragging sliders re-renders quickly with no repeated Drive/BiRefNet cost (watch backend logs — no repeated download/segment per adjustment).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api.ts frontend/src/components/ProductShotsTab.tsx
git commit -m "feat(product-shots): pre-warm cutout on open, show Product ID in editor"
```

---

## Self-Review

**Spec coverage:**
- Comment 1 (bg removal re-runs) → Tasks 1+2 (cutout computed once, cached).
- Comment 2 (fast adjustments) → Tasks 1+2 (`render` from cache) + Task 5 (pre-warm).
- Comment 3 (Product ID at top + in filename) → Task 4 (filename) + Task 5 (header).
- Comment 4 (500 / "rate limit") → Tasks 1+2 remove per-adjustment Drive download and repeat BiRefNet; residual diagnosis via HF logs noted in spec (out of code scope).
- Known behavior change (colour after cutout) → realized in Task 1 `render`.

**Placeholder scan:** none — every code step shows full content.

**Type consistency:** `compute_cutout`/`render`/`apply_edits` signatures identical across Tasks 1–3; `_get_cutout`/`_render`/`_CUTOUT_CACHE`/`_CACHE_CAP` consistent Tasks 2–4; `warmImportShot(file_id)` matches `/warm` `{file_id}` schema (Task 3 ↔ Task 5).
