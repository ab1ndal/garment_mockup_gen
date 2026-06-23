# 2026-06-23 add color to mockup_variations + public mockups bucket

- `alter table mockup_variations add column if not exists color text;`
- `update storage.buckets set public = true where id = 'mockups';`
- Applied via Supabase MCP (project epotsxdugwfhyeiudjox).
- Verified: `color` column present; `mockups.public = true`; no anon/authenticated insert/update/delete policy on `storage.objects` for the `mockups` bucket (public read-by-URL needs no policy).
