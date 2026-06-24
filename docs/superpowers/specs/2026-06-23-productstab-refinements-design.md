# ProductsTab + RefineButton Refinements — Design

**Date:** 2026-06-23
**Scope:** Frontend only. Backend (`products_repo.list_products`, `/api/products`) already supports `limit`/`offset` and the `base_mockup` pill source needs no change.

## Goal

Three independent UI refinements on the Products screen:

1. **Live "Generated" pill** — when a mockup is approved and published, the product's list pill flips from "Pending" to "Generated" immediately, without a re-search. Rename the existing "Done" label to "Generated".
2. **Infinite scroll** — the post-search product list loads more rows automatically as the user scrolls near the bottom, using the backend's existing `limit`/`offset` pagination.
3. **Accessible Refine tooltip** — replace the unreliable native `title` tooltip on the RefineButton info icon with a styled tooltip that works on hover, keyboard focus, and tap.

## Files

- `frontend/src/components/ProductsTab.tsx` — refinements 1 and 2.
- `frontend/src/components/RefineButton.tsx` — refinement 3.
- `frontend/src/index.css` — tooltip styles for refinement 3.

No backend, schema, or API-signature changes. `listProducts` already accepts `{ limit?, offset? }`.

---

## Refinement 1 — Live "Generated" pill

### Current behavior
The sidebar list renders each product's pill from `p.base_mockup`:

```tsx
<span className={p.base_mockup ? "pill pill-done" : "pill pill-pending"}>
  {p.base_mockup ? "Done" : "Pending"}
</span>
```

Publishing happens inside the child `GenerationStage` (`publish()` → `approveMockup()` → sets `publishedUrl`). The parent's `rows` array is never updated, so a freshly published product keeps showing "Pending" until a new search.

### Design
- **Label:** change the published text from `"Done"` to `"Generated"`. Keep the `pill-done` CSS class (no style change).
- **Live flip:** add an `onPublished?: (productid: string) => void` prop to `GenerationStage`. Call it in `publish()`'s success `.then`, after `approveMockup` resolves, alongside `setPublishedUrl`.
- **Parent state update:** `ProductsTab` defines `markPublished(id)`:
  - `setRows(rows => rows.map(r => r.productid === id ? { ...r, base_mockup: r.base_mockup || PLACEHOLDER_TRUTHY } : r))`
  - `setSelected(s => s && s.productid === id ? { ...s, base_mockup: s.base_mockup || PLACEHOLDER_TRUTHY } : s)`
  - `base_mockup` is the field that drives the pill. Set it to the published URL when available (returned by `approveMockup`), else any truthy value, so the pill flips to "Generated".
- Wire `<GenerationStage ... onPublished={markPublished} />` at the call site.

### Edge case
A live-flipped row stays visible in the current result set even under the default "Pending only" filter. A subsequent re-search with "Pending only" will drop it — expected and acceptable (it is no longer pending).

---

## Refinement 2 — Infinite scroll

### Current behavior
`search()` calls `listProducts` with no `limit`/`offset` and replaces `rows`. Backend returns the first 50 (default limit). No further rows are reachable.

### Design
- **Constants:** `const PAGE_SIZE = 50;`
- **State:** add `offset` (number), `hasMore` (boolean), `loadingMore` (boolean) to `ProductsTab`. Keep current `searching` for the initial search.
- **Filters snapshot:** factor the param-building (category / id / id_start+id_end / pending) into a helper `buildParams(extra)` so both the initial search and page loads use identical filters. The initial `search()` reads the live filter inputs; page loads must reuse the **same** filter values that produced the current list — store them (e.g. a `activeParams` ref or state) at search time so editing the form mid-scroll does not corrupt pagination.
- **Initial search:** on submit, set `offset = 0`, fetch `buildParams({ limit: PAGE_SIZE, offset: 0 })`, **replace** `rows`, set `hasMore = batch.length === PAGE_SIZE`, set `searched = true`.
- **Load next page:** `loadMore()` guards on `hasMore && !loadingMore && !searching`. Sets `loadingMore`, fetches `buildParams({ limit: PAGE_SIZE, offset: nextOffset })` where `nextOffset = offset + PAGE_SIZE`, **appends** to `rows`, updates `offset = nextOffset`, recomputes `hasMore = batch.length === PAGE_SIZE`, clears `loadingMore`. On error, surface via existing `err` alert and leave prior rows intact (do not advance `offset`).
- **Trigger:** a sentinel `<div ref={sentinelRef} />` rendered at the bottom of the list (only when `rows.length > 0`). A `useEffect` attaches an `IntersectionObserver` to the sentinel; when it intersects and `hasMore && !loadingMore`, call `loadMore()`. Re-create/cleanup the observer in the effect's teardown. Depend the effect on `hasMore`, `loadingMore`, `offset`, and `activeParams` so the latest closure is used.
- **Loading affordance:** show a small spinner row (reuse `.spinner`) near the sentinel while `loadingMore`.

### Notes
- No virtualization (YAGNI; lists are modest and capped by backend `limit` per page).
- `prefers-reduced-motion` is irrelevant here (no animation introduced).

---

## Refinement 3 — Accessible Refine tooltip

### Current behavior
The info affordance is a non-interactive `<span title={HINTS[kind]}>ⓘ`. Native `title` is hover-only, has a browser-controlled (~1s) delay, is not keyboard- or touch-reachable, and renders nothing in some setups — the reported symptom.

### Design (ui-ux-pro-max: `hover-vs-tap`, `tooltip-keyboard`, `focus-states`, `touch-target-size`)
Replace the `<span title>` with a small self-contained accessible tooltip:

- **Trigger:** a real `<button type="button">` for the ⓘ. Focusable; `aria-label="How to write a refine instruction"`; `aria-expanded={open}`; `aria-describedby={tooltipId}` (a stable id, e.g. derived from `kind`). Padding gives it a ≥44×44px hit area while the glyph stays small.
- **Tooltip element:** a `<div role="tooltip" id={tooltipId}>` containing `HINTS[kind]`, rendered when `open`.
- **Open/close (covers all input modes):**
  - hover: `onMouseEnter` / `onMouseLeave` on the wrapper → open/close.
  - keyboard: `onFocus` / `onBlur` on the button → open/close.
  - touch/click: `onClick` toggles `open`.
  - Escape key closes; outside-click (document listener while open) closes.
- **Styling (`index.css`, new `.tt-*` classes, design tokens):** `--surface` background, `1px solid var(--line)`, `--shadow-md`, `--ink` text, `--sp-2`/`--sp-3` padding, `border-radius` per existing scale, `max-width: 260px`, small font, positioned absolutely above-or-below the button (wrapper `position: relative`), `z-index` above the form. 150ms opacity/transform fade-in gated behind `@media (prefers-reduced-motion: reduce)` (no transition when reduced motion is requested).
- **Behavior unchanged:** `HINTS` copy, the ✨ Refine button, and the refine call all stay as-is. Only the info affordance changes.

---

## Testing

This is presentational/interaction frontend work; verification is the build gate plus manual smoke.

- **Build gate:** `cd frontend && npm run build` — clean `tsc -b` + Vite build (covers the new props, state, and component types).
- **Manual smoke:**
  1. Search products → list shows; scroll to bottom → next page auto-appends; stops when `hasMore` is false.
  2. Select a pending product, generate, **Approve and Publish** → its sidebar pill flips to "Generated" immediately (no re-search).
  3. Refine info icon: hover shows styled tooltip; Tab to it and it shows on focus; tap on touch toggles it; Escape / click-away dismisses.
  4. Reduced-motion enabled → tooltip appears without animation.

## Out of scope

- Numbered pagination / "Load more" button (chose infinite scroll).
- List virtualization.
- Any backend change.
