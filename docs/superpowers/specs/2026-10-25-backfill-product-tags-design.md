# Product tags in Backfill review

**Date:** 2026-10-25
**Status:** Draft, pending owner approval
**Builds on:** `2026-10-24-backfill-product-descriptions-design.md`

## Intent

Backfill is currently the main way products are approved. Owner (2026-10-25):
"Usually tags are auto generated but allow users to edit and pick as needed from
ai generated options". So the same Draft click that writes the description also
proposes tags. The reviewer keeps, drops or adds values, and Approve saves them
to `product_tags`, the same way it saves the description.

## Ownership (agreed with retail-inventory session, 2026-10-25)

- mockup-generator writes `product_tags` for new products approved through
  Backfill. Retail's batch handles live products, and skips any product that
  already has `ai_reviewed` or `manual` tag rows.
- Vocabulary: vendored `tag_vocab.json` (retail `a11dc28`). Every DB category is
  in a family. A category added later gets no tag editor, because there are no
  allowed values for it.

## Data (retail owns the schema; live)

`product_tags(productid, tag_group, value, source in ai|ai_reviewed|manual,
confidence 0..1 null, created_at)`, with PK `(productid, tag_group, value)`.
There is no placement column: work placement stays in the description text.

Write rule (shared with retail):
- Replace the product's `ai` and `ai_reviewed` rows; never touch `manual` rows.
- A value the AI proposed that the reviewer kept is `ai_reviewed`, with the AI's
  confidence.
- A value the reviewer added that the AI did not propose is `manual`, with null
  confidence.
- Single-value groups (silhouette, length, neck, sleeve) hold at most one value
  per product.

**Write path (live, retail `e34fac0`, PR #67):**
`public.replace_product_tags(p_productid varchar, p_tags jsonb, p_scope text) RETURNS SETOF product_tags`,
called over PostgREST RPC with the service-role client.
- `p_tags` is the complete approved set every time: `[{tag_group, value, source, confidence?}]`.
- `p_scope='ai'` (Backfill) deletes ai/ai_reviewed rows and inserts ours. `manual` rows are never touched; if a
  manual row holds a single-value slot, our value for that group is skipped. The returned rows are what was stored.
- One transaction. It raises and writes nothing for a value not allowed for the category, two values in one
  single-value group, an unknown product, or a bad scope/source. Its message is shown to the reviewer.
- Two PostgREST calls of our own (delete + insert) were *rejected*: not atomic, and the rule would live in two apps.

## Model output

The draft already returns tag fields. Today they are ignored. Map them as
follows:

| Schema field | Group | Shape |
|---|---|---|
| `occasion[]` | occasion | many |
| `work[].type` (+ `confidence`) | work | many; ignore `placement` |
| `print_weave[]` | print_weave | many |
| `silhouette` / `length` / `neck` / `sleeve` | same | one, or `""` = none |

- Drop any value not in `allowed(group, family)` from the draft rather than
  showing it, and log it. The schema enums allow out-of-family values. Retail
  calls these "violations".
- Duplicate work types: keep one, with the highest confidence.
- Confidence: work items carry one. Other groups have none in the schema, so they
  are saved as `ai_reviewed` with null confidence.

## API

- `GET /api/backfill/tags/{productid}` returns
  `{family, groups: [{group, label, single, allowed[]}], current: [{group, value,
  source, confidence}]}`. It reads the vocab and the product's rows. No AI call.
- The draft response gains `tags: [{group, value, confidence}]`. These are the
  AI proposals, already filtered. The draft still writes nothing.
- `POST /api/backfill/approve` gains an optional `tags: [{group, value}] | null`,
  the reviewer's final picks. Null means tags are left untouched. After the image
  is published and the description saved, the server works out each value's
  `source`/`confidence` from the draft proposals, then calls the RPC.
  - Proposals are sent back from the client as `tags_proposed`, because drafts
    are not stored.
  - Order: image, then description, then tags. Tags are written only if the
    description saved. If the description changed since load (conflict) or failed,
    tags are skipped too, so a product never gets new tags over a stale
    description (retail's advice). A combined RPC is not needed.
  - Failure follows the description rule: the image stays approved and
    `tags_warning` carries the RPC's message. If a manual row took a single-value
    slot, the warning names the skipped value.
- Validation at the boundary: every value must be in `allowed(group, family)`,
  and single groups take at most one value. Otherwise the tags are skipped with
  a warning; the approval itself never fails because of tags.

## UI (Backfill review panel, in the Description section)

- **On open:** the product's current tags are shown read-only as chips grouped by
  tag group, with `manual` ones marked. If there are none, it says "No tags".
- **After Draft:** an editable tag block appears under the draft text.
  - Multi-value groups: every allowed value is a toggle chip. AI proposals start
    selected and show a small "AI" mark; other allowed values start off.
  - Single-value groups: a select or segmented control with "—" (none) plus the
    allowed values, preset to the AI's value.
  - Existing `manual` values show as selected and locked, with a note that
    they are kept.
- **Discard draft** drops the tag edits as well. **Regenerate** resets them to the
  new proposals.
- **Approve** with a draft open saves the description and tags together. With no
  draft open, neither is touched.
- Follow `ui-ux-pro-max`:
  - chips at least 44px touch height with an 8px gap;
  - selection shown with a check icon, not by colour alone;
  - keyboard toggles;
  - group labels from `tag_vocab.json`.

## Out of scope

- Editing tags without drafting. Owner may want this later, but the first
  version mirrors the description flow (Claude's call).
- Auto-applying `ai` tags without review, and storefront filters (both retail's).
- Optimistic conflict guard for tags (Claude's call). Retail's batch skips
  reviewed products, and two reviewers on one product is rare. The RPC is
  atomic, so the worst case is the last writer winning.

## Calls made by Claude

- Tags ride on the existing Draft call: one AI call, no new cost.
- The editor appears only with a draft.
- Out-of-family AI values are dropped rather than flagged.
- No tag conflict guard.
- Use retail's RPC instead of writing with two PostgREST calls.
- Tags are saved only when the description saved in the same approval.
