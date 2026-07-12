# Design: Drive Product-Shot Import & Edit Pipeline

**Date:** 2026-07-12
**Status:** Approved design — pending spec review
**Author:** Claude (brainstorming session with @abindal)

## Problem

Beyond AI-generated model mockups, we sometimes need to publish **product-supplied
photos** straight from a product's Google Drive folder — colour variations of a
garment, folded/flat-lay shots, etc. These need light editing before they look
good on the storefront: rotation, colour correction, and a clean studio
background (Amazon-style white, or a softer cream). Today the only path to
Storage is the Gemini generation/backfill flow. We want a **no-Gemini** import
path so these shots can be cleaned up and published cheaply and consistently.

## Goals

- Import images directly from a product's existing Drive folder (no new folder
  convention).
- Edit server-side with a small, deterministic, params-driven pipeline: rotate,
  straighten, auto colour, manual brightness/saturation, background removal +
  white/cream composite, optional soft drop-shadow.
- Publish to the `mockups` bucket as **PNG (archival) + WEBP**, with the DB
  pointing at the WEBP URL (matches the existing publish behaviour).
- Assign `displayorder` in a **fixed 20+ band** so future model mockups (1–19)
  always sort ahead of product shots on the storefront.
- **Save/apply edit presets** for consistent looks, with one default preset that
  auto-applies when an image is opened.
- Zero Gemini calls.

## Non-Goals (v1)

- No worklist / batch triage UI (unlike backfill). Import is product-scoped and
  stateless.
- No multi-image batch publish (one image at a time).
- No cutout caching between requests (noted as a later optimisation).
- No arbitrary-angle rotation beyond ±15° straighten (plus 90° quarter turns).
- No Gemini touch-up of any kind.
- No manual mask editing / brush tools.

## Key Decisions (from brainstorming)

| Decision | Choice | Rationale |
|---|---|---|
| Edit location | **Hybrid** — heavy CV server-side, light manual controls in browser | Best cutout quality + user control, moderate UI |
| BG removal engine | **rembg + BiRefNet** (MIT) | No Gemini; ML segmentation robust on any background |
| BiRefNet variant | **`birefnet-general-lite` default**, `birefnet-general` opt-in | Lite ~214 MB vs general ~928 MB; far better for CPU HF Space |
| Source | **Product's own Drive folder** (`producturl`) | Reuses `list_folder_image_groups`; ties to product |
| Order band | **Fixed 20+, independent count** | Reserves 1–19 for future model mockups so they display first |
| Theme label | **`"Product Shot"`** (fixed) | Distinguishes imports from model mockups |
| Presets | **Global, DB-backed, one default auto-applies** | Brand-wide consistency |

## Architecture

### New backend router — `backend/routers/import_shots.py` (prefix `/api/import`)

Stateless, product-scoped. Endpoints:

| Method + path | Job |
|---|---|
| `GET /products/{productid}/drive-images` | List the product's Drive folder images (reuse `drive_client.list_folder_image_groups` + `thumbnails_for`) |
| `POST /preview` | `{file_id, params}` → download original → `apply_edits` → return `data:image/png;base64,…` (no upload) |
| `POST /publish` | `{productid, file_id, color, params}` → **re-run `apply_edits` from params** → PNG+WEBP upload → insert `productimages` row in 20+ band |
| `GET /presets` | List all presets + which is default |
| `POST /presets` | `{name, params, is_default?}` → save named preset |
| `PUT /presets/{preset_id}/default` | Mark default (unsets others) |
| `DELETE /presets/{preset_id}` | Delete a preset |

Auth: `Depends(get_current_user)`, same as other routers.

### New pure module — `mockup_generator/generation/edit_pipeline.py`

One public function:

```python
def apply_edits(src_bytes: bytes, params: EditParams) -> bytes:
    """Apply the deterministic edit pipeline; return PNG bytes (RGB)."""
```

Pure, deterministic, no I/O — unit-testable with synthetic PNGs. The rembg
session is **lazy-loaded once at module scope** and reused across calls (model
load is expensive; never per-call).

### Edit params — the contract (serialised to JSON; also the preset payload)

```python
@dataclass
class EditParams:
    rotate_quarter: int   = 0      # 0|1|2|3  → 0/90/180/270° (clockwise)
    straighten_deg: float = 0.0    # ±15, fine slider
    autocontrast:   bool  = True
    white_balance:  bool  = False  # gray-world
    brightness:     float = 1.0    # ~0.5–1.5, 1.0 = unchanged
    saturation:     float = 1.0    # ~0.5–1.5, 1.0 = unchanged
    bg:             str    = "white"  # "white" #FFFFFF | "cream" #FAF7F0
    shadow:         bool  = False  # soft drop-shadow
```

Validated at the boundary (FastAPI/Pydantic): `rotate_quarter ∈ {0,1,2,3}`,
`straighten_deg ∈ [-15, 15]`, `brightness/saturation ∈ [0.5, 1.5]`,
`bg ∈ {"white","cream"}`.

### Pipeline order (corrected per research — colour ops on RGB before alpha appears)

1. **Load + EXIF normalise** — `ImageOps.exif_transpose` first, so all downstream
   sees upright pixels.
2. **Quarter-rotate** — `Image.transpose(ROTATE_90/180/270)` (lossless; `transpose`
   not `rotate`). Map `rotate_quarter` clockwise → `ROTATE_270/180/90`.
3. **Colour/tonal ops on RGB** (while still a clean opaque photo):
   - Gray-world white balance (if `white_balance`) — numpy, clip to [0,255].
   - `ImageOps.autocontrast(cutoff=1, preserve_tone=True)` if `autocontrast`
     (`preserve_tone` keeps fabric colour faithful — no per-channel hue shift).
   - `ImageEnhance.Brightness` then `ImageEnhance.Color` (brightness, saturation).
4. **Straighten** — `rotate(straighten_deg, expand=True, resample=BICUBIC,
   fillcolor=(0,0,0,0))` on RGBA; transparent corner gaps (removed by the cutout,
   so no fringe halo).
5. **BiRefNet cutout** — `remove(img, session=session, post_process_mask=True)`
   → RGBA. `post_process_mask` tightens apparel edges; `alpha_matting` left OFF by
   default (slow on CPU, can hurt crisp edges).
6. **Composite** onto solid bg — `Image.alpha_composite` over white `#FFFFFF` or
   cream `#FAF7F0`, → RGB.
7. **Optional drop-shadow** — offset the alpha silhouette, `GaussianBlur`, paint
   semi-transparent black under the foreground, then composite. Pad canvas so blur
   isn't clipped.

Output: RGB PNG bytes.

### Storage / DB reuse (targeted refactor)

Extract the PNG+WEBP dual-upload (currently inline in
`mockup_generator/generation/publish.py`) into a shared helper:

```python
def upload_png_and_webp(productid: str, png: bytes, key: str) -> str:
    """Upload PNG (archival) + WEBP; return the WEBP public URL."""
```

Both `publish_image` (existing) and the new import path call it → no duplication,
consistent WEBP behaviour.

**Import publish differs from `publish_image`:**
- Writes **only** `productimages` (no `mockup_variations` audit row — not a
  generated variation).
- Does **not** flip `mockups.base_mockup` (these aren't base model mockups).
- `productcolor` = user-picked colour (dropdown from `variants_repo.list_colors`).
- `phototheme` = `"Product Shot"`.
- `displayorder` = `productimages_repo.next_product_shot_order(db, productid)`.

### New repo function — `productimages_repo.next_product_shot_order`

```python
def next_product_shot_order(db, productid) -> int:
    """Next order in the 20+ band: max(displayorder ≥ 20) + 1, else 20."""
```

### Presets — new table + repo

**Migration** (existing Inventory-Management Supabase):

```sql
create table edit_presets (
    preset_id   bigint generated always as identity primary key,
    name        text not null unique,
    params      jsonb not null,
    is_default  boolean not null default false,
    created_by  uuid,
    created_at  timestamptz not null default now()
);
-- backstop: at most one default
create unique index edit_presets_one_default
    on edit_presets (is_default) where is_default;
```

**Repo** `mockup_generator/db/edit_presets_repo.py`: `list_all`, `insert`,
`set_default` (unset prior default + set new in one logical op so exactly one
stays true), `get_default`, `delete`.

## Data Flow

1. User opens a product → `GET /drive-images` → grid of Drive thumbnails.
2. Frontend loads presets (`GET /presets`); if a **default** exists, its params
   pre-fill the edit panel (the "auto-apply").
3. User picks an image, adjusts params → each change → `POST /preview` → live
   before/after (debounced; see perf).
4. User clicks publish → `POST /publish {productid, file_id, color, params}` →
   backend **re-runs `apply_edits` from the same params** (never uploads preview
   bytes — deterministic, no client-injected bytes) → PNG+WEBP upload →
   `productimages` insert in the 20+ band.
5. "Save as preset" → names current params, optional "make default".

## Frontend

New page/section (React/Vite, follow existing patterns + `ui-ux-pro-max` rules):
- Product picker → Drive-image grid.
- Edit panel: rotate buttons (90°), straighten slider (±15°), auto-contrast +
  white-balance toggles, brightness + saturation sliders, bg white/cream toggle,
  shadow toggle. Before/after preview.
- Preset dropdown (apply) + "Save as preset" (name, make-default) + manage.
- Colour dropdown (variant colours) + publish button.

## Dependencies

Poetry (Python 3.10 constraint → **must pin**):

```toml
rembg = {version = "2.0.69", extras = ["cpu"]}   # last version supporting py3.10
```

- Latest rembg (2.0.76) requires Python ≥3.11; 2.0.69 is the last 3.10-compatible
  release and includes BiRefNet sessions. (Alternative: bump project to 3.11 for
  newer rembg — larger change, out of scope here.)
- `rembg[cpu]` pulls `onnxruntime` (not in base deps). Also pulls numpy,
  opencv-python-headless, pillow, pymatting, scipy, scikit-image.

### Deployment (HF Space backend)

- Default model **`birefnet-general-lite`** (~214 MB) — comfortable on the Space's
  RAM; general (~928 MB, ~2–3.5 GB RAM at inference) is opt-in via a setting.
- **Pre-cache the model in the Space build** so first request isn't a ~200 MB
  cold download:
  ```dockerfile
  ENV U2NET_HOME=/home/user/.u2net
  RUN python -c "from rembg import new_session; new_session('birefnet-general-lite')"
  ```
  (HF Spaces run as uid 1000 — `U2NET_HOME` must be writable by that user.)
- Reuse one module-global session across requests.

## Error Handling & Failure Modes

- **rembg not installed / model load fails** → 503 "background removal
  unavailable". Fail loud with a clear message (bg is core; don't silently skip).
- **Drive download fails** → 502 (same mapping as backfill via `DriveNotConfigured`
  / generic).
- **Preview latency** — BiRefNet-lite is seconds/image on CPU. Frontend debounces
  preview requests; preview endpoint is stateless so drops/retries are safe.
  Consider a generous timeout; do not block at scale (note for later: job queue).
- **PNG uploaded but WEBP or DB insert fails** → same orphan semantics as existing
  `publish_image` (no new defensive code — matches current behaviour).
- **Concurrent imports, same product** → `next_product_shot_order` reads max ≥20
  then +1; two parallel publishes could collide on order. Backfill has the same
  accepted race; `displayorder` is not a unique key. Accepted, documented.
- **Preset default invariant** — enforced app-side in `set_default`; partial unique
  index is a DB backstop.

## Testing

- **Unit — `edit_pipeline.apply_edits`** (synthetic PNGs, no network):
  quarter-rotate correctness, straighten expands canvas, autocontrast on/off,
  white-balance shifts channel means, brightness/saturation factors, bg white vs
  cream (assert corner pixels), shadow adds a blurred alpha region. rembg mocked
  (or a fake session returning a known mask) so tests don't need the model.
- **Unit — `next_product_shot_order`**: empty→20; existing 20,21→22; mockups at
  1–5 ignored→20.
- **Unit — `edit_presets_repo`**: `set_default` clears prior default; `get_default`
  returns it / None when empty.
- **Integration** (mock `drive_client` + `storage_client`): publish writes exactly
  one `productimages` row, **no** `mockup_variations`, **no** `base_mockup` flip,
  `displayorder ≥ 20`, DB URL is the WEBP.
- **Integration — presets**: save → list → mark-default → apply round-trip.
- **Skip e2e** (no live Drive/Supabase in tests).

## Recommended Spike (before full commit)

Validate **BiRefNet-lite cutout quality + latency on real Bindal's product
photos** (colour variants + folded shots). This is the only medium-confidence
piece. If lite edges are inadequate on sheer/fringe fabric, fall back to general
(accept the RAM/latency cost) or enable `alpha_matting` selectively.

## Confidence

HIGH overall. Edit pipeline, Drive/Storage/DB integration, and presets are
low-risk reuse or standard verified APIs. MEDIUM only on BiRefNet cutout
quality/latency on CPU — mitigated by the lite-model default, pre-caching,
manual controls, and the spike above. Licenses (rembg MIT, BiRefNet MIT) are
clear for commercial use.

## References (research 2026-07-12)

- rembg — https://github.com/danielgatis/rembg (source at tag `v2.0.69`), https://pypi.org/pypi/rembg/json
- BiRefNet model + license (MIT) — https://github.com/ZhengPeng7/BiRefNet, https://huggingface.co/ZhengPeng7/BiRefNet
- Pillow (11.3.0 installed) — Image.transpose/rotate, ImageOps.autocontrast/exif_transpose, ImageEnhance:
  https://pillow.readthedocs.io/en/stable/reference/
- Existing analogs in-repo: `backend/routers/backfill.py`, `mockup_generator/generation/publish.py`,
  `mockup_generator/integrations/{drive_client,storage_client}.py`.
