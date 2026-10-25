# Product descriptions in Backfill review

**Date:** 2026-10-24
**Status:** Draft, pending owner approval

## Intent

New products go live through the Backfill tab (products whose photos are in
Google Drive but are not yet live on the storefront). Today their storefront
description (`products.description`) is written separately, if at all. The
owner wants to see and fix a product's description at the moment its images are
approved, with an AI draft made on demand from the same photos, so a product
goes live with reviewed copy.

## Ownership split (owner decision, 2026-10-24, via retail-inventory session)

- **mockup-generator (this feature):** descriptions for new products as they go
  live through Backfill, each reviewed by the owner before it is written.
- **retail-inventory batch** (`retail-inventory/scripts/describe/`): the ~787
  products already live.
- Both use the **same prompt and format**: `retail-inventory/scripts/describe/prompt.md`
  and `schema.json`. `prompt.md` is the source of truth for format rules; the
  "Description format" section of `tasks/description-revamp-plan.md` is out of
  date (still says `Includes:` and allows stitching status).
- A marker column keeps the two from overwriting each other: retail's batch will
  skip rows marked `ai_reviewed` or `manual` when it starts writing (today it
  writes nothing to the DB). Retail's admin edit dialog
  (`src/admin/components/ProductEditDialog.js`) does **not** set `manual` yet;
  that change is retail-inventory's, requested by message, not part of this work.
  The save guard below does not depend on it.

## Goals

1. In the Backfill review panel, show the product's current description from the
   DB **automatically** when the panel opens (no AI call).
2. A **"Draft description"** button runs the AI draft **only on click**.
3. The reviewer can accept the draft, edit it, or keep the current text, and
   saves it to `products.description` with an explicit save action.
4. The reviewer can redraft with a short note ("has mirror work on sleeves") and
   choose which images feed the draft.

## Non-goals

- Tags (occasion, work, cut…). They wait for retail-inventory's `tag_vocab` /
  `product_tags` tables and the owner's vocab review. The model's schema returns
  tag fields; this feature ignores them and stores nothing but the description.
- Name, fabric, colour, size: never generated or written. They come from existing
  catalogue fields.
- Batch review, Quick Generate and Import Shots screens. The panel is built as a
  reusable component so they can adopt it later, but only Backfill uses it now.
- Bulk/auto drafting. No draft is ever made without a click.
- SEO fields (the storefront derives them from the description).

## Anti-goals (a failure even if it works)

- Silently overwriting a description: a hand-written one, one retail's batch
  reviewed, or one another reviewer just saved.
- Blocking or slowing image approval. A failed or slow draft must never stop the
  Approve button.
- A description that breaks the owner's format (repeats colour/size/price/code,
  mentions stitching, guesses details, uses markdown).

## Data model

Migration written in **retail-inventory** as `schema/migration_<date>_description_source.sql`
(that repo owns the `products` schema; never edit existing `schema/*.sql`; fetch
live schema first via `get_schema_info` RPC per retail-inventory `CLAUDE.md`).
Expand-only, safe against both running apps:

```sql
alter table public.products
  add column description_source text
    check (description_source in ('manual', 'ai', 'ai_reviewed')),
  add column description_reviewed_at timestamptz;
```

- Nullable, no default. Null means "unknown / legacy".
- No anon grant change: the storefront does not read these columns.
- Owner approved (2026-10-24): this session writes and applies it, before the
  code that uses it is merged (merge = deploy).
- This feature only ever writes `ai_reviewed`. (`ai` = drafted but not reviewed,
  used by retail's batch if it ever stores unreviewed drafts; never written here.)

## Prompt

- Vendor copies of `prompt.md` and `schema.json` into
  `mockup_generator/prompts/describe/`, each with a header line naming the
  source path and the retail-inventory commit they were copied from.
- Port `build_prompt.js` (a ~20-line template filler) to Python. It substitutes:
  - `{DATA}`: name, category, fabric, distinct variant colours, distinct sizes
    (from `productsizecolors`);
  - `{TEXT}`: current description or `(none)`;
  - `{TEXT_SOURCE}`: "written earlier by an AI from photos; it may be wrong, so
    trust the photos over it" when the text contains `**` or is longer than 200
    chars, else "written by the shop; facts here are true unless a photo clearly
    contradicts them";
  - `{OWNER_NOTE}`: the reviewer's redraft note in the same wrapper text, or empty.
- Pilot fully approved 20/20 (retail-inventory PR #62, 2026-10-24); copy
  `prompt.md` from that commit or later. `tasks/description-pilot/approved.json`
  holds the 20 owner-approved outputs: use them as style references and as a
  manual quality check of the Claude port (draft a few of the same products and
  compare) before shipping.
- Later prompt rounds are still possible. Each round means
  re-copying both files. A test compares the vendored copies with
  `../retail-inventory/scripts/describe/` when that checkout exists (skipped
  otherwise), so drift is caught locally.
- *Rejected:* a shared Supabase prompt row both repos read. It needs a change to
  retail's working pipeline mid-review plus a table; a vendored copy is smaller.

## Model and inputs

- **Claude Opus 5.5** (`claude-opus-5-5`) via the official `anthropic` Python SDK
  (new dependency, MIT, Anthropic-maintained), key in `ANTHROPIC_API_KEY` (`.env`
  locally, HF Space secret in prod). Owner approved "connect an API key", Claude
  recommended over OpenAI (Claude's call; see below). Model name in
  `settings.description_model` so it can be changed without code edits.
- Structured output: `output_config.format` with `schema.json`, effort `medium`,
  adaptive thinking (default). Check `stop_reason` (`refusal`, `max_tokens`)
  before reading content; treat either as a failed draft. Let the SDK retry
  429/5xx (`max_retries`), timeout ~90s.
- Style references: the 20 owner-approved pilot descriptions
  (`retail-inventory/tasks/description-pilot/approved.json`), vendored with the
  prompt and added to the request as examples of the approved style (shape and
  detail level only; the prompt says not to copy their facts).
- Images: up to 3 Drive **originals** from the product's folder (the same files
  the Backfill sources endpoint lists), and optionally the card's **generated
  mockup**. Downscale to ≤1568px long edge, JPEG, before sending. Label them in
  the request as `orig_*` / `mockup_*` because the prompt depends on those names.
- Source toggles in the panel: originals (default on), mockup (default on).
  At least one must be on.
- Rough cost: ~10k input + ~2k output tokens per draft ≈ $0.08 (≈ ₹7).
- Watch HF memory (lessons: Render 512MB budget; HF is now the backend). Decode
  and resize one image at a time.
- *Why Claude over OpenAI:* the pilot's first pass was OpenAI Codex, but every
  flagged product (conflicts, uncertain details: the cases where a wrong guess
  hurts) was redone by Claude Sonnet and that output is what the owner approved.
  One Claude pass replaces the two-pass setup. OpenAI (key already in `.env`) is
  the fallback if Claude quality disappoints in the check below.
- **Quality check before the UI ships:** draft 5 of the 20 pilot products through
  the new service and show them beside the approved text.

## API (new router or added to `backend/routers/backfill.py`, builder's call)

All plain `def` routes (lessons: no sync work in `async def`).

- `GET /api/backfill/description/{productid}` →
  `{description, description_source, description_reviewed_at}`.
- `POST /api/backfill/{file_id}/description/draft` with
  `{productid, use_originals, use_mockup, note?}` →
  `{description, includes, uncertain[], conflicts[]}`. Writes nothing.
  `file_id` identifies the Backfill card; its Drive file is the mockup.
- **Save happens inside `POST /api/backfill/approve`** (plan-stage refinement of
  the same behaviour): the request gains optional `description`,
  `expected_description`, `expected_reviewed_at`. After the image publishes, the
  route attempts the conditional save: sets `description`,
  `description_source='ai_reviewed'`, `description_reviewed_at=now()`, only if
  both `description` and `description_reviewed_at` still equal the `expected_*`
  values (null = empty / never reviewed). Comparing the text too catches an admin
  edit that doesn't touch the marker columns. The response carries
  `description_warning` (null on success); a failed or conflicting save never
  fails the approval. One request means a closed tab can't lose the save, and no
  separate save endpoint is needed.
- Validation at the boundary: description trimmed, plain text, ≤ 600 chars hard
  cap (format target is 15–60 words; the cap only stops garbage). Empty is
  allowed (the format allows an empty description).

## UI (Backfill `ReviewPanel`)

Invoke the `ui-ux-pro-max` skill before building (project CLAUDE.md).

- New **Description** section under the image controls. When the panel opens it
  shows the **current description from the DB** (read-only) with a small source
  label: Manual, AI-reviewed (date), AI, or none. No AI call.
- **Draft description** button (+ source toggles + optional note field). Only on
  click. The draft appears **below** the current text, which stays visible: an
  editable textarea, warnings for `conflicts` and `uncertain` items (left out of
  the text), and a word count against the 15–60 target. **Discard draft** and
  **Regenerate** (with the note) beside it.
- **Approving the image saves the draft** (owner decision): if a draft (edited or
  not) is present when the reviewer clicks Approve, the panel approves the image
  and then saves the draft as `ai_reviewed`. No draft = description untouched.
  The image approval never waits on or fails because of the description: if the
  save fails or hits a 409, the image stays approved and the reviewer sees a
  notice naming the product, with the draft text so it isn't lost.
- No standalone Save button and no draft persistence (Claude's call, YAGNI).
- If the product is already `ai_reviewed` or `manual`, the current text shows its
  label; Draft is still available (redraft overwrites only on Approve, and only
  through the conditional save).
- Several Backfill cards can belong to one product (one per colour). Each card
  loads the product's current description fresh when opened, so a description
  saved via one card shows on the next.

## Failure modes

- Draft call fails / times out / model returns no text → inline error in the
  section, Retry; image approval unaffected.
- Model returns invalid JSON or text over the cap → treated as a failed draft.
- Product row missing (`unknown_product` card) → section hidden.
- Save conflict → 409, panel shows the newer text and asks the reviewer to redo.
- Drive originals unavailable → draft with the mockup only if toggled, else error.

## Out of scope / later

- Wiring the panel into Batch review and Quick Generate.
- Tags, once `tag_vocab` is approved.
- Telling retail-inventory's session the ownership rule and migration status:
  done by message when this design is approved.

## Calls made by Claude (not owner decisions)

- Vendored prompt copy over shared Supabase row.
- This session writes the migration (owner approved 2026-10-24).
- Claude Opus 5.5 (one pass) instead of retail's Codex + Sonnet pair.
- Redraft notes and drafts are not persisted; no standalone Save button.
- Optimistic save keyed on loaded `description` + `description_reviewed_at`.
- Both image sources on by default.
