-- Batch Generate staging moves from Google Drive to Supabase Storage.
--
-- Why: staged mockups were uploaded to a `_batch` subfolder of the Base Mockup
-- Folder by the service account. That can never work — a service account has no
-- storage quota of its own, and a file created in a My Drive folder counts
-- against the creator's quota, so every upload failed with
-- 403 storageQuotaExceeded. Google's remedies (Shared Drive, OAuth delegation)
-- both require Google Workspace, which this account does not have. Reads and
-- moves are unaffected, which is why the Backfill flow (which only moves
-- human-owned files) was never hit by this.
--
-- Staged mockups now live in the private `mockups-temp` bucket, keyed by item.
-- `thumbnail_link` is dropped: the bucket is private, so the UI gets a
-- short-lived signed URL generated per read instead of a persisted link.
alter table public.batch_items rename column drive_file_id to storage_path;
alter table public.batch_items drop column if exists thumbnail_link;

comment on column public.batch_items.storage_path is
  'Object path of the staged mockup in the private mockups-temp bucket; null until generated. Cleared on accept/reject/edit.';
