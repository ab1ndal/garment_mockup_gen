-- 2026-07-15 — reframe the product-tag cleanup lines.
--
-- WHY. Every prompt closed with a line instructing the model to remove product
-- tags "from the final output". The image model read that as an instruction
-- about the response itself and answered with finish_reason=NO_IMAGE — a
-- candidate carrying no image part at all. Three BC25050 cards died that way in
-- the 100-product batch of 2026-07-15; the rest of the batch failed on 429s
-- first (see 73b8e71), which masked how wide this was.
--
-- Verified against the live API before applying: the saree prompt's first 13
-- lines return an image (finish_reason=STOP), and adding the 14th ("Final
-- Cleanup: ... removed from the final output") returns none, repeatably. Both
-- reframed candidates below returned an image in the same run in which the live
-- line failed. The trigger is the prompt, not the source image: the same image
-- succeeds with a trivial prompt, and a different product's image fails with
-- this prompt.
--
-- THE FIX. Describe the garment rather than instruct about the output:
-- "the garment carries no product tags ... none are present in the photograph".
-- Note the gown prompt ends with near-identical wording and published fine, so
-- the phrase is not universally fatal — this reframes all of them rather than
-- guessing which prompts sit close enough to the edge to trip.
--
-- ALREADY APPLIED to the Inventory-Management project on 2026-07-15, together
-- with the matching edit to mockup_generator/prompts/defaults.py (the seed, so
-- a newly seeded category cannot reintroduce the old phrasing). Recorded here
-- for the history. Backups: prompts_backup_20260715, batch_items_backup_20260715
-- — drop them once the requeued batch has been reviewed.
--
-- Verification after applying:
--   29 of 32 prompts changed, each differing in exactly one line, line counts
--   unchanged. The 3 untouched (C-KP, NHJ, TOP) never had a tag line.

-- prompts.body — the templates.
update prompts set body = replace(body,
  $q$Final Cleanup: Completely remove all product tags, yellow hanging tags, pins, stands, and labels from the final output.$q$,
  $q$Final Cleanup: The garment carries no product tags of any kind — no yellow hanging tags, pins, stands, or labels. None of these are present anywhere in the photograph.$q$);

update prompts set body = replace(body,
  $q$Final Cleanup: Ensure that the product tags (including the yellow tag) are removed from the final output.$q$,
  $q$Final Cleanup: The saree carries no product tags of any kind — no yellow hanging tag, no price tag, no pins, clips, stands, or labels. None of these are present anywhere in the photograph.$q$);

update prompts set body = replace(body,
  $q$Final Cleanup: Ensure the final image is completely free of any brand tags, size labels, pins, clips, stands, or any other studio equipment.$q$,
  $q$Final Cleanup: The garment carries no brand tags, size labels, pins, or clips, and no stands or other studio equipment are visible anywhere in the photograph.$q$);

update prompts set body = replace(body,
  $q$Final Cleanup: Ensure product tags, pins, stands, or supports visible in the reference image are completely removed in the final output.$q$,
  $q$Final Cleanup: The gown carries no product tags, pins, stands, or supports; any such items visible in the reference image are absent from the styled garment.$q$);

update prompts set body = replace(body,
  $q$Final Cleanup: Ensure that all product tags, pins, stands, labels, or any other non-garment items are removed from the final output.$q$,
  $q$Final Cleanup: The lehenga carries no product tags, pins, stands, labels, or any other non-garment items. None of these are present anywhere in the photograph.$q$);

update prompts set body = replace(body,
  $q$Final Cleanup: Ensure that all external product tags, price tags, pins, stands, and branding labels not intrinsic to the garment are removed from the final output.$q$,
  $q$Final Cleanup: The shirt carries no external product tags, price tags, pins, stands, or branding labels that are not intrinsic to the garment. None of these appear anywhere in the photograph.$q$);

update prompts set body = replace(body,
  $q$Remove any background elements in the reference image or any product tags in the image.$q$,
  $q$The kurti carries no product tags, and the background elements of the reference image are absent from the photograph.$q$);

update prompts set body = replace(body,
  $q$- Ensure removal of all product tags or distracting elements.$q$,
  $q$- The garment carries no product tags, and no distracting elements are present in the photograph.$q$);

update prompts set body = replace(body,
  $q$Completely remove all product tags, yellow hanging tags, and labels from the final output.$q$,
  $q$The garment carries no product tags, yellow hanging tags, or labels. None of these are present anywhere in the photograph.$q$);

update prompts set body = replace(body,
  $q$Final Cleanup: Ensure that all product tags, yellow hanging tags, pins, stands, and labels are completely removed from the final output.$q$,
  $q$Final Cleanup: The garment carries no product tags, yellow hanging tags, pins, stands, or labels. None of these are present anywhere in the photograph.$q$);

update prompts set body = replace(body,
  $q$Ensure that all product tags, pins, stands, labels, and any background elements from the reference image are removed from the final output.$q$,
  $q$The kurti carries no product tags, pins, stands, or labels, and the background elements of the reference image are absent from the photograph.$q$);

-- batch_items.prompt_text — the frozen snapshots.
--
-- A card stamps its prompt at enqueue and Retry only flips status back to
-- queued, so without this the 190 failed cards would replay the exact text that
-- failed. FAILED ONLY: a published or rejected card records the prompt that
-- actually produced its image, which is history and not a template to refresh.
-- Apply each replacement above to batch_items.prompt_text where status='failed'.
