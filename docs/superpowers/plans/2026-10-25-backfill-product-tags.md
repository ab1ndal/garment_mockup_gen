# Backfill Product Tags Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Backfill Draft proposes tags with the description. The reviewer
keeps, drops or adds tags, and Approve saves them to `product_tags` through
retail's `replace_product_tags` RPC.

**Architecture:**
- Vocab lookups: `tag_vocab.py` gains `groups_for`.
- Draft parsing: `draft.py` gains `Draft.tags`, filtered to the family.
- New repo: `product_tags_repo.py` reads tags and calls the RPC.
- Routes: a new GET for tags. The draft response gains `tags`, and approve gains
  `tags` plus a `tags_warning`.
- UI: a new `TagEditor.tsx`, rendered inside `DescriptionSection`. Tag state
  lives in `ReviewPanel` beside `descDraft`.

**Tech Stack:** FastAPI, supabase-py (PostgREST `rpc`), React/TS (no frontend
test runner; verified by `tsc -b` and in the browser), pytest.

**Spec:** `docs/superpowers/specs/2026-10-25-backfill-product-tags-design.md`

## Global Constraints

- `product_tags` writes go only through `replace_product_tags(p_productid, p_tags, p_scope)` with `p_scope="ai"`. There are no direct table writes.
- `p_tags` holds the complete approved set: `[{tag_group, value, source, confidence}]`. Source is `ai_reviewed` for AI values kept (with the AI confidence) or `manual` for values the reviewer added (confidence null). Never `ai`.
- Single-value groups: `silhouette`, `length`, `neck`, `sleeve`, with at most one value each.
- Order on Approve: image, then description, then tags. Tags are saved only when the description saved in the same request (`description` sent and `_save_description` returned None).
- Tags never fail an approval: any problem becomes `tags_warning`.
- Only `allowed(group, family)` values are offered or accepted. Out-of-family AI values are dropped and logged.
- Existing `manual` rows are never sent. The RPC keeps them, and the UI shows them locked.
- Plain `def` routes. UI follows `ui-ux-pro-max`: chips at least 44px tall with an 8px gap, a check icon on selected chips, and keyboard-toggleable native buttons with `aria-pressed`.

## Review Focus

1. The AI proposes a value outside the family (e.g. `office` on a lehenga). It is dropped from the draft, not shown. Test: Task 2 `test_out_of_family_tag_dropped`.
2. The AI repeats a work type. One chip is kept, with the max confidence. Test: Task 2 `test_duplicate_work_keeps_max_confidence`.
3. The description changed since load (conflict). Tags are not written, and the warning says so. Test: Task 4 `test_approve_skips_tags_when_description_not_saved`.
4. The RPC raises, e.g. "not allowed for ...". The image is still approved and `tags_warning` carries the message. Test: Task 4 `test_approve_rpc_error_is_warning`.
5. A manual row holds a single-value slot, so the RPC silently skips our value. The warning names the skipped value. Test: Task 4 `test_approve_reports_skipped_tags`.

Spec refinement (ledger as a ruling): the client sends `source`/`confidence`
with each tag instead of a separate `tags_proposed` list. The server validates
`source in {ai_reviewed, manual}`, `confidence` in 0..1 or null, and the value
being allowed. The reviewers are trusted staff, so a mislabelled source only
mis-marks a tag. Update the spec's API section in Task 4.

---

### Task 1: Vocab groups for the UI

**Files:**
- Modify: `mockup_generator/prompts/tag_vocab.py`
- Test: `tests/test_tag_vocab.py` (new)

**Interfaces:**
- Produces: `TagGroup(group: str, label: str, single: bool, values: list[tuple[str, str]])` (value, label), a frozen dataclass. Also `groups_for(family: str | None) -> list[TagGroup]`, which returns the groups in json order with at least one allowed value; `[]` for None.

- [ ] **Step 1: Write failing tests**

```python
def test_groups_for_lehenga_excludes_office():
    gs = {g.group: g for g in groups_for("lehenga")}
    assert "office" not in [v for v, _ in gs["occasion"].values]
    assert gs["occasion"].label == "Occasion" and gs["occasion"].single is False
    assert gs["neck"].single is True

def test_groups_for_none_is_empty():
    assert groups_for(None) == []

def test_groups_for_values_match_allowed():
    for g in groups_for("saree"):
        assert [v for v, _ in g.values] == allowed(g.group, "saree")
```

- [ ] **Step 2:** `poetry run pytest tests/test_tag_vocab.py -q`. Expected: ImportError on `groups_for`.
- [ ] **Step 3:** Implement `TagGroup`/`groups_for`. `single` is `group in SINGLE`; labels come from the json `label` fields.
- [ ] **Step 4:** Same command. Expected: 3 passed.
- [ ] **Step 5:** Commit `feat(tags): vocab groups for the review UI`.

### Task 2: Draft returns proposed tags

**Files:**
- Modify: `mockup_generator/describe/draft.py`
- Test: `tests/test_describe_draft.py`

**Interfaces:**
- Consumes: `allowed`, `family_of`, `SINGLE` from `tag_vocab`.
- Produces: `ProposedTag(group: str, value: str, confidence: float | None)`, a frozen dataclass, and a `Draft.tags: list[ProposedTag]` field (last field, default `field(default_factory=list)`, so existing constructors still work). Order: occasion, work, print_weave, then SINGLE.

- [ ] **Step 1: Write failing tests** (`_OK` gains tag fields; facts are a saree):

```python
def test_parses_tags():
    reply = {**_OK, "occasion": ["wedding", "festive"], "work": [{"type": "zari", "placement": "border", "confidence": 0.9}],
             "print_weave": ["banarasi"], "silhouette": "", "length": "", "neck": "", "sleeve": ""}
    out = d.draft_description(_FACTS, [_img()], None, client=FakeClient(_msg(json.dumps(reply))))
    assert out.tags == [d.ProposedTag("occasion", "wedding", None), d.ProposedTag("occasion", "festive", None),
                        d.ProposedTag("work", "zari", 0.9), d.ProposedTag("print_weave", "banarasi", None)]

def test_out_of_family_tag_dropped():   # lehenga facts, "office" not allowed
    ...assert all(t.value != "office" for t in out.tags)

def test_duplicate_work_keeps_max_confidence():
    # work: zari 0.4, zari 0.8  ->  one ProposedTag("work", "zari", 0.8)
```

- [ ] **Step 2:** `poetry run pytest tests/test_describe_draft.py -q`. Expected: the 3 new tests fail (`ProposedTag` missing).
- [ ] **Step 3:** Implement `_proposed_tags(data: dict, family: str | None) -> list[ProposedTag]`. Clamp confidence to 0..1. Log dropped values at info with productid.
- [ ] **Step 4:** Same command. Expected: all pass.
- [ ] **Step 5:** Commit `feat(tags): draft proposes tags from the photos`.

### Task 3: Tags repo (read + RPC)

**Files:**
- Create: `mockup_generator/db/product_tags_repo.py`
- Test: `tests/test_product_tags_repo.py`

**Interfaces:**
- Produces:
  - `TagRow(group: str, value: str, source: str, confidence: float | None)`, a frozen dataclass.
  - `get_tags(client, productid) -> list[TagRow]`, which selects `tag_group, value, source, confidence` from `product_tags`.
  - `replace_ai_tags(client, productid, tags: list[TagRow]) -> list[TagRow]`, which calls `client.rpc("replace_product_tags", {"p_productid": productid, "p_tags": [{"tag_group", "value", "source", "confidence"}...], "p_scope": "ai"}).execute()` and maps the returned rows. A PostgREST error propagates.

- [ ] **Step 1: Failing tests:**
  - `test_get_tags_maps_rows` uses a fake `table()`, in the style of `test_product_descriptions_repo.py`.
  - `test_replace_ai_tags_calls_rpc` uses a fake `rpc(name, params)` that records its args. It asserts the exact params dict, including `p_scope == "ai"`, and that the returned rows map back.
- [ ] **Step 2:** `poetry run pytest tests/test_product_tags_repo.py -q`. Expected: ModuleNotFoundError.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Same command. Expected: 2 passed.
- [ ] **Step 5:** Commit `feat(tags): product_tags repo over replace_product_tags`.

### Task 4: Routes: read, draft tags, save on approve

**Files:**
- Modify: `backend/schemas.py`, `backend/routers/backfill.py`
- Modify: `docs/superpowers/specs/2026-10-25-backfill-product-tags-design.md` (API refinement above)
- Test: `tests/test_backfill_tags_api.py` (new); `tests/test_backfill_description_api.py` (draft tags field)

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces (schemas):
  - `TagValue{value, label}`
  - `TagGroupOut{group, label, single, values: list[TagValue]}`
  - `TagOut{group, value, source, confidence: float | None}`
  - `BackfillTags{family: str | None, groups: list[TagGroupOut], current: list[TagOut]}`
  - `ProposedTagOut{group, value, confidence}`
  - `BackfillDraftResponse.tags: list[ProposedTagOut]`
  - `TagPick{group, value, source: Literal["ai_reviewed","manual"], confidence: float | None = Field(None, ge=0, le=1)}`
  - `BackfillApproveRequest.tags: list[TagPick] | None = None`
  - The approve response gains `tags_warning`.
- Route: `GET /api/backfill/tags/{productid}` → `BackfillTags`. It uses `products_repo.get_product` for `categoryid` and returns 404 if the product is missing.
- Warnings (exact copy):
  - `_TAGS_NOT_SAVED = "Tags not saved because the description was not saved."`
  - `_TAGS_INVALID = "Tags not saved: {detail}."`
  - `_TAGS_SAVE_FAILED = "Image approved, but the tags could not be saved: {detail}"`
  - `_TAGS_SKIPPED = "Kept hand-entered tags; not saved: {values}."`

- [ ] **Step 1: Failing tests** in `test_backfill_tags_api.py`:
  - `test_get_tags_returns_groups_and_current`
  - `test_get_tags_404`
  - `test_approve_saves_tags_after_description`: asserts the RPC was called once with the picks and `tags_warning is None`.
  - `test_approve_without_tags_leaves_them`: the RPC is not called.
  - `test_approve_skips_tags_when_description_not_saved`: `save_reviewed` returns False, so the RPC is not called and `tags_warning == _TAGS_NOT_SAVED`.
  - `test_approve_rejects_disallowed_value`: `office` on LE means no RPC call and `_TAGS_INVALID`.
  - `test_approve_rejects_two_single_values`: two `neck` values means no RPC call.
  - `test_approve_rpc_error_is_warning`: the RPC raises, so the status is 200 and the warning holds the message.
  - `test_approve_reports_skipped_tags`: the RPC returns fewer rows than sent, so `_TAGS_SKIPPED` names the missing `group: value`.
  - Also extend the draft test so `tags` is in the response.
  - Reuse the monkeypatching style of `test_backfill_description_api.py`, and stub publish the way that file does.
- [ ] **Step 2:** `poetry run pytest tests/test_backfill_tags_api.py tests/test_backfill_description_api.py -q`. Expected: new tests fail (404 on the route, missing fields).
- [ ] **Step 3:** Implement the schemas, the GET route, the draft `tags` mapping, and `_save_tags(db, req, categoryid) -> str | None` called after `_save_description`.
  - `_save_tags` validates against `allowed(group, family_of(categoryid))` and the single-group limit, calls `replace_ai_tags`, and diffs the returned rows against what was sent.
  - It never raises, and logs with `exc_info=True` on RPC failure.
  - Get `categoryid` from the `get_product` call approve already makes, if it makes one; otherwise call `products_repo.get_product`.
  - Update the spec's API bullets: `tags` carries source/confidence, and there is no `tags_proposed`.
- [ ] **Step 4:** Same command, then `poetry run pytest -q`. Expected: all pass.
- [ ] **Step 5:** Commit `feat(tags): read, propose and save tags in Backfill approve`.

### Task 5: Tag editor in the review panel

**Files:**
- Create: `frontend/src/components/TagEditor.tsx`
- Modify: `frontend/src/api.ts`, `frontend/src/components/DescriptionSection.tsx`, `frontend/src/components/BackfillTab.tsx`, `frontend/src/index.css`

**Interfaces:**
- Consumes: Task 4 shapes.
- Produces:
  - In `api.ts`: types `TagGroup`, `TagRow`, `ProposedTag`, `TagPick`, `ProductTags`; `getBackfillTags(productid)`; `DescriptionDraft.tags`; `approveBackfill` with `tags?: TagPick[]` and response `tags_warning`.
  - `TagEditor` props: `{groups: TagGroup[]; manual: TagRow[]; proposed: ProposedTag[]; picks: TagPick[]; onChange(p: TagPick[]): void}`.
  - `DescriptionSection` gains props `tags: TagPick[] | null` and `onTagsChange`.

- [ ] **Step 1:** Invoke `ui-ux-pro-max` and check the chip, segmented-control, focus and contrast rules for the editor.
- [ ] **Step 2:** `DescriptionSection` loads `getBackfillTags` beside the description on mount and shows the current tags as read-only pills grouped by label, with a "Manual" mark, or "No tags".
  - On draft, `onTagsChange` receives the proposals as picks (`source: "ai_reviewed"`, AI confidence), and the proposals are kept in local state.
  - Discard clears both. Regenerate resets the picks to the new proposals.
- [ ] **Step 3:** In `TagEditor`, multi groups render `<button aria-pressed>` chips for every allowed value.
  - Turning on a value that wasn't proposed makes it `source: "manual"`, with confidence null.
  - AI-proposed chips carry a small "AI" mark.
  - Single groups render a radio-style segmented row with a "—" option.
  - `manual` values show selected and disabled, with the title "Hand-entered; kept". A single group whose slot a manual row holds is disabled entirely.
  - Manual rows are never put into `picks`.
- [ ] **Step 4:** `ReviewPanel` adds `tagPicks` state.
  - `doApprove` sends `tags: tagPicks` only when a description draft is sent.
  - `onResolved` appends `tags_warning` to the notice.
  - Reset `tagPicks` wherever `descDraft` resets.
- [ ] **Step 5:** `cd frontend && npm run build`. Expected: no TS errors.
- [ ] **Step 6:** Browser check against the local backend: open a Backfill card, see the current tags, click Draft, see the proposals preselected, toggle one off, add one, and switch a single group. Do not click Approve on a real card. Check approve via the Task 4 tests.
- [ ] **Step 7:** Commit `feat(tags): tag editor in Backfill review`.

### Task 6: Docs

- [ ] Update the CLAUDE.md "Product Descriptions" section: tags are proposed by the draft, saved on Approve through `replace_product_tags` (scope `ai`), and saved only after the description saves. Update the memory entry.
- [ ] `poetry run pytest -q`. Expected: all pass. Commit `docs: product tags in Backfill`.
