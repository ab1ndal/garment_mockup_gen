-- Phase 7 backfill: backfilled images have no generation prompt.
-- Make prompt_text nullable so the audit row can record provenance
-- (who/when/source path) without a fabricated prompt.
alter table public.mockup_variations alter column prompt_text drop not null;
