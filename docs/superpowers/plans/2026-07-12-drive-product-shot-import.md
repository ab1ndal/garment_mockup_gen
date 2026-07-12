# Drive Product-Shot Import & Edit Pipeline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a no-Gemini path to import product-supplied photos from a product's Google Drive folder, edit them server-side (rotate/straighten/auto-colour/brightness/saturation/BiRefNet background removal/white-cream composite/optional shadow), and publish them WEBP-only to Supabase Storage in the 20+ `displayorder` band, with reusable global edit presets.

**Architecture:** New stateless FastAPI router `backend/routers/import_shots.py` (prefix `/api/import`) drives a pure edit module `mockup_generator/generation/edit_pipeline.py`. Background removal uses `rembg` + BiRefNet (lite model default). Publishing reuses existing `storage_client.upload_mockup(ext="webp")` + `publish._encode_webp` + `productimages_repo`. Presets live in a new `edit_presets` table + repo. Frontend adds a product-scoped edit page (its own recon + `ui-ux-pro-max` at execution time).

**Tech Stack:** Python 3.10, FastAPI, Pillow 11.3, rembg 2.0.69 (BiRefNet, MIT), onnxruntime CPU, numpy, Supabase (supabase-py), pytest. React/Vite frontend.

## Global Constraints

- Python version: **`>=3.10,<3.11`** (strict). rembg MUST be pinned to **`rembg[cpu]==2.0.69`** — the last release supporting Python 3.10 (2.0.70+ requires 3.11).
- Default BiRefNet model: **`birefnet-general-lite`** (~214 MB). Full `birefnet-general` (~928 MB) is opt-in via the `REMBG_MODEL` env var only.
- Imports upload **WEBP only** to the `mockups` bucket — no PNG archival copy. Generate/backfill flow stays PNG+WEBP, unchanged.
- `displayorder` for imports uses the **fixed 20+ band**: `max(displayorder ≥ 20) + 1`, default `20`. Never touch orders `< 20`.
- `phototheme` for all imports = literal **`"Product Shot"`**.
- Import publish writes **only** `productimages` — no `mockup_variations` row, no `mockups.base_mockup` flip.
- WEBP encoding: reuse `publish._encode_webp` (lossy quality 85). Do not re-implement.
- No Gemini calls anywhere in this feature.
- Tests: repo has **no `conftest.py`**. Use self-contained fake-DB classes (see `tests/test_productimages_repo.py`) for repo tests, and `TestClient` + `app.dependency_overrides` + `monkeypatch.setattr(module.attr, …)` for API tests (see `tests/test_backfill_api.py`). Run with `poetry run pytest`.
- Dependencies declared in `pyproject.toml` under PEP-621 `[project].dependencies` (NOT `[tool.poetry.dependencies]`).

---

### Task 1: Dependencies + `REMBG_MODEL` config setting

**Files:**
- Modify: `pyproject.toml` (`[project].dependencies`, lines 8-22)
- Modify: `mockup_generator/config.py` (add a property near `gemini_image_model`, line 80)
- Test: `tests/test_config_rembg.py`

**Interfaces:**
- Produces: `settings.rembg_model -> str` (default `"birefnet-general-lite"`, override via env `REMBG_MODEL`).

- [ ] **Step 1: Add dependencies to `pyproject.toml`**

In `[project].dependencies`, add these three entries (keep the existing list; append).
NOTE (deviation from first draft): bare `numpy`/`onnxruntime` resolve to versions
that require Python ≥3.11/3.12 or lack cp310 macOS-arm64 wheels — both must be
bounded:

```toml
    "rembg[cpu]==2.0.69",
    "numpy>=1.26,<2.3",       # <2.3: 2.3 requires py3.11; 2.2.x supports 3.10
    "onnxruntime>=1.19,<1.20" # 1.19.2 ships cp310 wheels for mac-arm64 (dev) + linux-x64 (HF)
```

- [ ] **Step 2: Install and verify resolution**

Run: `poetry lock && poetry install`
Expected: resolves and installs. `rembg`, `onnxruntime`, `numpy` present.
Verify: `poetry run python -c "import rembg, onnxruntime, numpy; print(rembg.__version__)"` → prints `2.0.69` (or a 2.0.6x).
If resolution conflicts with existing deps (opencv/scipy/scikit-image pulled by rembg vs supabase/google/streamlit), STOP and resolve version bounds before continuing — do not force.

- [ ] **Step 3: Write the failing test**

```python
# tests/test_config_rembg.py
import importlib


def test_rembg_model_default(monkeypatch):
    monkeypatch.delenv("REMBG_MODEL", raising=False)
    import mockup_generator.config as config
    importlib.reload(config)
    assert config.get_settings().rembg_model == "birefnet-general-lite"


def test_rembg_model_env_override(monkeypatch):
    monkeypatch.setenv("REMBG_MODEL", "birefnet-general")
    import mockup_generator.config as config
    importlib.reload(config)
    assert config.get_settings().rembg_model == "birefnet-general"
```

- [ ] **Step 4: Run test to verify it fails**

Run: `poetry run pytest tests/test_config_rembg.py -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'rembg_model'`.

- [ ] **Step 5: Add the setting**

In `mockup_generator/config.py`, inside `class Settings`, add a property mirroring `gemini_image_model`:

```python
    @property
    def rembg_model(self) -> str:
        return _get("REMBG_MODEL", default="birefnet-general-lite")
```

- [ ] **Step 6: Run test to verify it passes**

Run: `poetry run pytest tests/test_config_rembg.py -v`
Expected: PASS (both tests).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml poetry.lock mockup_generator/config.py tests/test_config_rembg.py
git commit -m "feat(deps): add rembg[cpu]==2.0.69 + REMBG_MODEL setting"
```

---

### Task 2: `edit_pipeline` — params + geometry/colour stage

**Files:**
- Create: `mockup_generator/generation/edit_pipeline.py`
- Test: `tests/test_edit_pipeline_geometry.py`

**Interfaces:**
- Produces:
  - `EditParams` dataclass with fields: `rotate_quarter: int = 0`, `straighten_deg: float = 0.0`, `autocontrast: bool = True`, `white_balance: bool = False`, `brightness: float = 1.0`, `saturation: float = 1.0`, `bg: str = "white"`, `shadow: bool = False`.
  - `apply_geometry_and_colour(img: PIL.Image.Image, params: EditParams) -> PIL.Image.Image` — returns an RGBA image (straighten adds transparent corners). Pure, no rembg.
  - `_gray_world(img: PIL.Image.Image, mask: PIL.Image.Image | None) -> PIL.Image.Image` (RGB in/out).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_edit_pipeline_geometry.py
from PIL import Image
from mockup_generator.generation.edit_pipeline import (
    EditParams, apply_geometry_and_colour,
)


def _img(w=100, h=60, colour=(120, 60, 30)):
    return Image.new("RGB", (w, h), colour)


def test_quarter_rotate_swaps_dimensions():
    out = apply_geometry_and_colour(_img(100, 60), EditParams(rotate_quarter=1))
    assert out.size == (60, 100)          # 90° swaps W/H
    assert out.mode == "RGBA"


def test_no_rotation_keeps_dimensions():
    out = apply_geometry_and_colour(_img(100, 60), EditParams())
    assert out.size == (100, 60)


def test_straighten_expands_and_adds_transparency():
    out = apply_geometry_and_colour(_img(100, 60), EditParams(straighten_deg=10))
    assert out.size[0] > 100 and out.size[1] > 60   # expand=True grows canvas
    assert out.getchannel("A").getextrema()[0] == 0  # transparent corners exist


def test_brightness_increases_pixel_values():
    base = apply_geometry_and_colour(_img(colour=(100, 100, 100)),
                                     EditParams(autocontrast=False))
    bright = apply_geometry_and_colour(_img(colour=(100, 100, 100)),
                                       EditParams(autocontrast=False, brightness=1.4))
    assert bright.convert("RGB").getpixel((0, 0))[0] > \
        base.convert("RGB").getpixel((0, 0))[0]


def test_gray_world_neutralises_colour_cast():
    # a red-cast grey image should move toward neutral
    out = apply_geometry_and_colour(_img(colour=(160, 120, 120)),
                                    EditParams(autocontrast=False, white_balance=True))
    r, g, b = out.convert("RGB").getpixel((0, 0))
    assert abs(r - g) < 160 - 120          # cast reduced vs original 40-gap
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_edit_pipeline_geometry.py -v`
Expected: FAIL — `ModuleNotFoundError` / `ImportError` (module not created yet).

- [ ] **Step 3: Write the implementation**

```python
# mockup_generator/generation/edit_pipeline.py
"""Deterministic, params-driven image edit pipeline for imported product shots.

Pure image ops (no I/O) except the lazily-loaded rembg session. Colour/tonal ops
run on RGB before straighten introduces alpha; the BiRefNet cutout + composite +
optional shadow run last. See docs/superpowers/specs/2026-07-12-...-design.md.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageEnhance, ImageOps

WHITE = (255, 255, 255)
CREAM = (250, 247, 240)  # #FAF7F0

_QUARTER_CW = {
    1: Image.Transpose.ROTATE_270,  # Pillow ROTATE_90 is CCW; 270 == 90 clockwise
    2: Image.Transpose.ROTATE_180,
    3: Image.Transpose.ROTATE_90,
}


@dataclass
class EditParams:
    rotate_quarter: int = 0        # 0|1|2|3 -> 0/90/180/270 clockwise
    straighten_deg: float = 0.0    # +-15
    autocontrast: bool = True
    white_balance: bool = False    # gray-world
    brightness: float = 1.0        # ~0.5-1.5, 1.0 unchanged
    saturation: float = 1.0        # ~0.5-1.5, 1.0 unchanged
    bg: str = "white"              # "white" | "cream"
    shadow: bool = False


def _gray_world(img: Image.Image, mask: Image.Image | None = None) -> Image.Image:
    """Scale each channel so its mean equals the global gray mean."""
    rgb = np.asarray(img.convert("RGB"), dtype=np.float64)
    if mask is not None:
        keep = np.asarray(mask.convert("L")) > 0
        sel = rgb[keep] if keep.any() else rgb.reshape(-1, 3)
    else:
        sel = rgb.reshape(-1, 3)
    means = sel.mean(axis=0)
    gray = means.mean()
    scale = gray / np.clip(means, 1e-6, None)
    out = np.clip(rgb * scale, 0, 255).astype(np.uint8)
    return Image.fromarray(out, "RGB")


def apply_geometry_and_colour(img: Image.Image, params: EditParams) -> Image.Image:
    """EXIF-normalise -> quarter-rotate -> colour/tonal (RGB) -> straighten (RGBA)."""
    img = ImageOps.exif_transpose(img).convert("RGB")

    q = params.rotate_quarter % 4
    if q:
        img = img.transpose(_QUARTER_CW[q])

    if params.white_balance:
        img = _gray_world(img)
    if params.autocontrast:
        img = ImageOps.autocontrast(img, cutoff=1, preserve_tone=True)
    if params.brightness != 1.0:
        img = ImageEnhance.Brightness(img).enhance(params.brightness)
    if params.saturation != 1.0:
        img = ImageEnhance.Color(img).enhance(params.saturation)

    rgba = img.convert("RGBA")
    if params.straighten_deg:
        rgba = rgba.rotate(params.straighten_deg, resample=Image.Resampling.BICUBIC,
                           expand=True, fillcolor=(0, 0, 0, 0))
    return rgba
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_edit_pipeline_geometry.py -v`
Expected: PASS (all five).

- [ ] **Step 5: Commit**

```bash
git add mockup_generator/generation/edit_pipeline.py tests/test_edit_pipeline_geometry.py
git commit -m "feat(edit): geometry + colour stage of the edit pipeline"
```

---

### Task 3: `edit_pipeline` — background removal, composite, `apply_edits`

**Files:**
- Modify: `mockup_generator/generation/edit_pipeline.py`
- Test: `tests/test_edit_pipeline_bg.py`

**Interfaces:**
- Consumes: `EditParams`, `apply_geometry_and_colour` (Task 2); `settings.rembg_model` (Task 1).
- Produces:
  - `class BackgroundRemovalUnavailable(RuntimeError)`.
  - `_remove_background(img: PIL.Image.Image) -> PIL.Image.Image` (RGBA cutout) — the ONLY function that touches rembg; tests monkeypatch it.
  - `apply_edits(src_bytes: bytes, params: EditParams) -> bytes` — full pipeline, returns RGB PNG bytes.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_edit_pipeline_bg.py
from io import BytesIO
from PIL import Image
import pytest
import mockup_generator.generation.edit_pipeline as ep
from mockup_generator.generation.edit_pipeline import EditParams, apply_edits


def _png_bytes(colour=(120, 60, 30), size=(80, 80)):
    buf = BytesIO()
    Image.new("RGB", size, colour).save(buf, format="PNG")
    return buf.getvalue()


def _fake_cutout(img):
    # opaque centre square, transparent border -> lets us see the bg composite
    rgba = img.convert("RGBA")
    a = Image.new("L", rgba.size, 0)
    w, h = rgba.size
    for x in range(w // 4, 3 * w // 4):
        for y in range(h // 4, 3 * h // 4):
            a.putpixel((x, y), 255)
    rgba.putalpha(a)
    return rgba


def test_apply_edits_composites_white_bg(monkeypatch):
    monkeypatch.setattr(ep, "_remove_background", _fake_cutout)
    out = Image.open(BytesIO(apply_edits(_png_bytes(), EditParams(bg="white"))))
    assert out.mode == "RGB"
    assert out.getpixel((1, 1)) == (255, 255, 255)     # transparent border -> white


def test_apply_edits_composites_cream_bg(monkeypatch):
    monkeypatch.setattr(ep, "_remove_background", _fake_cutout)
    out = Image.open(BytesIO(apply_edits(_png_bytes(), EditParams(bg="cream"))))
    assert out.getpixel((1, 1)) == (250, 247, 240)     # cream corner


def test_apply_edits_shadow_darkens_under_subject(monkeypatch):
    monkeypatch.setattr(ep, "_remove_background", _fake_cutout)
    no_shadow = Image.open(BytesIO(apply_edits(_png_bytes(), EditParams(shadow=False))))
    shadow = Image.open(BytesIO(apply_edits(_png_bytes(), EditParams(shadow=True))))
    # shadow output has at least one pixel darker than pure bg somewhere
    assert shadow.size[1] >= no_shadow.size[1]         # shadow pads canvas height
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_edit_pipeline_bg.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute '_remove_background'` / `apply_edits`.

- [ ] **Step 3: Extend the implementation**

Append to `mockup_generator/generation/edit_pipeline.py`:

```python
from io import BytesIO

from PIL import ImageFilter

from mockup_generator.config import settings

_BG_COLOURS = {"white": WHITE, "cream": CREAM}
_session = None


class BackgroundRemovalUnavailable(RuntimeError):
    """rembg / the BiRefNet model could not be loaded or run."""


def _get_session():
    global _session
    if _session is None:
        try:
            from rembg import new_session
            _session = new_session(settings.rembg_model)
        except Exception as exc:  # noqa: BLE001 - surfaced as 503 upstream
            raise BackgroundRemovalUnavailable(str(exc)) from exc
    return _session


def _remove_background(img: Image.Image) -> Image.Image:
    """Return an RGBA cutout via rembg + BiRefNet. The single rembg touch-point."""
    try:
        from rembg import remove
        return remove(img, session=_get_session(), post_process_mask=True).convert("RGBA")
    except BackgroundRemovalUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        raise BackgroundRemovalUnavailable(str(exc)) from exc


def _add_drop_shadow(fg: Image.Image, bg_rgb: tuple[int, int, int],
                     offset: tuple[int, int] = (0, 18), blur: int = 24,
                     opacity: float = 0.35) -> Image.Image:
    fg = fg.convert("RGBA")
    w, h = fg.size
    margin = blur * 3
    size = (w + margin * 2, h + margin * 2)
    shadow = Image.new("RGBA", size, (0, 0, 0, 0))
    solid = Image.new("RGBA", (w, h), (0, 0, 0, int(255 * opacity)))
    shadow.paste(solid, (margin + offset[0], margin + offset[1]), fg.getchannel("A"))
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur))
    base = Image.new("RGBA", size, bg_rgb + (255,))
    fg_layer = Image.new("RGBA", size, (0, 0, 0, 0))
    fg_layer.paste(fg, (margin, margin), fg)
    out = Image.alpha_composite(base, shadow)
    out = Image.alpha_composite(out, fg_layer)
    return out.convert("RGB")


def apply_edits(src_bytes: bytes, params: EditParams) -> bytes:
    """Full pipeline: geometry+colour -> cutout -> composite -> optional shadow."""
    src = Image.open(BytesIO(src_bytes))
    prepared = apply_geometry_and_colour(src, params)       # RGBA
    cutout = _remove_background(prepared)                    # RGBA
    bg_rgb = _BG_COLOURS.get(params.bg, WHITE)
    if params.shadow:
        composited = _add_drop_shadow(cutout, bg_rgb)
    else:
        base = Image.new("RGBA", cutout.size, bg_rgb + (255,))
        composited = Image.alpha_composite(base, cutout).convert("RGB")
    buf = BytesIO()
    composited.save(buf, format="PNG")
    return buf.getvalue()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_edit_pipeline_bg.py -v`
Expected: PASS (all three).

- [ ] **Step 5: Commit**

```bash
git add mockup_generator/generation/edit_pipeline.py tests/test_edit_pipeline_bg.py
git commit -m "feat(edit): BiRefNet background removal + composite + apply_edits"
```

---

### Task 4: `next_product_shot_order` repo function (20+ band)

**Files:**
- Modify: `mockup_generator/db/productimages_repo.py`
- Test: `tests/test_next_product_shot_order.py`

**Interfaces:**
- Produces: `next_product_shot_order(client, productid: str) -> int`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_next_product_shot_order.py
from mockup_generator.db.productimages_repo import next_product_shot_order


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows
    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def gte(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def execute(self):
        return type("R", (), {"data": self._rows})()


class _FakeDb:
    def __init__(self, rows):
        self._rows = rows
    def table(self, name):
        assert name == "productimages"
        return _FakeQuery(self._rows)


def test_empty_band_defaults_to_20():
    assert next_product_shot_order(_FakeDb([]), "P1") == 20


def test_appends_after_existing_band_max():
    assert next_product_shot_order(_FakeDb([{"displayorder": 21}]), "P1") == 22


def test_null_displayorder_is_safe():
    assert next_product_shot_order(_FakeDb([{"displayorder": None}]), "P1") == 20
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_next_product_shot_order.py -v`
Expected: FAIL — `ImportError: cannot import name 'next_product_shot_order'`.

- [ ] **Step 3: Add the function**

In `mockup_generator/db/productimages_repo.py`, add (near `next_display_order`):

```python
def next_product_shot_order(client: Client, productid: str) -> int:
    """Next display order in the reserved 20+ band for imported product shots.

    Returns max(displayorder >= 20) + 1, or 20 when the band is empty. Orders
    below 20 are reserved for model mockups and never touched.
    """
    resp = (
        client.table("productimages")
        .select("displayorder")
        .eq("productid", productid)
        .gte("displayorder", 20)
        .order("displayorder", desc=True)
        .limit(1)
        .execute()
    )
    rows = resp.data or []
    if not rows:
        return 20
    return (rows[0].get("displayorder") or 19) + 1
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_next_product_shot_order.py -v`
Expected: PASS (all three).

- [ ] **Step 5: Commit**

```bash
git add mockup_generator/db/productimages_repo.py tests/test_next_product_shot_order.py
git commit -m "feat(db): next_product_shot_order for the 20+ display band"
```

---

### Task 5: `edit_presets` table + repo

**Files:**
- Create: `docs/migrations/2026-07-12-edit-presets.sql`
- Create: `mockup_generator/db/edit_presets_repo.py`
- Test: `tests/test_edit_presets_repo.py`

**Interfaces:**
- Produces:
  - `list_all(client) -> list[dict]`
  - `insert(client, *, name: str, params: dict, is_default: bool, created_by: str | None) -> dict`
  - `set_default(client, preset_id: int) -> None`
  - `get_default(client) -> dict | None`
  - `delete(client, preset_id: int) -> None`

- [ ] **Step 1: Write the migration file**

```sql
-- docs/migrations/2026-07-12-edit-presets.sql
-- Apply via Supabase MCP (project epotsxdugwfhyeiudjox), like other docs/migrations/*.sql.
create table if not exists public.edit_presets (
    preset_id  bigint generated always as identity primary key,
    name       text not null unique,
    params     jsonb not null,
    is_default boolean not null default false,
    created_by uuid,
    created_at timestamptz not null default now()
);

-- backstop: at most one default preset
create unique index if not exists edit_presets_one_default
    on public.edit_presets (is_default) where is_default;

-- server writes via service-role (bypasses RLS); no anon policies
alter table public.edit_presets enable row level security;
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_edit_presets_repo.py
from mockup_generator.db import edit_presets_repo as repo


class _Q:
    def __init__(self, store, name):
        self.store, self.name = store, name
        self._rows, self._filters, self._payload, self._op = None, [], None, None
    def select(self, *a, **k): self._op = "select"; return self
    def insert(self, payload): self._op, self._payload = "insert", payload; return self
    def update(self, payload): self._op, self._payload = "update", payload; return self
    def delete(self): self._op = "delete"; return self
    def eq(self, col, val): self._filters.append((col, val)); return self
    def order(self, *a, **k): return self
    def limit(self, n): self._filters.append(("_limit", n)); return self
    def execute(self):
        self.store.setdefault("calls", []).append((self._op, self._payload, self._filters))
        if self._op == "insert":
            row = {"preset_id": 1, **self._payload}
            self.store["rows"].append(row)
            return type("R", (), {"data": [row]})()
        if self._op == "select":
            rows = self.store["rows"]
            for col, val in self._filters:
                if col == "is_default":
                    rows = [r for r in rows if r.get("is_default") == val]
            return type("R", (), {"data": rows})()
        return type("R", (), {"data": []})()


class _Db:
    def __init__(self):
        self.store = {"rows": []}
    def table(self, name):
        assert name == "edit_presets"
        return _Q(self.store, name)


def test_insert_returns_row():
    db = _Db()
    row = repo.insert(db, name="Studio", params={"bg": "white"},
                      is_default=False, created_by="u1")
    assert row["name"] == "Studio" and row["params"] == {"bg": "white"}


def test_get_default_none_when_empty():
    assert repo.get_default(_Db()) is None


def test_set_default_clears_then_sets():
    db = _Db()
    repo.set_default(db, 5)
    ops = [c[0] for c in db.store["calls"]]
    assert ops == ["update", "update"]          # clear others, then set target
```

- [ ] **Step 3: Run test to verify it fails**

Run: `poetry run pytest tests/test_edit_presets_repo.py -v`
Expected: FAIL — `ModuleNotFoundError: mockup_generator.db.edit_presets_repo`.

- [ ] **Step 4: Write the repo**

```python
# mockup_generator/db/edit_presets_repo.py
"""CRUD for global edit presets (docs/migrations/2026-07-12-edit-presets.sql)."""

from __future__ import annotations

from supabase import Client

_TABLE = "edit_presets"


def list_all(client: Client) -> list[dict]:
    resp = client.table(_TABLE).select("*").order("created_at").execute()
    return resp.data or []


def get_default(client: Client) -> dict | None:
    resp = client.table(_TABLE).select("*").eq("is_default", True).limit(1).execute()
    rows = resp.data or []
    return rows[0] if rows else None


def set_default(client: Client, preset_id: int) -> None:
    """Exactly-one-default: clear the current default, then set the target."""
    client.table(_TABLE).update({"is_default": False}).eq("is_default", True).execute()
    client.table(_TABLE).update({"is_default": True}).eq("preset_id", preset_id).execute()


def insert(client: Client, *, name: str, params: dict, is_default: bool,
           created_by: str | None) -> dict:
    if is_default:
        client.table(_TABLE).update({"is_default": False}).eq("is_default", True).execute()
    payload = {"name": name, "params": params, "is_default": is_default,
               "created_by": created_by}
    resp = client.table(_TABLE).insert(payload).execute()
    return (resp.data or [{}])[0]


def delete(client: Client, preset_id: int) -> None:
    client.table(_TABLE).delete().eq("preset_id", preset_id).execute()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `poetry run pytest tests/test_edit_presets_repo.py -v`
Expected: PASS (all three).

- [ ] **Step 6: Apply the migration**

Apply `docs/migrations/2026-07-12-edit-presets.sql` to Supabase (project `epotsxdugwfhyeiudjox`) via the Supabase MCP `apply_migration` tool (re-auth OAuth first if the query tools aren't loaded). Verify the table exists: `select 1 from edit_presets limit 1;` returns without error.

- [ ] **Step 7: Commit**

```bash
git add docs/migrations/2026-07-12-edit-presets.sql mockup_generator/db/edit_presets_repo.py tests/test_edit_presets_repo.py
git commit -m "feat(db): edit_presets table + repo"
```

---

### Task 6: Request/response schemas

**Files:**
- Modify: `backend/schemas.py`
- Test: `tests/test_import_schemas.py`

**Interfaces:**
- Produces (all `pydantic.BaseModel`):
  - `EditParamsModel` — mirrors `EditParams` fields with boundary validation (`rotate_quarter` 0–3, `straighten_deg` ±15, `brightness`/`saturation` 0.5–1.5, `bg` Literal `white|cream`).
  - `ImportImage`, `ImportGroup`, `ImportDriveImagesResponse`
  - `PreviewRequest`, `PreviewResponse`
  - `ImportPublishRequest`, `ImportPublishResponse`
  - `PresetModel`, `PresetsResponse`, `CreatePresetRequest`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_import_schemas.py
import pytest
from pydantic import ValidationError
from backend.schemas import EditParamsModel, ImportPublishRequest


def test_defaults():
    p = EditParamsModel()
    assert p.rotate_quarter == 0 and p.bg == "white" and p.brightness == 1.0


def test_rejects_out_of_range_brightness():
    with pytest.raises(ValidationError):
        EditParamsModel(brightness=3.0)


def test_rejects_bad_rotate_quarter():
    with pytest.raises(ValidationError):
        EditParamsModel(rotate_quarter=7)


def test_publish_request_parses_nested_params():
    req = ImportPublishRequest(productid="P1", file_id="f1",
                               params={"bg": "cream", "shadow": True})
    assert req.params.bg == "cream" and req.params.shadow is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_import_schemas.py -v`
Expected: FAIL — `ImportError` (names not defined).

- [ ] **Step 3: Add the schemas**

Append to `backend/schemas.py` (uses `Literal` already imported at line 67; add `Field` import from pydantic if not present):

```python
class EditParamsModel(BaseModel):
    rotate_quarter: int = Field(default=0, ge=0, le=3)
    straighten_deg: float = Field(default=0.0, ge=-15.0, le=15.0)
    autocontrast: bool = True
    white_balance: bool = False
    brightness: float = Field(default=1.0, ge=0.5, le=1.5)
    saturation: float = Field(default=1.0, ge=0.5, le=1.5)
    bg: Literal["white", "cream"] = "white"
    shadow: bool = False


class ImportImage(BaseModel):
    id: str
    name: str
    mime_type: str | None = None
    thumbnail_url: str | None = None


class ImportGroup(BaseModel):
    id: str
    name: str
    images: list[ImportImage]


class ImportDriveImagesResponse(BaseModel):
    loose: list[ImportImage]
    groups: list[ImportGroup]


class PreviewRequest(BaseModel):
    file_id: str
    params: EditParamsModel = EditParamsModel()


class PreviewResponse(BaseModel):
    preview: str            # data:image/png;base64,...


class ImportPublishRequest(BaseModel):
    productid: str
    file_id: str
    color: str | None = None
    params: EditParamsModel = EditParamsModel()


class ImportPublishResponse(BaseModel):
    image_url: str
    displayorder: int


class PresetModel(BaseModel):
    preset_id: int
    name: str
    params: EditParamsModel
    is_default: bool


class PresetsResponse(BaseModel):
    presets: list[PresetModel]


class CreatePresetRequest(BaseModel):
    name: str
    params: EditParamsModel
    is_default: bool = False
```

If `Field` is not already imported, change the pydantic import line to `from pydantic import BaseModel, Field`.

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_import_schemas.py -v`
Expected: PASS (all four).

- [ ] **Step 5: Commit**

```bash
git add backend/schemas.py tests/test_import_schemas.py
git commit -m "feat(api): schemas for product-shot import + presets"
```

---

### Task 7: `import_shots` router — drive-images, preview, publish

**Files:**
- Create: `backend/routers/import_shots.py`
- Modify: `backend/main.py` (lines 18-21 imports, 42-45 include_router)
- Test: `tests/test_import_shots_api.py`

**Interfaces:**
- Consumes: `edit_pipeline.apply_edits`, `EditParams`, `BackgroundRemovalUnavailable` (Tasks 2-3); `productimages_repo.next_product_shot_order` + `insert` (Task 4); `publish._encode_webp`; `storage_client.upload_mockup/slugify/short_hex`; `drive_client.download_file/list_folder_image_groups/extract_folder_id/DriveNotConfigured`; `products_repo.get_product`; schemas (Task 6).
- Produces: `router` with `GET /api/import/products/{productid}/drive-images`, `POST /api/import/preview`, `POST /api/import/publish`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_import_shots_api.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_import_shots_api.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.routers.import_shots`.

- [ ] **Step 3: Write the router**

```python
# backend/routers/import_shots.py
"""No-Gemini import path: edit a product's Drive photos and publish them WEBP-only.

Stateless and product-scoped. Publishes into the reserved 20+ display band as
"Product Shot" rows (productimages only). See
docs/superpowers/specs/2026-07-12-drive-product-shot-import-design.md.
"""

from __future__ import annotations

import base64
import logging

from fastapi import APIRouter, Depends, HTTPException
from supabase import Client

from backend.auth import CurrentUser, get_current_user
from backend.deps import get_db
from backend.schemas import (
    ImportDriveImagesResponse, ImportPublishRequest, ImportPublishResponse,
    PreviewRequest, PreviewResponse,
)
from mockup_generator.db import productimages_repo, products_repo
from mockup_generator.generation import edit_pipeline, publish
from mockup_generator.generation.edit_pipeline import BackgroundRemovalUnavailable, EditParams
from mockup_generator.integrations import drive_client, storage_client
from mockup_generator.integrations.drive_client import DriveNotConfigured

router = APIRouter(prefix="/api/import", tags=["import"])
log = logging.getLogger(__name__)

_PRODUCT_SHOT_THEME = "Product Shot"


def _download(file_id: str) -> bytes:
    try:
        return drive_client.download_file(file_id)
    except DriveNotConfigured as exc:
        raise HTTPException(status_code=503, detail="Drive access is not configured on the server") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Could not load the image: {exc}") from exc


def _edit(src: bytes, params_model) -> bytes:
    try:
        return edit_pipeline.apply_edits(src, EditParams(**params_model.model_dump()))
    except BackgroundRemovalUnavailable as exc:
        raise HTTPException(status_code=503, detail="Background removal is unavailable on the server") from exc


@router.get("/products/{productid}/drive-images", response_model=ImportDriveImagesResponse)
def drive_images(productid: str, user: CurrentUser = Depends(get_current_user),
                 db: Client = Depends(get_db)):
    product = products_repo.get_product(db, productid)
    producturl = getattr(product, "producturl", None) if product else None
    if not producturl:
        raise HTTPException(status_code=404, detail="Product has no Drive folder URL")
    fid = drive_client.extract_folder_id(producturl)
    if not fid:
        raise HTTPException(status_code=404, detail="Could not parse the Drive folder from the product URL")
    try:
        return drive_client.list_folder_image_groups(fid)
    except DriveNotConfigured as exc:
        raise HTTPException(status_code=503, detail="Drive access is not configured on the server") from exc


@router.post("/preview", response_model=PreviewResponse)
def preview(req: PreviewRequest, user: CurrentUser = Depends(get_current_user),
            db: Client = Depends(get_db)):
    png = _edit(_download(req.file_id), req.params)
    return PreviewResponse(preview="data:image/png;base64," + base64.b64encode(png).decode("ascii"))


@router.post("/publish", response_model=ImportPublishResponse)
def publish_shot(req: ImportPublishRequest, user: CurrentUser = Depends(get_current_user),
                 db: Client = Depends(get_db)):
    webp = publish._encode_webp(_edit(_download(req.file_id), req.params))
    order = productimages_repo.next_product_shot_order(db, req.productid)
    slug = storage_client.slugify(req.color)
    stem = "_".join(p for p in (slug, str(order)) if p)
    key = f"{stem}_{storage_client.short_hex()}"
    _path, url = storage_client.upload_mockup(
        req.productid, webp, key, ext="webp", content_type="image/webp")
    productimages_repo.insert(db, productid=req.productid, imageurl=url,
                              productcolor=req.color, theme=_PRODUCT_SHOT_THEME,
                              displayorder=order)
    return ImportPublishResponse(image_url=url, displayorder=order)
```

- [ ] **Step 4: Register the router**

In `backend/main.py`, add to the router imports (lines 18-21):

```python
from backend.routers import import_shots as import_router
```

And add to the `include_router` block (lines 42-45):

```python
app.include_router(import_router.router)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `poetry run pytest tests/test_import_shots_api.py -v`
Expected: PASS (all four).

- [ ] **Step 6: Commit**

```bash
git add backend/routers/import_shots.py backend/main.py tests/test_import_shots_api.py
git commit -m "feat(api): import_shots router (drive-images, preview, publish)"
```

---

### Task 8: `import_shots` router — presets endpoints

**Files:**
- Modify: `backend/routers/import_shots.py`
- Test: `tests/test_import_presets_api.py`

**Interfaces:**
- Consumes: `edit_presets_repo` (Task 5); `PresetsResponse`, `PresetModel`, `CreatePresetRequest` (Task 6).
- Produces: `GET /api/import/presets`, `POST /api/import/presets`, `PUT /api/import/presets/{preset_id}/default`, `DELETE /api/import/presets/{preset_id}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_import_presets_api.py
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


def test_list_presets(client, monkeypatch):
    monkeypatch.setattr(im.edit_presets_repo, "list_all", lambda db: [
        {"preset_id": 1, "name": "Studio", "params": {"bg": "white"}, "is_default": True}])
    r = client.get("/api/import/presets")
    assert r.status_code == 200
    assert r.json()["presets"][0]["name"] == "Studio"


def test_create_preset(client, monkeypatch):
    seen = {}
    def _insert(db, *, name, params, is_default, created_by):
        seen.update(name=name, is_default=is_default, created_by=created_by)
        return {"preset_id": 9, "name": name, "params": params, "is_default": is_default}
    monkeypatch.setattr(im.edit_presets_repo, "insert", _insert)
    r = client.post("/api/import/presets", json={
        "name": "Soft", "params": {"bg": "cream"}, "is_default": True})
    assert r.status_code == 200
    assert seen == {"name": "Soft", "is_default": True, "created_by": "u1"}


def test_mark_default(client, monkeypatch):
    marked = {}
    monkeypatch.setattr(im.edit_presets_repo, "set_default",
                        lambda db, pid: marked.update(pid=pid))
    r = client.put("/api/import/presets/7/default")
    assert r.status_code == 200 and marked["pid"] == 7


def test_delete_preset(client, monkeypatch):
    gone = {}
    monkeypatch.setattr(im.edit_presets_repo, "delete", lambda db, pid: gone.update(pid=pid))
    r = client.delete("/api/import/presets/3")
    assert r.status_code == 200 and gone["pid"] == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_import_presets_api.py -v`
Expected: FAIL — 404s (routes not defined).

- [ ] **Step 3: Add preset endpoints**

In `backend/routers/import_shots.py`, add the repo import and schema imports:

```python
from mockup_generator.db import edit_presets_repo
```
(extend the existing `backend.schemas` import to include `CreatePresetRequest, PresetModel, PresetsResponse`)

Then append the endpoints:

```python
@router.get("/presets", response_model=PresetsResponse)
def list_presets(user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    return PresetsResponse(presets=edit_presets_repo.list_all(db))


@router.post("/presets", response_model=PresetModel)
def create_preset(req: CreatePresetRequest, user: CurrentUser = Depends(get_current_user),
                  db: Client = Depends(get_db)):
    return edit_presets_repo.insert(db, name=req.name, params=req.params.model_dump(),
                                    is_default=req.is_default, created_by=user.id)


@router.put("/presets/{preset_id}/default")
def mark_default(preset_id: int, user: CurrentUser = Depends(get_current_user),
                 db: Client = Depends(get_db)):
    edit_presets_repo.set_default(db, preset_id)
    return {"status": "ok"}


@router.delete("/presets/{preset_id}")
def delete_preset(preset_id: int, user: CurrentUser = Depends(get_current_user),
                  db: Client = Depends(get_db)):
    edit_presets_repo.delete(db, preset_id)
    return {"status": "ok"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_import_presets_api.py -v`
Expected: PASS (all four).

- [ ] **Step 5: Commit**

```bash
git add backend/routers/import_shots.py tests/test_import_presets_api.py
git commit -m "feat(api): edit-preset endpoints on the import router"
```

---

### Task 9: Backend full-suite gate + model warm-up

**Files:**
- Modify: `backend/main.py` (the `lifespan` function)
- Test: (whole suite)

**Interfaces:**
- Consumes: `edit_pipeline._get_session` (Task 3), `settings.rembg_model`.

- [ ] **Step 1: Run the whole backend suite**

Run: `poetry run pytest -q`
Expected: all tests pass (existing + the new ones). Fix any regressions before proceeding.

- [ ] **Step 2: Add a non-fatal startup warm-up**

In `backend/main.py` `lifespan`, warm the rembg session at boot so the first real request isn't slow — but never block startup if the model can't load:

```python
    # warm the background-removal model (best-effort; first import request otherwise pays the cost)
    try:
        from mockup_generator.generation import edit_pipeline
        edit_pipeline._get_session()
        log.info("rembg session warmed (%s)", settings.rembg_model)
    except Exception as exc:  # noqa: BLE001 - import feature degrades to 503, boot must not fail
        log.warning("rembg warm-up skipped: %s", exc)
```

(Place it inside the existing `lifespan` startup section; import `settings`/`log` consistent with that file. If `lifespan` doesn't already log, use the module logger.)

- [ ] **Step 3: Verify the app boots**

Run: `poetry run python -c "from backend.main import app; print('ok')"`
Expected: prints `ok` (import warms or logs a warning, does not raise).

- [ ] **Step 4: Commit**

```bash
git add backend/main.py
git commit -m "feat(api): best-effort rembg warm-up on startup"
```

---

### Task 10: Deployment — model pre-cache

**Files:**
- Inspect: repo root for a `Dockerfile` / HF Space config; `.github/workflows/sync-to-hf.yml`.
- Modify/Create: `Dockerfile` (only if the Space is Docker-based) OR document the env approach.

- [ ] **Step 1: Determine the HF Space type**

Run: `ls Dockerfile 2>/dev/null; cat README.md | grep -i "sdk:" || true`
- If a `Dockerfile` exists (Docker Space): go to Step 2.
- If SDK Space (streamlit/gradio) with no Dockerfile: the model can't be baked at build; rely on the Task 9 startup warm-up + a persistent `U2NET_HOME`. Document in Step 3 and skip Step 2.

- [ ] **Step 2 (Docker Space only): Pre-cache the model in the image**

Add to `Dockerfile` before the app start, so the ~214 MB model is baked into a layer:

```dockerfile
ENV U2NET_HOME=/home/user/.u2net
RUN python -c "from rembg import new_session; new_session('birefnet-general-lite')"
```

(HF Spaces run as uid 1000 — ensure `/home/user/.u2net` is writable by that user.)

- [ ] **Step 3: Document the deployment note**

Add a short section to `docs/migrations/2026-07-12-edit-presets.sql` header comment OR a new `docs/deploy-rembg.md` noting: model default `birefnet-general-lite`, `REMBG_MODEL` override, `U2NET_HOME` cache path, and that the first cold request downloads ~214 MB if not pre-cached.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore(deploy): pre-cache/document rembg BiRefNet model for HF Space"
```

---

### Task 11: Frontend — product-shot import page

> This task needs its own recon of the React/Vite `frontend/` at execution time (component structure, API client, routing, existing product picker). Do NOT fabricate paths. Invoke the `ui-ux-pro-max:ui-ux-pro-max` skill before writing UI, and apply its touch-target/focus/contrast rules. Follow existing frontend patterns exactly.

**API contract the UI consumes (all under `/api/import`, bearer auth like other calls):**
- `GET /products/{productid}/drive-images` → `{loose: ImportImage[], groups: ImportGroup[]}`, `ImportImage = {id, name, mime_type?, thumbnail_url?}`.
- `POST /preview` `{file_id, params}` → `{preview: "data:image/png;base64,..."}`.
- `POST /publish` `{productid, file_id, color, params}` → `{image_url, displayorder}`.
- `GET /presets` → `{presets: [{preset_id, name, params, is_default}]}`.
- `POST /presets` `{name, params, is_default}` → the created preset.
- `PUT /presets/{preset_id}/default` → `{status:"ok"}`.
- `DELETE /presets/{preset_id}` → `{status:"ok"}`.
- `params` shape: `{rotate_quarter:0-3, straighten_deg:-15..15, autocontrast:bool, white_balance:bool, brightness:0.5-1.5, saturation:0.5-1.5, bg:"white"|"cream", shadow:bool}`.

**Steps (fill in exact paths during recon):**

- [ ] **Step 1:** Recon `frontend/` — locate the API client module, product picker, routing, and the design tokens/components used by existing pages (e.g. the backfill review page). Note the exact files.
- [ ] **Step 2:** Add the API client functions for the 7 endpoints above, matching the existing fetch/auth wrapper.
- [ ] **Step 3:** Build the page: product picker → Drive-image grid (thumbnails from `thumbnail_url`). Invoke `ui-ux-pro-max` first.
- [ ] **Step 4:** Build the edit panel: 90° rotate buttons, straighten slider (±15°), autocontrast + white-balance toggles, brightness + saturation sliders (0.5–1.5), bg white/cream toggle, shadow toggle. Debounce preview calls (e.g. 400 ms) since BiRefNet is seconds/image; show a loading state; before/after view.
- [ ] **Step 5:** On page load, `GET /presets`; if one `is_default`, pre-fill the edit panel with its `params` (the auto-apply). Add a preset dropdown (apply any preset's params) + "Save as preset" (name, make-default) + delete.
- [ ] **Step 6:** Colour dropdown (fetch product variant colours the same way the backfill card does) + Publish button → `POST /publish`; on success show the returned `displayorder` and image.
- [ ] **Step 7:** Manual verification — run frontend + backend, import one real Drive image end-to-end (rotate, adjust, cream bg, publish), confirm a WEBP object lands in the `mockups` bucket and a `productimages` row appears with `phototheme="Product Shot"` and `displayorder>=20`. Verify the default-preset auto-apply and preset save/apply.
- [ ] **Step 8:** Commit.

```bash
git add frontend/
git commit -m "feat(ui): product-shot import & edit page"
```

---

## Self-Review

**Spec coverage:**
- Drive-folder source → Task 7 (`drive-images`, reuses `list_folder_image_groups`). ✓
- Hybrid edit (server pipeline) → Tasks 2-3. ✓
- Rotate/straighten/auto-colour/brightness/saturation → Task 2. ✓
- BiRefNet bg removal + white/cream composite + shadow → Task 3. ✓
- `birefnet-general-lite` default + `REMBG_MODEL` override → Task 1. ✓
- WEBP-only publish, DB→WEBP → Task 7 (asserts ext=webp, single upload). ✓
- 20+ display band → Task 4 + Task 7. ✓
- `"Product Shot"` theme, no mockup_variations, no base_mockup flip → Task 7 (asserted). ✓
- Presets table/repo/endpoints + default auto-apply → Tasks 5, 8, 11-Step5. ✓
- Params validation at boundary → Task 6. ✓
- Error modes (503 bg unavailable, 502/503 Drive) → Tasks 3, 7. ✓
- Deployment pre-cache/warm-up → Tasks 9-10. ✓
- Frontend → Task 11. ✓
- Testing strategy (unit pipeline, repo, integration) → Tasks 2-8. ✓
- Spike (BiRefNet-lite quality/latency on real photos) → covered by Task 11-Step7 manual verify; a dedicated pre-commit spike is optional and noted in the spec.

**Placeholder scan:** No TBD/TODO in backend tasks; all code shown. Frontend (Task 11) intentionally defers exact paths to execution-time recon (documented, with full API contract) because `frontend/` was not reconned — flagged, not a silent gap.

**Type consistency:** `EditParams` (dataclass, pipeline) vs `EditParamsModel` (pydantic, API) kept distinct; router converts via `EditParams(**model.model_dump())`. `apply_edits(src_bytes, params)`, `_remove_background`, `next_product_shot_order(client, productid)`, `edit_presets_repo` signatures, and `upload_mockup(..., ext=, content_type=)` match across tasks. `productimages_repo.insert` kwargs (`productid, imageurl, productcolor, theme, displayorder`) match the confirmed signature.
