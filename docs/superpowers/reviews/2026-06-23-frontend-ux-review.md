# Frontend UX/UI Review — Products, Prompts, App Shell, RefineButton

**Date:** 2026-06-23
**Reviewer basis:** `ui-ux-pro-max` rule ladder (P1 Accessibility → P10 Charts). General polish.
**Files reviewed:** `frontend/src/App.tsx`, `frontend/src/components/ProductsTab.tsx`, `frontend/src/components/PromptsTab.tsx`, `frontend/src/components/RefineButton.tsx`, `frontend/src/index.css`

## Scope note

`taste-skill` was evaluated and found **out of scope**. Its own Section 13 excludes dashboards, dense product UI, and multi-step forms — this app is all three (search → generate → review → publish admin tool). Its landing-page rules (hero discipline, eyebrow rationing, marquees, palette bans) do not apply. All findings below come from `ui-ux-pro-max` plus general engineering polish.

---

## CRITICAL — Accessibility & Touch (P1–P2)

### 1. Emoji used as structural icons
Rule: `no-emoji-icons` (hard rule). Emojis are font-dependent, inconsistent across platforms, and cannot be themed via design tokens. No icon library is installed (`package.json` has none).

Locations:
- `✨ Refine` — `RefineButton.tsx:88`
- `ⓘ` tooltip trigger — `RefineButton.tsx:54` (the newly-accessible tooltip still uses an emoji glyph)
- `✓` selection badge — `ProductsTab.tsx:755`
- `↗` "Open Drive folder ↗" — `ProductsTab.tsx:426`
- `+ Add prompt` — `PromptsTab.tsx:47`

Fix: add `lucide-react` (or Heroicons), replace these with SVG icons. `CanvasIcon` / `GoogleMark` are already hand-rolled SVG; a library unifies the icon language (one family, consistent stroke width).

### 2. Native `title=` still in use — same flaw the tooltip task fixed
Rule: `hover-vs-tap`, `aria-labels`. Native `title` is hover-only, keyboard/touch-unreachable, and delayed. Task 3 of the ProductsTab plan replaced it in `RefineButton`, but two instances remain:
- `title={img.name}` — ImageGrid, `ProductsTab.tsx:747`
- `title={v.feedback || ...}` — history filmstrip, `ProductsTab.tsx:596`

Lower stakes (name/feedback surfaced elsewhere), but inconsistent with the fix just shipped. Minimum: drop `title` on the filmstrip button (it already has a good `aria-label`).

### 3. Touch targets under 44px
Rule: `touch-target-size` (min 44×44). Base `button` is `min-height: 40px` (`index.css:216`). Selects/inputs are 44px (good). Affected:
- Sign out — `App.tsx:117`, `App.tsx:81`
- Delete — `PromptsTab.tsx:114`
- "Refine this" / "Try again" — `ProductsTab.tsx:618,622`
- "Start over" text button — `ProductsTab.tsx:648`
- `.tab` — `min-height: auto` (`index.css:329`)

Fix: bump base button to `min-height: 44px`; give tabs real vertical padding.

---

## HIGH — Typography & Color (P6)

### 4. `text-subtle` contrast risk
Rule: `color-contrast` / `contrast-readability` (4.5:1 small text). `--text-subtle: oklch(55.17%)` on white is borderline ~4.5:1, and it is applied to the smallest text — `text-xs`, `text-[10px]`, `text-[11px]` (e.g. helper text `ProductsTab.tsx:543,615,627`, row IDs). Likely fails AA at those sizes.

Fix: verify with a contrast checker; darken `--text-subtle` one step, or stop pairing it with sub-12px text.

---

## MEDIUM — Forms, Performance, Loading (P3, P8)

### 5. Some inputs lack visible labels
Rule: `input-labels` (visible label, not placeholder/aria-only). Prompt-template select (`ProductsTab.tsx:472`) and both prompt textareas use `aria-label` only. Model/Quality/Aspect selects do have visible `<span>` labels — good; make the focal prompt controls match.

### 6. Infinite-scroll list has no virtualization
Rule: `virtualize-lists` (50+ items). List appends 50/page → potentially hundreds of live DOM rows; scroll and memory degrade. Deliberately scoped out of the current plan — flag as the next perf step (e.g. `@tanstack/react-virtual`) if catalogs grow large.

### 7. Loading affordance inconsistent
Rule: `progressive-loading` (skeleton preferred for >1s). Regenerate uses a shaped skeleton (good, `ProductsTab.tsx:632`). Initial search, "Loading more…", and Drive image load use bare spinners. Consider skeleton rows/tiles for consistency.

### 8. Tooltip edge cases
- Bubble is `right:0; max-width:260px` (`index.css`) — near the left viewport edge on narrow screens it can overflow; check at 375px.
- Opened-by-focus then `onBlur` closes before a pointer can reach the bubble — fine for static text; do not add interactive content inside.

---

## LOW — polish

- Shared `msg` slot in `GenerationStage` (`ProductsTab.tsx:547`) shows video-refine errors up near the image section — minor mis-location.
- `window.confirm` for delete (`PromptsTab.tsx:89`) is acceptable for an internal tool; a styled dialog would match the rest but is not required.
- Pills convey state by color **and** text (`color-not-only` satisfied) — no change.

---

## Already strong (no change)

- Empty states throughout (no results, no images, choose-a-category).
- Inline errors with `role="alert"` / `aria-live="polite"`.
- `aria-pressed` / `aria-current` / `aria-selected` on toggles, rows, tabs.
- `loading="lazy"` on thumbnails.
- Regenerate skeleton matches final layout shape.
- Infinite scroll via `IntersectionObserver` (not a scroll listener).
- Reduced motion handled globally in `index.css` and in `.tt-bubble`.

---

## Suggested fix batch (highest impact, low risk)

1. Add `lucide-react`; swap all emoji icons (finding 1).
2. Base button `min-height: 44px`; tab padding (finding 3).
3. Remove/replace the two leftover `title=` (finding 2).
4. Darken `--text-subtle` for AA at small sizes (finding 4).

Note: `ProductsTab.tsx` is mid-plan on branch `feat/productstab-refinements`. Land as a follow-up commit on that branch or a dedicated `fix/frontend-a11y` branch.
