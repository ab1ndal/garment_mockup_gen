# ProductsTab + RefineButton Refinements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Three frontend refinements on the Products screen — a list pill that flips "Pending" → "Generated" live on publish, infinite scroll over the existing backend pagination, and an accessible Refine tooltip that replaces the broken native `title`.

**Architecture:** Pure frontend, additive. Refinements 1 and 2 live in `ProductsTab.tsx` (parent holds `rows`/`selected`/pagination state; child `GenerationStage` reports a publish up via a new callback prop). Refinement 3 rewrites the info affordance inside `RefineButton.tsx` plus new tooltip styles in `index.css`. Backend, schemas, and API signatures are untouched — `listProducts` already accepts `limit`/`offset`, and the pill derives from `Product.base_mockup`.

**Tech Stack:** Vite + React + TypeScript. Frontend gate: `cd frontend && npm run build` (`tsc -b` + Vite). No backend changes, no new dependency.

**Design:** `docs/superpowers/specs/2026-06-23-productstab-refinements-design.md`

## Global Constraints

- Frontend only. No backend, schema, migration, API-signature, or dependency change.
- Verification is the build gate: `cd frontend && npm run build` must pass `tsc -b` cleanly plus a successful Vite build. There is no frontend unit-test harness in this repo; do not invent one.
- Reuse existing CSS design tokens (`--surface`, `--surface-2`, `--line`, `--ink`, `--shadow-md`, `--sp-1..8`) and existing classes (`.spinner`, `.pill`, `.pill-done`, `.pill-pending`). Do not hardcode hex.
- `Product.base_mockup` is the field that drives the list pill; it is `string | null` (the published image URL when set).
- Follow ui-ux-pro-max interaction rules for the tooltip: works on hover **and** keyboard focus **and** tap; trigger is a real focusable `<button>` with a ≥44×44px hit area and aria attributes; respect `prefers-reduced-motion`.
- Caveman mode is chat-only; code, comments, and commit messages are normal prose.

---

### Task 1: Live "Generated" pill on publish

**Files:**
- Modify: `frontend/src/components/ProductsTab.tsx`

**Interfaces:**
- Consumes: existing `Product` type (`{ productid: string; base_mockup: string | null; ... }`), existing `rows`/`setRows`, `selected`/`setSelected` state in `ProductsTab`, existing `approveMockup` response (`{ image_url: string; detail: string }`) already handled in `GenerationStage.publish`.
- Produces: a new optional prop `onPublished?: (productid: string) => void` on `GenerationStage`, invoked after a successful publish.

- [ ] **Step 1: Rename the pill label "Done" → "Generated"**

In `frontend/src/components/ProductsTab.tsx`, the sidebar list pill (~line 110) currently reads:

```tsx
                    <span className={p.base_mockup ? "pill pill-done" : "pill pill-pending"}>
                      {p.base_mockup ? "Done" : "Pending"}
                    </span>
```

Change the published label text only (keep the `pill-done` class):

```tsx
                    <span className={p.base_mockup ? "pill pill-done" : "pill pill-pending"}>
                      {p.base_mockup ? "Generated" : "Pending"}
                    </span>
```

- [ ] **Step 2: Add `markPublished` in `ProductsTab`**

In `ProductsTab` (the top-level component, alongside the `search` function ~line 34), add a handler that flips a product's `base_mockup` to truthy in both `rows` and `selected`. `base_mockup` is what the pill reads, so any truthy value makes it render "Generated":

```tsx
  const markPublished = (id: string) => {
    setRows((rs) => rs.map((r) => (r.productid === id && !r.base_mockup ? { ...r, base_mockup: "published" } : r)));
    setSelected((s) => (s && s.productid === id && !s.base_mockup ? { ...s, base_mockup: "published" } : s));
  };
```

- [ ] **Step 3: Pass `onPublished` to `GenerationStage`**

At the `GenerationStage` call site (~line 127):

```tsx
      {selected
        ? <GenerationStage key={selected.productid} product={selected} />
```

add the prop:

```tsx
      {selected
        ? <GenerationStage key={selected.productid} product={selected} onPublished={markPublished} />
```

- [ ] **Step 4: Accept and call `onPublished` in `GenerationStage`**

Update the `GenerationStage` signature (~line 148) from:

```tsx
function GenerationStage({ product }: { product: Product }) {
```

to:

```tsx
function GenerationStage({ product, onPublished }: { product: Product; onPublished?: (productid: string) => void }) {
```

Then in `publish()` (~line 326), call `onPublished` in the success `.then`, right after `setPublishedUrl`:

```tsx
    approveMockup(fd)
      .then((r) => { setPublishedUrl(r.image_url); setMsg({ kind: "info", text: r.detail }); onPublished?.(product.productid); })
      .catch((e: Error) => setMsg({ kind: "error", text: e.message.replace(/^\d+:\s*/, "") }))
      .finally(() => setPublishing(false));
```

- [ ] **Step 5: Build to verify it compiles**

Run: `cd frontend && npm run build`
Expected: PASS — `tsc -b` clean, Vite build succeeds (new prop typed, no errors).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/ProductsTab.tsx
git commit -m "feat(ui): flip product pill to Generated live on publish

Renames the Done label to Generated and adds an onPublished callback from
GenerationStage so the sidebar pill updates immediately after Approve and
Publish, without requiring a re-search."
```

---

### Task 2: Infinite scroll on the product list

**Files:**
- Modify: `frontend/src/components/ProductsTab.tsx`

**Interfaces:**
- Consumes: existing `listProducts(params: { category?, id?, id_start?, id_end?, pending?, limit?, offset? }) => Promise<Product[]>` (api.ts — already supports `limit`/`offset`), existing filter state (`category`, `idSingle`, `idEnd`, `pending`), existing `rows`/`setRows`, `searching`/`setSearching`, `searched`/`setSearched`, `err`/`setErr`.
- Produces: paginated, appending list behavior driven by an `IntersectionObserver` sentinel. No new exported interface.

- [ ] **Step 1: Add pagination state and a stable params builder**

In `ProductsTab`, add the constant and state. Place the constant above the component, and the state with the other `useState` declarations (~line 30). Also add a `useRef` for the sentinel and one for the active (frozen) filter params. Ensure `useRef` is imported (the file already imports it: `import { useEffect, useRef, useState } from "react";`).

```tsx
const PAGE_SIZE = 50;
```

```tsx
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const activeParams = useRef<Parameters<typeof listProducts>[0]>({ pending: true });
```

- [ ] **Step 2: Build the params once, reuse for search and paging**

Replace the existing `search` function (~line 34) with a version that snapshots the filters into `activeParams`, resets pagination, and fetches the first page:

```tsx
  const buildParams = (extra: { limit: number; offset: number }) => {
    const params: Parameters<typeof listProducts>[0] = { pending, ...extra };
    if (category) params.category = category;
    if (idSingle && idEnd) { params.id_start = idSingle; params.id_end = idEnd; }
    else if (idSingle) params.id = idSingle;
    return params;
  };

  const search = () => {
    setErr(null);
    setSearching(true);
    setOffset(0);
    const params = buildParams({ limit: PAGE_SIZE, offset: 0 });
    activeParams.current = params;
    listProducts(params)
      .then((r) => { setRows(r); setSearched(true); setHasMore(r.length === PAGE_SIZE); })
      .catch((e) => setErr(e.message))
      .finally(() => setSearching(false));
  };
```

- [ ] **Step 3: Add `loadMore` to append the next page**

Add directly below `search`. It reuses the frozen `activeParams` filters (so editing the form mid-scroll does not corrupt pagination), advances `offset`, and **appends**:

```tsx
  const loadMore = () => {
    if (!hasMore || loadingMore || searching) return;
    setLoadingMore(true);
    const nextOffset = offset + PAGE_SIZE;
    listProducts({ ...activeParams.current, limit: PAGE_SIZE, offset: nextOffset })
      .then((r) => {
        setRows((prev) => [...prev, ...r]);
        setOffset(nextOffset);
        setHasMore(r.length === PAGE_SIZE);
      })
      .catch((e) => setErr(e.message))
      .finally(() => setLoadingMore(false));
  };
```

- [ ] **Step 4: Observe the sentinel**

Add a `useEffect` (after the existing `useEffect` for categories ~line 32) that attaches an `IntersectionObserver` to the sentinel and calls `loadMore` when it scrolls into view. Re-run when the relevant values change so the observer's callback closes over fresh state:

```tsx
  useEffect(() => {
    const node = sentinelRef.current;
    if (!node || !hasMore) return;
    const io = new IntersectionObserver((entries) => {
      if (entries[0].isIntersecting) loadMore();
    });
    io.observe(node);
    return () => io.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasMore, loadingMore, offset]);
```

- [ ] **Step 5: Render the sentinel and a loading row**

In the list block, the list currently ends at the closing `</ul>` (~line 117). Add the sentinel and a loading affordance immediately after the `</ul>`, inside the same `rows.length > 0` branch. Locate:

```tsx
            })}
          </ul>
        ) : (
```

and change to:

```tsx
            })}
          </ul>
        ) : (
```

then place the sentinel just before the closing of the truthy branch by wrapping the `<ul>` and sentinel in a fragment. Concretely, the truthy branch becomes:

```tsx
        {rows.length > 0 ? (
          <>
            <ul className="card max-h-[70vh] divide-y divide-line overflow-auto p-0">
              {rows.map((p) => {
                const isSel = selected?.productid === p.productid;
                return (
                  <li key={p.productid}>
                    <button
                      type="button"
                      onClick={() => setSelected(p)}
                      aria-current={isSel}
                      className={`flex w-full items-center gap-3 border-0 border-l-2 justify-start! rounded-none! px-4 py-3 text-left min-h-0!
                        ${isSel
                          ? "border-l-accent bg-accent-soft"
                          : "border-l-transparent bg-transparent hover:bg-surface-2"}`}
                    >
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-medium text-ink">{p.name}</span>
                        <span className="mono block text-xs text-subtle">
                          {p.productid}{showRowCategory && (p.category_name ?? p.categoryid) ? ` · ${p.category_name ?? p.categoryid}` : ""}
                        </span>
                      </span>
                      <span className={p.base_mockup ? "pill pill-done" : "pill pill-pending"}>
                        {p.base_mockup ? "Generated" : "Pending"}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
            {hasMore && <div ref={sentinelRef} aria-hidden style={{ height: 1 }} />}
            {loadingMore && (
              <p className="empty flex items-center justify-center gap-2 py-3">
                <span className="spinner" aria-hidden /> Loading more…
              </p>
            )}
          </>
        ) : (
```

(Note: this replaces the existing single-`<ul>` truthy branch with a fragment containing the same `<ul>` plus the sentinel and loading row. The `<li>`/pill markup is identical to Task 1's result — "Generated" label retained.)

- [ ] **Step 6: Build to verify it compiles**

Run: `cd frontend && npm run build`
Expected: PASS — `tsc -b` clean, Vite build succeeds.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/ProductsTab.tsx
git commit -m "feat(ui): infinite scroll on the product list

Pages the search results via the backend's existing limit/offset using an
IntersectionObserver sentinel. Filters are frozen at search time so editing
the form mid-scroll does not corrupt pagination; results append until a short
page signals the end."
```

---

### Task 3: Accessible Refine tooltip

**Files:**
- Modify: `frontend/src/components/RefineButton.tsx`
- Modify: `frontend/src/index.css`

**Interfaces:**
- Consumes: existing `HINTS: Record<"image" | "video", string>` and `kind` prop in `RefineButton`. No prop signature change.
- Produces: an accessible tooltip (hover + focus + tap) replacing the native `title` span. New `.tt-*` CSS classes.

- [ ] **Step 1: Add tooltip styles to `index.css`**

Append to `frontend/src/index.css` (uses existing tokens; new classes are namespaced `tt-`):

```css
/* Accessible tooltip (RefineButton info hint) */
.tt-wrap {
  position: relative;
  display: inline-flex;
  align-items: center;
}
.tt-trigger {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 44px;
  min-height: 44px;
  padding: 0;
  background: transparent;
  border: 0;
  color: var(--ink);
  cursor: help;
  line-height: 1;
}
.tt-bubble {
  position: absolute;
  top: calc(100% + var(--sp-1));
  right: 0;
  z-index: 50;
  width: max-content;
  max-width: 260px;
  padding: var(--sp-2) var(--sp-3);
  background: var(--surface);
  color: var(--ink);
  border: 1px solid var(--line);
  border-radius: 8px;
  box-shadow: var(--shadow-md);
  font-size: 0.8125rem;
  line-height: 1.4;
  text-align: left;
  white-space: normal;
}
@media (prefers-reduced-motion: no-preference) {
  .tt-bubble { animation: tt-fade 150ms ease-out; }
  @keyframes tt-fade {
    from { opacity: 0; transform: translateY(-2px); }
    to   { opacity: 1; transform: translateY(0); }
  }
}
```

- [ ] **Step 2: Rewrite `RefineButton.tsx` to use the accessible tooltip**

Replace the entire contents of `frontend/src/components/RefineButton.tsx` with:

```tsx
import { useEffect, useRef, useState } from "react";
import { refinePrompt } from "../api";

const HINTS: Record<"image" | "video", string> = {
  image:
    "Describe what you want — garment, mood, any must-keep details. " +
    "e.g. 'Festive Diwali saree, warm mood — match the provided pattern details.'",
  video:
    "Describe the clip — motion, camera, mood, must-keep details. " +
    "e.g. 'Slow elegant twirl, soft festive light, fabric flowing — keep the print exact.'",
};

function InfoTooltip({ kind }: { kind: "image" | "video" }) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const tipId = `refine-hint-${kind}`;

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div
      className="tt-wrap"
      ref={wrapRef}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        className="tt-trigger"
        aria-label="How to write a refine instruction"
        aria-expanded={open}
        aria-describedby={open ? tipId : undefined}
        onClick={() => setOpen((v) => !v)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
      >
        ⓘ
      </button>
      {open && (
        <div id={tipId} role="tooltip" className="tt-bubble">
          {HINTS[kind]}
        </div>
      )}
    </div>
  );
}

export default function RefineButton({
  kind, instruction, categoryid, onRefined, onError,
}: {
  kind: "image" | "video";
  instruction: string;
  categoryid?: string;
  onRefined: (text: string) => void;
  onError: (msg: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const empty = !instruction.trim();

  const run = () => {
    setBusy(true);
    refinePrompt(instruction, categoryid, kind)
      .then((r) => onRefined(r.refined))
      .catch((e) => onError(e.message))
      .finally(() => setBusy(false));
  };

  return (
    <div className="toolbar" style={{ alignItems: "center", gap: "var(--sp-2)" }}>
      <button className="btn-primary" onClick={run} disabled={busy || empty}>
        {busy && <span className="spinner" aria-hidden />}
        {busy ? "Refining…" : "✨ Refine"}
      </button>
      <InfoTooltip kind={kind} />
    </div>
  );
}
```

- [ ] **Step 3: Build to verify it compiles**

Run: `cd frontend && npm run build`
Expected: PASS — `tsc -b` clean, Vite build succeeds.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/RefineButton.tsx frontend/src/index.css
git commit -m "fix(ui): accessible Refine tooltip replacing native title

The info hint was a native title attribute — hover-only, delayed, and
keyboard/touch-unreachable (often showing nothing). Replaces it with a real
focusable button + role=tooltip bubble that opens on hover, focus, and tap,
dismisses on Escape or outside click, and respects reduced motion."
```

---

### Task 4: End-to-end verification

**Files:** none (verification only).

- [ ] **Step 1: Frontend build clean**

Run: `cd frontend && npm run build`
Expected: PASS — clean `tsc -b` + Vite build covering all three refinements.

- [ ] **Step 2: Backend suite still green (sanity — should be untouched)**

Run: `poetry run pytest -q`
Expected: PASS — no backend files changed; full suite green.

- [ ] **Step 3: Live smoke**

Run: `poetry run uvicorn backend.main:app --reload`, then `cd frontend && npm run dev`. In the Products screen:
- Search products → list shows. Scroll to the bottom → the next page auto-appends with a brief "Loading more…" row; appending stops when a short page is returned.
- Select a pending product, generate, click **Approve and Publish** → its sidebar pill flips from "Pending" to "Generated" immediately (no re-search).
- Refine info icon (✨ Refine area, both Prompts tab and generate boxes): hover → styled tooltip appears; Tab to it → tooltip shows on focus; on a touch device / via click → tooltip toggles; press Escape or click away → it dismisses.
- Enable OS reduced-motion → the tooltip appears without the fade animation.

Expected: all behaviors hold; no console errors.

---

## Self-Review

**Spec coverage:**
- Refinement 1 (live "Generated" pill, rename Done, onPublished callback, parent `markPublished` updating `rows` + `selected`) → Task 1. ✓
- Refinement 2 (infinite scroll, `PAGE_SIZE`, frozen `activeParams`, replace-on-search / append-on-page, `hasMore` from short page, `IntersectionObserver` sentinel, loading row, no virtualization) → Task 2. ✓
- Refinement 3 (real focusable `<button>` trigger, ≥44px hit area, aria-label/expanded/describedby, `role="tooltip"`, hover+focus+tap open, Escape/outside-click close, token-based styling, `prefers-reduced-motion`) → Task 3. ✓
- Testing (build gate + backend sanity + manual smoke) → Task 4. ✓
- Out of scope (numbered pagination, virtualization, backend change) honored: no task touches them. ✓

**Placeholder scan:** No TBD/TODO. Every code step shows full code; every command shows expected output. The single `eslint-disable` line is intentional (the observer effect deliberately omits `loadMore` from deps and closes over fresh state via the listed deps), not a placeholder. ✓

**Type consistency:** `onPublished?: (productid: string) => void` is identical at the `GenerationStage` definition (Task 1 Step 4), the call site (Task 1 Step 3), and the `publish` invocation (Task 1 Step 4). `markPublished(id: string)` matches the prop type. `buildParams({ limit, offset })` returns `Parameters<typeof listProducts>[0]`, consumed by both `search` and `loadMore`. `activeParams` ref typed `Parameters<typeof listProducts>[0]`. `base_mockup` treated as `string | null` (set to `"published"`), matching the `Product` type and the pill's truthiness check. `InfoTooltip({ kind })` consumes the same `"image" | "video"` union as `RefineButton`. ✓
