# Phase 4 Feedback → Regenerate Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an in-session iterate-to-perfection loop to mockup generation — review a preview against source images, write optional feedback, and regenerate by refining the current image or trying again fresh.

**Architecture:** Backend gets one optional `refine_image_b64` field on the existing `POST /api/generate/image`; when present, the decoded image is appended as an extra reference. Frontend `GenerationStage` keeps an in-session array of variations (base64 in React state) with a history filmstrip, feedback box, and Refine/Try-again buttons. Nothing new is persisted — Approve/publish is unchanged.

**Tech Stack:** FastAPI + Pydantic + Pillow (backend), pytest (backend tests), React + TypeScript + Vite + Tailwind (frontend).

## Global Constraints

- Python 3.10 (`>=3.10,<3.11`). Run backend tests with `poetry run pytest`.
- No new dependencies, no DB schema change, no Storage/migration, no new endpoint.
- `_MAX_REFS = 14` — hard cap on total references passed to Gemini.
- Backend stays feedback-agnostic: the feedback note is folded into `prompt` by the frontend; no new feedback parameter.
- Frontend has no unit-test harness — gate frontend work on `npm run build` (from `frontend/`).
- Existing `/image` and `/approve` behavior and tests must stay green (no regression).
- Caveman mode is for chat only; code, comments, and commit messages are normal prose.

---

### Task 1: Backend — `refine_image_b64` on `/api/generate/image`

**Files:**
- Modify: `backend/schemas.py` (add field to `GenerateRequest`, ~line 62-69)
- Modify: `backend/routers/generate.py` (add logging + `_decode_b64_image` helper; rewrite `generate_image`, lines 139-181)
- Test: `tests/test_generate_api.py` (add 5 tests)

**Interfaces:**
- Consumes: `gen.products_repo.get_product`, `gen.drive_client.download_file`, `gen.drive_client.extract_folder_id`, `gen.service.generate_mockup_bytes(images, prompt, **kw)` — all already mocked by `_wire_happy` in the test file.
- Produces: `POST /api/generate/image` accepts optional `refine_image_b64: str`. Response shape unchanged (`GeneratePreview{status, detail, image_b64}`). Frontend Task 2 sends this field.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_generate_api.py` (the helpers `_wire_happy`, `_png_bytes`, `client` already exist at the top of the file):

```python
import base64

def _refine_b64() -> str:
    return base64.b64encode(_png_bytes()).decode("ascii")


def test_generate_image_refine_appends_prior_output(client, monkeypatch):
    calls = {}
    _wire_happy(monkeypatch, calls=calls)
    r = client.post("/api/generate/image", json={
        "productid": "BC25001", "prompt": "p", "image_ids": ["f1", "f2"],
        "refine_image_b64": _refine_b64(),
    })
    assert r.status_code == 200
    assert calls["downloaded"] == ["f1", "f2"]
    # 2 downloaded sources + 1 refine reference
    assert calls["gen"]["n_images"] == 3


def test_generate_image_refine_only_no_sources(client, monkeypatch):
    calls = {}
    _wire_happy(monkeypatch, calls=calls)
    r = client.post("/api/generate/image", json={
        "productid": "BC25001", "prompt": "p", "image_ids": [],
        "refine_image_b64": _refine_b64(),
    })
    assert r.status_code == 200
    assert calls.get("downloaded") in (None, [])     # no Drive download needed
    assert calls["gen"]["n_images"] == 1             # the refine image alone


def test_generate_image_requires_source_or_refine_400(client, monkeypatch):
    _wire_happy(monkeypatch, calls={})
    r = client.post("/api/generate/image",
                    json={"productid": "BC25001", "prompt": "p", "image_ids": []})
    assert r.status_code == 400


def test_generate_image_bad_refine_400(client, monkeypatch):
    _wire_happy(monkeypatch, calls={})
    r = client.post("/api/generate/image", json={
        "productid": "BC25001", "prompt": "p", "image_ids": [],
        "refine_image_b64": "!!!not-base64!!!",
    })
    assert r.status_code == 400


def test_generate_image_refine_dropped_when_sources_at_cap(client, monkeypatch):
    calls = {}
    _wire_happy(monkeypatch, calls=calls)
    many = [f"id{i}" for i in range(15)]
    r = client.post("/api/generate/image", json={
        "productid": "BC25001", "prompt": "p", "image_ids": many,
        "refine_image_b64": _refine_b64(),
    })
    assert r.status_code == 200
    assert len(calls["downloaded"]) == 14            # sources capped at _MAX_REFS
    assert calls["gen"]["n_images"] == 14            # refine dropped, not 15
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/test_generate_api.py -k "refine or source_or_refine" -v`
Expected: FAIL — `refine_image_b64` is rejected/ignored by the schema, so the refine tests get wrong `n_images` and the bad-refine test returns 200 instead of 400.

- [ ] **Step 3: Add the schema field**

In `backend/schemas.py`, add to `GenerateRequest` (after `color`, ~line 69):

```python
class GenerateRequest(BaseModel):
    productid: str
    prompt: str
    image_ids: list[str] = []
    model: str | None = None
    resolution: str | None = None
    aspect_ratio: str | None = None
    color: str | None = None
    refine_image_b64: str | None = None   # prior output, included as an extra reference on refine
```

- [ ] **Step 4: Add logging + decode helper to the router**

In `backend/routers/generate.py`, add `import logging` next to the other stdlib imports (top of file, after `import base64`), and after the `router = APIRouter(...)` line add:

```python
log = logging.getLogger(__name__)


def _decode_b64_image(b64: str) -> Image.Image:
    """Decode a base64 PNG/JPEG into a PIL image, or raise 400 on bad input."""
    try:
        raw = base64.b64decode(b64, validate=True)
        return Image.open(BytesIO(raw)).convert("RGB")
    except Exception as exc:  # noqa: BLE001 - any decode/parse failure is a bad request
        raise HTTPException(status_code=400, detail="Invalid refine image.") from exc
```

- [ ] **Step 5: Rewrite `generate_image`**

Replace the body of `generate_image` (lines 139-181) with:

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

    if not req.image_ids and not req.refine_image_b64:
        raise HTTPException(status_code=400, detail="Select at least one source image.")

    # Decode the refine reference first so bad input fails fast as a 400.
    refine_img = _decode_b64_image(req.refine_image_b64) if req.refine_image_b64 else None

    product = products_repo.get_product(db, req.productid)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    images: list[Image.Image] = []
    ref_ids = req.image_ids[:_MAX_REFS]
    if ref_ids:
        folder_id = drive_client.extract_folder_id(product.producturl)
        if not folder_id:
            raise HTTPException(status_code=400, detail="Product has no linked Drive folder")
        try:
            images = [Image.open(BytesIO(drive_client.download_file(fid))) for fid in ref_ids]
        except DriveNotConfigured as exc:
            raise HTTPException(status_code=503, detail="Drive access is not configured on the server") from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Could not download Drive images: {exc}") from exc

    # Sources own the reference budget; the refine image is appended only if room.
    if refine_img is not None:
        if len(images) < _MAX_REFS:
            images.append(refine_img)
        else:
            log.warning("refine image dropped: %d source refs already at cap %d",
                        len(images), _MAX_REFS)

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

- [ ] **Step 6: Run the full backend suite**

Run: `poetry run pytest -q`
Expected: PASS — all prior tests stay green and the 5 new tests pass.

- [ ] **Step 7: Commit**

```bash
git add backend/schemas.py backend/routers/generate.py tests/test_generate_api.py
git commit -m "feat(generate): optional refine_image_b64 reference on /image

Appends the prior generated output as an extra Gemini reference for
feedback-driven regeneration. Sources keep priority within the 14-ref cap;
refine is dropped if sources fill it. Refine-only (no sources) is valid."
```

---

### Task 2: Frontend — in-session variation history + review panel

**Files:**
- Modify: `frontend/src/api.ts` (add `refine_image_b64?` to the `generateImage` request type)
- Modify: `frontend/src/components/ProductsTab.tsx` (`GenerationStage`: replace `previewB64` with a variations array; add regenerate handlers; replace the Preview JSX block with the review panel)

**Interfaces:**
- Consumes: `generateImage(req)` from `api.ts` — now accepts `refine_image_b64?: string` (Task 1 backend honors it). `approveMockup(fd)` unchanged.
- Produces: a self-contained review UX; no exported interface for later tasks.

- [ ] **Step 1: Add the field to the api.ts request type**

In `frontend/src/api.ts`, find the request type/argument object for `generateImage` (the one carrying `productid`, `prompt`, `image_ids`, `color`, `model`, `resolution`, `aspect_ratio`) and add an optional field:

```ts
  refine_image_b64?: string;
```

If `generateImage` takes an inline-typed param, add the same optional property there. Do not change the function body — it already serializes the whole object to JSON.

- [ ] **Step 2: Replace preview state with variation history**

In `GenerationStage` (`frontend/src/components/ProductsTab.tsx`), define a `Variation` type above the component and replace the `previewB64` state. Remove:

```ts
  const [previewB64, setPreviewB64] = useState<string | null>(null);
```

Add (alongside the other `useState` calls, keep `publishedUrl`/`publishing`):

```ts
type Variation = {
  b64: string;
  promptUsed: string;     // full prompt sent (base + folded feedback)
  feedback: string;       // note that produced this variation ("" for the first)
  mode: "fresh" | "refine";
};
```

```ts
  const [variations, setVariations] = useState<Variation[]>([]);
  const [activeIdx, setActiveIdx] = useState(0);
  const [feedback, setFeedback] = useState("");
  const active = variations[activeIdx] ?? null;
```

In the two `useEffect` blocks that reset preview state on product/image change, replace every `setPreviewB64(null)` with:

```ts
      setVariations([]); setActiveIdx(0); setFeedback("");
```

- [ ] **Step 3: Build the prompt helper and the generate/regenerate handlers**

Add a small helper inside `GenerationStage` (above `run`):

```ts
  const composePrompt = () =>
    feedback.trim() ? `${promptText}\n\nRevision note: ${feedback.trim()}` : promptText;

  const pushVariation = (b64: string, promptUsed: string, mode: "fresh" | "refine", note: string) => {
    setVariations((prev) => {
      const next = [...prev, { b64, promptUsed, feedback: note, mode }];
      setActiveIdx(next.length - 1);
      return next;
    });
    setFeedback("");
  };
```

Rewrite the image branch of `run` so the first generation pushes `variations[0]`. Replace the `if (kind === "image") { ... }` block with:

```ts
    if (kind === "image") {
      setPublishedUrl(null);
      const promptUsed = composePrompt();
      const note = feedback.trim();
      generateImage({
        productid: product.productid, prompt: promptUsed, image_ids,
        color: color || undefined,
        model: model || undefined, resolution: resolution || undefined,
        aspect_ratio: aspect || undefined,
      })
        .then((r) => { setMsg({ kind: "info", text: r.detail }); pushVariation(r.image_b64, promptUsed, "fresh", note); })
        .catch((e: Error) => setMsg({ kind: "error", text: e.message.replace(/^\d+:\s*/, "") }))
        .finally(() => setBusy(null));
    } else {
```

Add a dedicated regenerate handler (used by the panel's two buttons):

```ts
  const regenerate = (refine: boolean) => {
    if (refine && !active) return;
    setBusy("image");
    setMsg(null);
    setPublishedUrl(null);
    const promptUsed = composePrompt();
    const note = feedback.trim();
    generateImage({
      productid: product.productid, prompt: promptUsed, image_ids: [...picked],
      color: color || undefined,
      model: model || undefined, resolution: resolution || undefined,
      aspect_ratio: aspect || undefined,
      refine_image_b64: refine && active ? active.b64 : undefined,
    })
      .then((r) => { setMsg({ kind: "info", text: r.detail }); pushVariation(r.image_b64, promptUsed, refine ? "refine" : "fresh", note); })
      .catch((e: Error) => setMsg({ kind: "error", text: e.message.replace(/^\d+:\s*/, "") }))
      .finally(() => setBusy(null));
  };
```

- [ ] **Step 4: Point approve/download at the active variation**

Replace `approveGenerated` and `downloadPreview` so they read `active`:

```ts
  const approveGenerated = async () => {
    if (!active) return;
    const blob = await (await fetch(`data:image/png;base64,${active.b64}`)).blob();
    publish(blob, "generated");
  };

  const downloadPreview = () => {
    if (!active) return;
    const a = document.createElement("a");
    a.href = `data:image/png;base64,${active.b64}`;
    a.download = `${product.productid}_${color ? color.replace(/\s+/g, "-") : "mockup"}.png`;
    a.click();
  };
```

(The `publish` helper and `onCorrectedFile` are unchanged — `onCorrectedFile` still uploads the chosen file directly.)

- [ ] **Step 5: Replace the Preview JSX with the review panel**

Replace the entire `{previewB64 && ( ... )}` section (the "Preview — review before publishing" block) with:

```tsx
      {/* Review & iterate — in-session variation history */}
      {active && (
        <section className="mt-5">
          <div className="flex items-center justify-between">
            <p className="section-label mt-0!">
              Review · <span className="tabular-nums">{activeIdx + 1} of {variations.length}</span>
            </p>
            <span className={`pill ${active.mode === "refine" ? "pill-done" : "pill-pending"}`}>
              {active.mode === "refine" ? "refined" : "fresh"}
            </span>
          </div>

          {/* Side-by-side: picked sources vs the active variation */}
          <div className="mt-2 grid gap-4 sm:grid-cols-[160px_1fr]">
            <div className="flex flex-row gap-2 overflow-x-auto sm:flex-col">
              {[...imgs.loose, ...imgs.groups.flatMap((g) => g.images)]
                .filter((im) => picked.has(im.id))
                .map((im) => (
                  <img key={im.id} src={im.thumbnail_url} alt={`Source ${im.name}`}
                       className="h-16 w-16 shrink-0 rounded-md border border-line object-cover sm:h-auto sm:w-full" />
                ))}
            </div>
            <img
              src={`data:image/png;base64,${active.b64}`}
              alt={`Variation ${activeIdx + 1}`}
              className="max-w-full rounded-lg border border-line"
            />
          </div>

          {/* History filmstrip */}
          {variations.length > 1 && (
            <div className="mt-3 flex gap-2 overflow-x-auto pb-1">
              {variations.map((v, i) => (
                <button
                  key={i}
                  type="button"
                  onClick={() => setActiveIdx(i)}
                  aria-pressed={i === activeIdx}
                  aria-label={`View variation ${i + 1}${v.feedback ? ` — note: ${v.feedback}` : ""}`}
                  title={v.feedback || (v.mode === "refine" ? "refined" : "fresh")}
                  className={`relative aspect-square h-16 w-16 shrink-0 overflow-hidden rounded-md! p-0! transition
                    ${i === activeIdx ? "border-accent! ring-2 ring-accent/30" : "border-line! hover:border-line-strong!"}`}
                >
                  <img src={`data:image/png;base64,${v.b64}`} alt={`Variation ${i + 1}`}
                       className="h-full w-full object-cover" />
                  <span className="absolute bottom-0.5 right-0.5 rounded bg-black/60 px-1 text-[10px] font-bold text-white">
                    {i + 1}
                  </span>
                </button>
              ))}
            </div>
          )}

          {/* Feedback + regenerate */}
          <div className="field mt-4">
            <label htmlFor="fb">Feedback for next version</label>
            <textarea id="fb" value={feedback} onChange={(e) => setFeedback(e.target.value)} rows={3}
                      placeholder="e.g. make the sleeves longer, warmer background…" />
            <p className="mt-1 text-xs text-subtle">Leave empty to regenerate unchanged.</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button className="btn" onClick={() => regenerate(true)}
                    disabled={busy !== null || publishing || !active}>
              {busy === "image" && <span className="spinner" aria-hidden />} Refine this
            </button>
            <button className="btn" onClick={() => regenerate(false)}
                    disabled={busy !== null || publishing || pickedCount === 0}>
              Try again
            </button>
          </div>
          <p className="mt-1 text-xs text-subtle">
            <strong>Refine this</strong> edits the current image; <strong>Try again</strong> regenerates from the source images.
          </p>

          {/* Loading skeleton while a regenerate is in flight */}
          {busy === "image" && (
            <div className="mt-3 aspect-square w-full max-w-md animate-pulse rounded-lg bg-surface-2" aria-hidden />
          )}

          {/* Publish — the single primary action */}
          <div className="mt-5 border-t border-line pt-4">
            <div className="flex flex-wrap items-center gap-2">
              <button className="btn-primary" onClick={approveGenerated} disabled={publishing || busy !== null}>
                {publishing && <span className="spinner" aria-hidden />}
                {publishing ? "Publishing…" : "Approve & publish"}
              </button>
              <button className="btn" onClick={downloadPreview} disabled={publishing || busy !== null}>Download</button>
              <label className="btn cursor-pointer">
                Upload corrected
                <input type="file" accept="image/*" className="hidden" onChange={onCorrectedFile} />
              </label>
              <button className="ml-auto text-sm text-subtle hover:text-ink"
                      onClick={() => { setVariations([]); setActiveIdx(0); setFeedback(""); setMsg(null); }}
                      disabled={publishing || busy !== null}>
                Start over
              </button>
            </div>
            {publishedUrl && (
              <p className="alert alert-info mt-3" role="status">
                Published: <a href={publishedUrl} target="_blank" rel="noreferrer">{publishedUrl}</a>
              </p>
            )}
          </div>
        </section>
      )}
```

- [ ] **Step 6: Build to typecheck**

Run: `cd frontend && npm run build`
Expected: PASS — TypeScript compiles, Vite build succeeds. Fix any type errors (e.g. an unused `active` guard) before continuing.

- [ ] **Step 7: Manual smoke (document result in the commit / PR)**

With backend + frontend running and a real product: Generate Image → a variation appears in the review panel beside the picked sources → type a note, click **Refine this** (the model sees the prior image) → click **Try again** (fresh from sources) → click an earlier thumbnail in the filmstrip to switch the active variation → **Approve & publish** the active one → confirm the published URL renders.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/api.ts frontend/src/components/ProductsTab.tsx
git commit -m "feat(ui): in-session variation history + feedback-regenerate loop

Review panel shows source images beside the active variation, a history
filmstrip of in-session attempts, a feedback box, and Refine-this vs
Try-again regenerate buttons. Approve/Download/Upload-corrected act on the
active variation. Nothing persisted until Approve."
```

---

## Self-Review

**Spec coverage:**
- Backend `refine_image_b64` field + append-as-reference + 14-ref cap with refine-dropped → Task 1 (Steps 3-5) + tests (Step 1). ✓
- Source-or-refine requirement; refine-only valid; bad-refine 400 → Task 1 tests + router logic. ✓
- In-session `variations[]` + `activeIdx` + `feedback`, push-on-regen, feedback cleared on success only → Task 2 Steps 2-3. ✓
- Per-regen Refine/Try-again toggle (refine includes prior output) → Task 2 Step 3 (`regenerate(refine)`) + Step 5 buttons. ✓
- Side-by-side sources vs active, history filmstrip with active ring + text counter, single primary CTA, loading skeleton, visible feedback label, a11y alt/aria, Start over → Task 2 Step 5. ✓
- `api.ts` optional field → Task 2 Step 1. ✓
- Persist on Approve only / `/approve` unchanged → approve path untouched (Task 2 Step 4 only changes the source of bytes). ✓
- Error handling: regen failure preserves variations + feedback (no state reset in `.catch`) → Task 2 Step 3. ✓
- Frontend gated on `npm run build`; backend full suite green → Task 1 Step 6, Task 2 Step 6. ✓

**Placeholder scan:** No TBD/TODO; every code step shows complete code. ✓

**Type consistency:** `Variation` fields (`b64`, `promptUsed`, `feedback`, `mode`) used consistently across Steps 2-5. `active = variations[activeIdx] ?? null` guards all reads. `regenerate(refine: boolean)`, `composePrompt()`, `pushVariation(...)` signatures match their call sites. Backend `refine_image_b64` name matches schema field and the `api.ts` property. ✓
