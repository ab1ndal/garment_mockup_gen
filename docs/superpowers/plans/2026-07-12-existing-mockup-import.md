# Existing-Mockup Import with Star Watermark Removal — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish pre-made Drive mockups into Supabase as full generations, with an optional local inpaint that removes the Gemini sparkle watermark at the bottom-right — no AI generation involved.

**Architecture:** One new pure-PIL/numpy utility (`remove_corner_star`) shared by two publish paths: the existing Backfill approve (new `remove_watermark` flag) and a new `POST /api/generate/approve-existing` endpoint driven from the Products tab. Both end in the canonical `publish.publish_image` (mockup_variations + base_mockup + productimages).

**Tech Stack:** FastAPI + Pydantic (backend), PIL + numpy (image), React/TypeScript/Vite (frontend), pytest.

**Spec:** `docs/superpowers/specs/2026-07-12-existing-mockup-import-design.md`

## Global Constraints

- No new dependencies. PIL + numpy only for image work.
- ROI constants (relative to image size): x ∈ [0.86, 0.97]·w, y ∈ [0.915, 0.98]·h — named module constants, one place.
- Watermark toggle is manual, default **off**, everywhere.
- Publish path is `publish.publish_image` — NOT the import_shots/product-shot path.
- `prompt_text="(existing mockup import)"` for the Products-tab path so rows are identifiable in `mockup_variations`.
- Payload cap: `_MAX_UPLOAD_BYTES` (25 MB, already defined in `backend/routers/generate.py:53`).
- Frontend has no unit-test infra — frontend tasks verify with `npm run build` (runs `tsc -b`).
- Backend tests: `poetry run pytest` from repo root, style matches `tests/test_backfill_api.py` (monkeypatch module attributes, TestClient with dependency overrides).
- Commit messages: imperative, ≤72-char subject, Co-Authored-By trailer per repo convention.

---

### Task 1: `remove_corner_star` utility

**Files:**
- Create: `mockup_generator/generation/watermark.py`
- Test: `tests/test_watermark.py`

**Interfaces:**
- Consumes: nothing (leaf module).
- Produces: `remove_corner_star(png_bytes: bytes) -> bytes` — decodes any PIL-readable image, repaints the bottom-right ROI with a smooth blend of the surrounding background, returns PNG (RGB) bytes. On images too small to hold the ROI, returns input unchanged. Module constants `ROI_X0, ROI_X1, ROI_Y0, ROI_Y1` (floats, relative).

Background: the Gemini sparkle in the reference sample (680×1082) is a 39×39 px white diamond ~38 px in from the right/bottom edges — bbox ≈ x ∈ [0.887, 0.944]·w, y ∈ [0.930, 0.966]·h, on flat gray ~RGB(221,221,221). The ROI is slightly generous around that. Fill = average of vertical interpolation (top ring row → bottom ring row) and horizontal interpolation (left ring col → right ring col): reconstructs a flat/gradient background exactly and erases anything inside the ROI.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_watermark.py
from io import BytesIO

import numpy as np
from PIL import Image

from mockup_generator.generation.watermark import remove_corner_star, ROI_X0, ROI_X1, ROI_Y0, ROI_Y1

W, H = 680, 1082
BG = (221, 221, 221)
# Star bbox measured on the reference sample.
STAR = (603, 1006, 642, 1045)  # x0, y0, x1, y1


def _png(img: Image.Image) -> bytes:
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _with_star() -> bytes:
    img = Image.new("RGB", (W, H), BG)
    px = img.load()
    x0, y0, x1, y1 = STAR
    cx, cy, r = (x0 + x1) // 2, (y0 + y1) // 2, (x1 - x0) // 2
    for y in range(y0, y1):
        for x in range(x0, x1):
            if abs(x - cx) + abs(y - cy) <= r:   # diamond, like the sparkle
                px[x, y] = (245, 245, 245)
    return _png(img)


def test_star_erased():
    out = remove_corner_star(_with_star())
    a = np.asarray(Image.open(BytesIO(out)).convert("RGB")).astype(int)
    x0, y0, x1, y1 = STAR
    star_region = a[y0:y1, x0:x1]
    assert np.abs(star_region - np.array(BG)).max() <= 2  # flat bg restored


def test_pixels_outside_roi_untouched():
    src = _with_star()
    out = remove_corner_star(src)
    before = np.asarray(Image.open(BytesIO(src)).convert("RGB"))
    after = np.asarray(Image.open(BytesIO(out)).convert("RGB"))
    rx0, ry0 = int(W * ROI_X0), int(H * ROI_Y0)
    rx1, ry1 = int(W * ROI_X1), int(H * ROI_Y1)
    mask = np.ones((H, W), dtype=bool)
    mask[ry0:ry1, rx0:rx1] = False
    assert (before[mask] == after[mask]).all()


def test_no_star_is_visual_noop():
    src = _png(Image.new("RGB", (W, H), BG))
    out = remove_corner_star(src)
    a = np.asarray(Image.open(BytesIO(out)).convert("RGB")).astype(int)
    assert np.abs(a - np.array(BG)).max() <= 2  # whole image still flat bg


def test_gradient_background_reconstructed():
    # Vertical gradient — the blend must follow it, not flatten it.
    grad = np.tile(np.linspace(180, 240, H).astype(np.uint8)[:, None, None], (1, W, 3))
    src = _png(Image.fromarray(grad))
    out = remove_corner_star(src)
    a = np.asarray(Image.open(BytesIO(out)).convert("RGB")).astype(int)
    assert np.abs(a - grad.astype(int)).max() <= 4


def test_tiny_image_returned_unchanged():
    src = _png(Image.new("RGB", (5, 5), BG))
    assert remove_corner_star(src) == src
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_watermark.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mockup_generator.generation.watermark'`

- [ ] **Step 3: Implement**

```python
# mockup_generator/generation/watermark.py
"""Erase the Gemini sparkle watermark from the bottom-right corner.

The sparkle sits on flat studio background at a fixed offset from the corner
(measured on a 680x1082 reference: 39x39 px, ~38 px in from each edge). The
ROI below covers it with margin; its contents are repainted as the average of
a vertical blend (row above the ROI -> row below) and a horizontal blend
(column left of the ROI -> column right), which reproduces flat or gently
graded backgrounds and erases anything printed on top. Manual toggle decides
whether this runs — there is no detection.
"""

from __future__ import annotations

from io import BytesIO

import numpy as np
from PIL import Image

# Relative ROI bounds (fraction of width/height).
ROI_X0, ROI_X1 = 0.86, 0.97
ROI_Y0, ROI_Y1 = 0.915, 0.98


def remove_corner_star(png_bytes: bytes) -> bytes:
    """Repaint the bottom-right ROI from its surrounding background.

    Returns PNG (RGB) bytes. Images too small to hold the ROI plus a 1-px
    border ring are returned unchanged.
    """
    a = np.asarray(Image.open(BytesIO(png_bytes)).convert("RGB")).astype(np.float64)
    h, w = a.shape[:2]
    x0, x1 = int(w * ROI_X0), int(w * ROI_X1)
    y0, y1 = int(h * ROI_Y0), int(h * ROI_Y1)
    if x1 - x0 < 1 or y1 - y0 < 1 or x0 < 1 or y0 < 1 or x1 >= w or y1 >= h:
        return png_bytes

    rh, rw = y1 - y0, x1 - x0
    top, bottom = a[y0 - 1, x0:x1], a[y1, x0:x1]          # (rw, 3)
    left, right = a[y0:y1, x0 - 1], a[y0:y1, x1]          # (rh, 3)
    ty = ((np.arange(rh) + 1) / (rh + 1))[:, None, None]  # 0..1 down the ROI
    tx = ((np.arange(rw) + 1) / (rw + 1))[None, :, None]  # 0..1 across the ROI
    vert = top[None, :, :] * (1 - ty) + bottom[None, :, :] * ty
    horiz = left[:, None, :] * (1 - tx) + right[:, None, :] * tx
    a[y0:y1, x0:x1] = (vert + horiz) / 2

    out = Image.fromarray(a.round().clip(0, 255).astype(np.uint8))
    buf = BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()
```

Note `test_tiny_image_returned_unchanged` requires byte-identical passthrough — the guard returns the *input bytes*, not a re-encode. For a 5×5 image: `x0 = int(5*0.86) = 4`, `x1 = int(5*0.97) = 4` → `x1 - x0 < 1` → unchanged. Good.

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_watermark.py -v`
Expected: 5 passed

- [ ] **Step 5: Manual verification against the real sample**

The reference sample (a real watermarked mockup screenshot) lives at
`/Users/abindal/.claude/image-cache/5385e36a-23b7-4f67-b1d2-9413158e527d/1.png` (session cache — if missing, ask the user for a watermarked sample). Run:

```bash
poetry run python - <<'EOF'
from mockup_generator.generation.watermark import remove_corner_star
src = open('/Users/abindal/.claude/image-cache/5385e36a-23b7-4f67-b1d2-9413158e527d/1.png','rb').read()
open('/tmp/star_removed.png','wb').write(remove_corner_star(src))
print('written /tmp/star_removed.png')
EOF
```

View `/tmp/star_removed.png` (Read tool renders it): star must be gone, no visible rectangle seam. If the star pokes out of the ROI on real full-size Drive mockups, widen the ROI constants and re-run tests.

- [ ] **Step 6: Commit**

```bash
git add mockup_generator/generation/watermark.py tests/test_watermark.py
git commit -m "feat: add corner-star watermark removal utility"
```

---

### Task 2: Backfill approve — `remove_watermark` flag

**Files:**
- Modify: `backend/schemas.py:150-155` (`BackfillApproveRequest`)
- Modify: `backend/routers/backfill.py:152-163` (`approve`) + imports at `:40`
- Test: `tests/test_backfill_api.py` (append)

**Interfaces:**
- Consumes: `watermark.remove_corner_star(png_bytes) -> bytes` from Task 1.
- Produces: `POST /api/backfill/approve` accepts optional `remove_watermark: bool` (default false). No response change.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_backfill_api.py` (fixtures `client`, helpers `_png`, and the monkeypatch style already exist in this file — reuse them):

```python
def test_approve_remove_watermark_routes_bytes_through_inpaint(client, monkeypatch):
    calls = {}
    monkeypatch.setattr(bf.items_repo, "transition", lambda db, **kw: True)
    monkeypatch.setattr(bf.drive_client, "download_file", lambda fid: b"RAW")
    monkeypatch.setattr(bf.watermark, "remove_corner_star",
                        lambda png: calls.setdefault("inpaint_in", png) or b"CLEANED")
    monkeypatch.setattr(bf.publish, "publish_image",
                        lambda db, **kw: calls.update(kw) or {"image_url": "u", "variation_id": 1})
    monkeypatch.setattr(bf.drive_client, "ensure_subfolder", lambda *a: "arch")
    monkeypatch.setattr(bf.drive_client, "move_file", lambda *a: None)

    r = client.post("/api/backfill/approve", json={
        "file_id": "f1", "productid": "BC25001", "color": "Red", "remove_watermark": True,
    })
    assert r.status_code == 200
    assert calls["inpaint_in"] == b"RAW"
    assert calls["png"] == b"CLEANED"


def test_approve_default_skips_inpaint(client, monkeypatch):
    calls = {}
    monkeypatch.setattr(bf.items_repo, "transition", lambda db, **kw: True)
    monkeypatch.setattr(bf.drive_client, "download_file", lambda fid: b"RAW")
    monkeypatch.setattr(bf.watermark, "remove_corner_star",
                        lambda png: calls.setdefault("inpaint", True) or png)
    monkeypatch.setattr(bf.publish, "publish_image",
                        lambda db, **kw: calls.update(kw) or {"image_url": "u", "variation_id": 1})
    monkeypatch.setattr(bf.drive_client, "ensure_subfolder", lambda *a: "arch")
    monkeypatch.setattr(bf.drive_client, "move_file", lambda *a: None)

    r = client.post("/api/backfill/approve", json={"file_id": "f1", "productid": "BC25001"})
    assert r.status_code == 200
    assert "inpaint" not in calls
    assert calls["png"] == b"RAW"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_backfill_api.py -v`
Expected: the two new tests FAIL — `AttributeError: module ... has no attribute 'watermark'`

- [ ] **Step 3: Implement**

In `backend/schemas.py`, add the field to `BackfillApproveRequest`:

```python
class BackfillApproveRequest(BaseModel):
    file_id: str
    productid: str
    color: str | None = None
    theme_name: str | None = None
    aspect_ratio: str | None = None
    remove_watermark: bool = False
```

In `backend/routers/backfill.py`, extend the generation import (line 40):

```python
from mockup_generator.generation import publish, watermark
```

In `approve` (line 152), inside the existing try block, between the download and `publish_image`:

```python
    try:
        png = drive_client.download_file(req.file_id)
        if req.remove_watermark:
            png = watermark.remove_corner_star(png)
        result = publish.publish_image(
            db, productid=req.productid, png=png, color=req.color,
            theme_name=req.theme_name, aspect_ratio=req.aspect_ratio,
            created_by=user.id, prompt_text=None,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_backfill_api.py tests/test_watermark.py -v`
Expected: all pass (existing approve tests must still pass — flag defaults off).

- [ ] **Step 5: Commit**

```bash
git add backend/schemas.py backend/routers/backfill.py tests/test_backfill_api.py
git commit -m "feat(backfill): optional star-watermark removal on approve"
```

---

### Task 3: `POST /api/generate/approve-existing` endpoint

**Files:**
- Modify: `backend/schemas.py` (new `ApproveExistingRequest`, place after `ApproveResponse` at `:122`)
- Modify: `backend/routers/generate.py` (new route after `approve_mockup`, ~line 440; extend the generation import at `:34`)
- Test: `tests/test_generate_api.py` (append)

**Interfaces:**
- Consumes: `watermark.remove_corner_star` (Task 1), `drive_client.download_file(file_id) -> bytes`, `publish.publish_image(...)` (both existing).
- Produces: `POST /api/generate/approve-existing`, JSON body `{productid: str, file_id: str, color?: str, theme_name?: str, aspect_ratio?: str, remove_watermark?: bool}`, response = existing `ApproveResponse` (`{status, detail, image_url, variation_id}`). Frontend Task 5 calls this.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_generate_api.py`. The file already provides the `client` fixture, the router module as `gen` (`from backend.routers import generate as gen`), and a `_png_bytes()` helper — reuse all three:

```python
def test_approve_existing_publishes_drive_image(client, monkeypatch):
    calls = {}
    monkeypatch.setattr(gen.drive_client, "download_file", lambda fid: calls.setdefault("fid", fid) or _png_bytes())
    monkeypatch.setattr(gen.publish, "publish_image",
                        lambda db, **kw: calls.update(kw) or {"image_url": "https://pub/x.webp", "variation_id": 7})

    r = client.post("/api/generate/approve-existing", json={
        "productid": "BC25001", "file_id": "drv1", "color": "Ivory",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["image_url"] == "https://pub/x.webp" and body["variation_id"] == 7
    assert calls["fid"] == "drv1"
    assert calls["productid"] == "BC25001" and calls["color"] == "Ivory"
    assert calls["prompt_text"] == "(existing mockup import)"


def test_approve_existing_remove_watermark_flag(client, monkeypatch):
    calls = {}
    monkeypatch.setattr(gen.drive_client, "download_file", lambda fid: _png_bytes())
    monkeypatch.setattr(gen.watermark, "remove_corner_star",
                        lambda png: calls.setdefault("inpainted", True) or png)
    monkeypatch.setattr(gen.publish, "publish_image",
                        lambda db, **kw: {"image_url": "u", "variation_id": 1})

    r = client.post("/api/generate/approve-existing", json={
        "productid": "BC25001", "file_id": "drv1", "remove_watermark": True,
    })
    assert r.status_code == 200
    assert calls.get("inpainted") is True


def test_approve_existing_rejects_non_image(client, monkeypatch):
    monkeypatch.setattr(gen.drive_client, "download_file", lambda fid: b"not an image")
    r = client.post("/api/generate/approve-existing",
                    json={"productid": "BC25001", "file_id": "drv1"})
    assert r.status_code == 400


def test_approve_existing_drive_failure_is_502(client, monkeypatch):
    def boom(fid):
        raise RuntimeError("drive down")
    monkeypatch.setattr(gen.drive_client, "download_file", boom)
    r = client.post("/api/generate/approve-existing",
                    json={"productid": "BC25001", "file_id": "drv1"})
    assert r.status_code == 502
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_generate_api.py -v -k approve_existing`
Expected: FAIL with 404 (route not defined) / AttributeError on `gen.watermark`.

- [ ] **Step 3: Implement**

In `backend/schemas.py` (after `ApproveResponse`):

```python
class ApproveExistingRequest(BaseModel):
    """Publish an already-made Drive mockup as a generation (no AI call)."""
    productid: str
    file_id: str
    color: str | None = None
    theme_name: str | None = None
    aspect_ratio: str | None = None
    remove_watermark: bool = False
```

In `backend/routers/generate.py`:
- extend the schemas import (`:28-31`) with `ApproveExistingRequest`
- extend the generation import (`:34`) to `from mockup_generator.generation import publish, service, video_service, watermark`
- add after `approve_mockup`:

```python
@router.post("/approve-existing", response_model=ApproveResponse)
def approve_existing(req: ApproveExistingRequest,
                     user: CurrentUser = Depends(get_current_user),
                     db: Client = Depends(get_db)):
    """Publish a pre-made mockup that already lives in the product's Drive
    folder — download, optionally erase the corner star watermark, and run the
    canonical publish path. No generation involved."""
    try:
        raw = drive_client.download_file(req.file_id)
    except DriveNotConfigured as exc:
        raise HTTPException(status_code=503, detail="Drive access is not configured on the server") from exc
    except Exception as exc:  # noqa: BLE001 - surface any Drive failure as upstream error
        raise HTTPException(status_code=502, detail=f"Could not download the Drive image: {exc}") from exc

    if len(raw) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Image is too large.")
    try:
        Image.open(BytesIO(raw)).verify()                  # cheap validity check
        png_img = Image.open(BytesIO(raw)).convert("RGB")  # reopen (verify exhausts it)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="The Drive file is not a valid image.") from exc

    buf = BytesIO()
    png_img.save(buf, format="PNG")
    png = buf.getvalue()
    if req.remove_watermark:
        png = watermark.remove_corner_star(png)

    try:
        result = publish.publish_image(
            db, productid=req.productid, png=png, color=req.color,
            theme_name=req.theme_name, aspect_ratio=req.aspect_ratio,
            created_by=user.id, prompt_text="(existing mockup import)",
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

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_generate_api.py -v`
Expected: all pass (new + existing).

- [ ] **Step 5: Run the full backend suite**

Run: `poetry run pytest`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/schemas.py backend/routers/generate.py tests/test_generate_api.py
git commit -m "feat(generate): approve-existing endpoint for pre-made Drive mockups"
```

---

### Task 4: Backfill frontend — watermark checkbox

**Files:**
- Modify: `frontend/src/api.ts:485-495` (`approveBackfill`)
- Modify: `frontend/src/components/BackfillTab.tsx:350-433` (`ReviewPanel` state + `doApprove`), `:493-522` (fields area)

**Interfaces:**
- Consumes: backend flag from Task 2.
- Produces: user-visible checkbox; no exports consumed elsewhere.

Note: this is UI work — per project CLAUDE.md, load the `ui-ux-pro-max:ui-ux-pro-max` skill before editing, and keep the existing `label.check` pattern (see `ProductShotsTab.tsx:80-88`), 44px min touch target.

- [ ] **Step 1: Extend the API client**

In `frontend/src/api.ts`, add `remove_watermark?: boolean` to `approveBackfill`'s parameter type:

```ts
export const approveBackfill = (b: {
  file_id: string;
  productid: string;
  color?: string;
  theme_name?: string;
  aspect_ratio?: string;
  remove_watermark?: boolean;
}) =>
```

(body already `JSON.stringify(b)` — no other change).

- [ ] **Step 2: Add checkbox state + wire into approve**

In `ReviewPanel` (`BackfillTab.tsx:358` area), add state next to the existing fields:

```tsx
const [removeWm, setRemoveWm] = useState(false);
```

In `doApprove` (`:382`), pass the flag:

```tsx
    approveBackfill({
      file_id: item.file_id, productid: item.productid,
      color: color || undefined, theme_name: theme, aspect_ratio: aspect,
      remove_watermark: removeWm,
    })
```

- [ ] **Step 3: Render the checkbox**

Directly after the closing `</div>` of `.review-fields` (`:511`), before the Edit-notes field:

```tsx
        <label className="check" style={{ minHeight: 44 }}>
          <input
            type="checkbox"
            checked={removeWm}
            onChange={(e) => setRemoveWm(e.target.checked)}
          />
          <span>Remove star watermark (bottom-right)</span>
        </label>
```

- [ ] **Step 4: Verify build**

Run: `cd frontend && npm run build`
Expected: `tsc -b` and vite build succeed, no type errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api.ts frontend/src/components/BackfillTab.tsx
git commit -m "feat(backfill-ui): remove-star-watermark toggle on approve"
```

---

### Task 5: Products frontend — "Use as mockup" flow

**Files:**
- Modify: `frontend/src/api.ts` (new `approveExistingMockup`, next to `approveMockup` at `:347`)
- Modify: `frontend/src/components/ProductsTab.tsx` — `GenerationStage` (`:211`), `ImageGrid` (`:843`), icon import (`:10`)

**Interfaces:**
- Consumes: `POST /api/generate/approve-existing` (Task 3), existing `ApproveResult` type, existing `colors`/`onPublished` in `GenerationStage`, `UploadIcon` from `./icons`.
- Produces: per-tile "Use as existing mockup" button + inline confirm panel.

UI work — load `ui-ux-pro-max:ui-ux-pro-max` before editing. Match existing tile-button style (`enlarge-btn`), `label.check` checkbox pattern, `btn-primary` CTA, and existing alert classes.

- [ ] **Step 1: Add the API client function**

In `frontend/src/api.ts`, after `approveMockup` (`:347`):

```ts
/** Publish a pre-made Drive mockup as a generation (no AI call). */
export const approveExistingMockup = (b: {
  productid: string;
  file_id: string;
  color?: string;
  theme_name?: string;
  aspect_ratio?: string;
  remove_watermark?: boolean;
}) =>
  apiFetch<ApproveResult>("/api/generate/approve-existing", {
    method: "POST",
    body: JSON.stringify(b),
  });
```

- [ ] **Step 2: Add a "use as mockup" affordance to ImageGrid**

`ImageGrid` (`ProductsTab.tsx:843`) gains an optional callback; when present, render a second corner button (bottom-right, mirroring the enlarge button bottom-left):

```tsx
function ImageGrid({ images, picked, onToggle, onEnlarge, onUseAsMockup }: {
  images: ProductImage[]; picked: Set<string>;
  onToggle: (id: string) => void;
  onEnlarge: (img: ProductImage) => void;
  onUseAsMockup?: (img: ProductImage) => void;
}) {
```

and inside the tile, after the enlarge button (`:874-881`):

```tsx
            {onUseAsMockup && (
              <button
                type="button"
                onClick={() => onUseAsMockup(img)}
                aria-label={`Use ${img.name} as existing mockup`}
                title="Use as existing mockup"
                className="enlarge-btn absolute bottom-1.5 right-1.5"
              >
                <UploadIcon size={15} strokeWidth={2} />
              </button>
            )}
```

Extend the icons import (`:10`): `import { ArrowUpRightIcon, CheckIcon, ExpandIcon, UploadIcon } from "./icons";`

- [ ] **Step 3: Confirm panel + publish call in GenerationStage**

State (near the other `useState` calls at `:220-228`):

```tsx
  const [useAsMockup, setUseAsMockup] = useState<ProductImage | null>(null);
  const [useWm, setUseWm] = useState(false);
  const [useColor, setUseColor] = useState("");
  const [useBusy, setUseBusy] = useState(false);
  const [useMsg, setUseMsg] = useState<{ kind: "info" | "error"; text: string } | null>(null);
```

Handler (near `publish`, `:406`):

```tsx
  const publishExisting = () => {
    if (!useAsMockup) return;
    setUseBusy(true);
    setUseMsg(null);
    approveExistingMockup({
      productid: product.productid, file_id: useAsMockup.id,
      color: useColor || undefined, remove_watermark: useWm,
    })
      .then((r) => {
        setPublishedUrl(r.image_url);
        setUseMsg({ kind: "info", text: r.detail });
        setUseAsMockup(null);
        onPublished?.(product.productid);
      })
      .catch((e: Error) => setUseMsg({ kind: "error", text: e.message.replace(/^\d+:\s*/, "") }))
      .finally(() => setUseBusy(false));
  };
```

Reset the panel when the product changes — extend the existing product-change effect (`:281-288`) with:

```tsx
    setUseAsMockup(null); setUseWm(false); setUseColor(""); setUseMsg(null);
```

Pass the callback to both `ImageGrid` usages (`:487`, `:495`): add
`onUseAsMockup={(im) => { setUseAsMockup(im); setUseColor(color); setUseMsg(null); }}`

Render the confirm panel at the end of the Source-images section (after the closing `</div>` of the image list, `:499`, still inside the `<section>`):

```tsx
        {useAsMockup && (
          <div className="card mt-4 p-4">
            <p className="section-label mt-0!">Use as existing mockup</p>
            <div className="mt-2 flex flex-wrap items-center gap-4">
              <img src={useAsMockup.thumbnail_url} alt={useAsMockup.name}
                   className="h-16 w-16 rounded-lg border border-line object-cover" />
              <label className="field mb-0!">
                <span className="text-xs font-semibold text-subtle">Color</span>
                <select value={useColor} onChange={(e) => setUseColor(e.target.value)}>
                  <option value="">— select —</option>
                  {colors.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              </label>
              <label className="check" style={{ minHeight: 44 }}>
                <input type="checkbox" checked={useWm}
                       onChange={(e) => setUseWm(e.target.checked)} />
                <span>Remove star watermark</span>
              </label>
              <div className="ml-auto flex items-center gap-2">
                <button type="button" disabled={useBusy} onClick={() => setUseAsMockup(null)}>
                  Cancel
                </button>
                <button type="button" className="btn-primary"
                        disabled={useBusy || !useColor} onClick={publishExisting}>
                  {useBusy && <span className="spinner" aria-hidden />}
                  {useBusy ? "Publishing…" : "Publish as mockup"}
                </button>
              </div>
            </div>
            <p className="mt-2 text-sm text-muted">
              Publishes this Drive image directly — no AI generation.
            </p>
            {useMsg && (
              <p className={`alert ${useMsg.kind === "error" ? "alert-error" : "alert-info"}`} role="alert">
                {useMsg.text}
              </p>
            )}
          </div>
        )}
        {!useAsMockup && useMsg && (
          <p className={`alert mt-3 ${useMsg.kind === "error" ? "alert-error" : "alert-info"}`} role="alert">
            {useMsg.text}
          </p>
        )}
```

(Color required — publish button disabled without it, matching Backfill's rule.)

- [ ] **Step 4: Verify build**

Run: `cd frontend && npm run build`
Expected: clean tsc + vite build.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api.ts frontend/src/components/ProductsTab.tsx
git commit -m "feat(products-ui): publish existing Drive image as mockup"
```

---

### Task 6: End-to-end verification

**Files:** none (verification only).

- [ ] **Step 1: Full backend suite**

Run: `poetry run pytest`
Expected: all pass.

- [ ] **Step 2: Drive the app** (use the `verify`/`run` skill conventions)

Start backend + frontend dev servers, then with Playwright CLI (per user rules: `npx playwright`, not MCP):
1. Products tab → pick a product with Drive images → tile shows the new bottom-right Use button → panel opens → select color + check "Remove star watermark" → Publish → success message, product marked published.
2. Backfill tab → open a pending item → new checkbox visible → approve with it checked → success.
3. Confirm in Supabase (`mockup_variations` latest row has `prompt_text='(existing mockup import)'` for the Products path).

- [ ] **Step 3: Report**

Summarize what shipped, test output, and any ROI-constant adjustments made against real Drive files.
