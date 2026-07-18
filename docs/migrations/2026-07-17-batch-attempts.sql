-- Auto-retry bookkeeping for Batch Generate. `attempts` counts how many times a
-- card's generation has been tried. The worker requeues transient failures (429
-- quota, 5xx, or an empty NO_IMAGE response) until this reaches the cap, then
-- marks the card failed for good — so a passing rate-limit no longer strands a
-- card in `failed` waiting on a manual retry.
alter table public.batch_items
  add column if not exists attempts int not null default 0;
