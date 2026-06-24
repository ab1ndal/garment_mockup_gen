---
title: Bindals Creation Mockup API
emoji: 🎨
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
---

# Bindal's Creation — Mockup Generator

AI-powered luxury garment mockup generator. Produces photorealistic fashion
mockups (Google Gemini) and short product videos (Google VEO) for Bindal's
Creation, an Indian ethnic-wear brand.

Ported from a single Streamlit app into a **React frontend (Vercel) + FastAPI
backend (Hugging Face Spaces)**, gated behind Google login and integrated with
the existing `Inventory-Management` Supabase project and the team's Google Drive.

> The YAML header above configures the Hugging Face **Docker Space** that hosts
> the backend (`sdk: docker`, port `7860`). It is required by HF and harmless
> elsewhere.

## Architecture

```
mockup_generator/         # framework-agnostic core (pure Python)
  config.py               # single settings/secrets source
  prompts/                # garment prompts + CATEGORY_PROMPTS (keyed by categoryid)
  generation/             # Gemini images, VEO video, legacy OpenAI engines
  integrations/           # Supabase clients (anon / service / per-user)
  db/                     # repositories over Supabase tables (profiles, ...)
backend/                  # FastAPI app — wraps core + auth (deploys to HF Spaces)
  auth.py                 # Supabase token verify + profiles allowlist gate
  main.py                 # /api/health, /api/me
frontend/                 # React (Vite + TS) — Google login (deploys to Vercel)
Dockerfile                # backend container for HF Spaces
docs/plans/               # design + phased implementation plan
app.py                    # legacy Streamlit UI (kept working during transition)
```

**Hosting:** frontend = Vercel (static), backend = Hugging Face Spaces (Docker),
auth/DB/storage = Supabase, raw images = Google Drive. All free-tier capable.

**Auth:** the React app signs in with Supabase Google OAuth. The backend
verifies the access token and allows the request only if a matching `profiles`
row exists with `is_active = true`. `role` (`user`/`admin`/`superadmin`) gates
admin actions.

## Requirements

- Python 3.10 (`>=3.10,<3.11`), [Poetry](https://python-poetry.org/)
- Node.js 18+ and npm

---

## Development (run locally)

Two processes: backend on `:8000`, frontend on `:5173`.

### 1. Backend

```bash
poetry install
```

Create `.env` in the repo root:

```bash
cp .env.example .env
# Fill in all required keys — see .env.example for the full list with comments.
```

Key variables the backend requires at runtime:

| Variable | Required? | Notes |
|---|---|---|
| `GOOGLE_API_KEY` | Yes | Gemini Developer API key |
| `OPENAI_API_KEY` | Yes | Legacy `gpt-image-1` path only |
| `GOOGLE_GENAI_USE_VERTEXAI` | No (default off) | Set `true` to bill via Vertex AI |
| `GOOGLE_CLOUD_PROJECT` | When Vertex on | GCP project id |
| `GOOGLE_CLOUD_LOCATION` | No (default `global`) | Vertex region |
| `GOOGLE_VERTEX_SA_JSON` | No | Path or JSON; falls back to `GOOGLE_DRIVE_SA_JSON` |
| `GOOGLE_DRIVE_SA_JSON` | No | Service-account for Drive image reads |
| `SUPABASE_PROJECT_ID` | No | Required for DB/auth features |
| `SUPABASE_PUBLISHABLE_KEY` | No | Client-safe anon key |
| `SUPABASE_SECRET_KEY` | No | Server-only — never expose to clients |
| `GEMINI_IMAGE_MODEL` | No (default `gemini-3-pro-image`) | Override to target a preview model |
| `VEO_MODEL` | No (default `veo-3.1-generate-preview`) | |
| `VEO_POLL_TIMEOUT_SEC` | No (default `900`) | |
| `VEO_POLL_INTERVAL_SEC` | No (default `10`) | |

Run it:

```bash
poetry run uvicorn backend.main:app --reload --port 8000
# health check: curl http://localhost:8000/api/health
```

### 2. Frontend

```bash
cd frontend
npm install
cp .env.example .env
```

Set `frontend/.env` for local split dev (point the app at the local backend):

```bash
VITE_SUPABASE_URL=https://<your-project-ref>.supabase.co
VITE_SUPABASE_ANON_KEY=<your-anon-publishable-key>   # client-safe; never the secret key
VITE_API_URL=http://localhost:8000
```

Run it:

```bash
npm run dev        # http://localhost:5173
```

### 3. Enable Google login (one-time, Supabase dashboard)

- Authentication → Providers → **Google**: add the OAuth client id/secret.
- Authentication → URL Configuration: add `http://localhost:5173` to the
  redirect allowlist.
- Only accounts with an active `profiles` row can sign in. Currently active:
  `bindalscreations@gmail.com` (admin), `bindal.abhinav@gmail.com` (superadmin).

### Legacy Streamlit UI (still works during the transition)

```bash
poetry run streamlit run app.py
```

### Tests

```bash
poetry run python -m pytest -q     # backend / core smoke tests
cd frontend && npm run build       # type-check + build the frontend
```

---

## Deployment

### Backend → Hugging Face Spaces (Docker)

1. Create a new **Space** → SDK **Docker** (it reuses this repo's `Dockerfile`
   and the YAML header in this README; the container listens on `7860`).
2. Push this repo to the Space (or connect the GitHub repo).
3. Space → **Settings → Variables and secrets**, add every key from `.env.example`
   that your deploy needs. Minimum set:
   - `GOOGLE_API_KEY`, `OPENAI_API_KEY`
   - `GOOGLE_GENAI_USE_VERTEXAI=true`, `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`
   - `GOOGLE_VERTEX_SA_JSON` — paste the raw JSON content of the Vertex service-account key
     (the same SA can serve Vertex + Drive if it holds `roles/aiplatform.user`)
   - `GOOGLE_DRIVE_SA_JSON` — paste the raw JSON content of the Drive reader SA, or omit
     if `GOOGLE_VERTEX_SA_JSON` uses a SA that also has Drive access
   - `SUPABASE_PROJECT_ID`, `SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_SECRET_KEY`
   - `FRONTEND_ORIGINS=https://<your-app>.vercel.app` (CORS allowlist)
4. The Space **must be public** so approved mockups served from the public Supabase
   Storage bucket render for anonymous viewers.
5. The backend will be live at `https://<user>-<space>.hf.space`
   (verify `…/api/health`).

### Rotating / replacing Google Cloud credentials

Credentials live **only** as host secrets (Hugging Face Space secrets) and in the
local `.env` — never committed. After any rotation, **redeploy/restart the Space**
so it picks up the new secret. Always verify the new credential works before
revoking the old one.

**1. Vertex AI service account — `GOOGLE_VERTEX_SA_JSON`**
(Gemini image + VEO video generation, when `GOOGLE_GENAI_USE_VERTEXAI=true`)

1. In the Google Cloud Console for the target project (`GOOGLE_CLOUD_PROJECT`),
   create a new service account — or a new JSON key on the existing one.
2. Grant it the **`Vertex AI User`** role on that project.
3. Download the JSON key.
4. Update `GOOGLE_VERTEX_SA_JSON` (path or JSON content) in local `.env` **and**
   in the HF Space secrets, then restart/redeploy the Space.
5. Once the new key is verified, delete/revoke the old key in the console.

**2. Drive reader service account — `GOOGLE_DRIVE_SA_JSON`**
(reads product image folders; current SA: `mockup-drive-reader@...`)

1. Create the new service account / JSON key.
2. **IMPORTANT:** each product Drive folder must be **re-shared** with the new
   service-account email (read access), or reads return 403. If you switch the
   whole Google account, **every** product folder needs re-sharing.
3. Update `GOOGLE_DRIVE_SA_JSON` (path or JSON content) in local `.env` and in
   the HF Space secrets, then redeploy.
4. **Verify before revoking:** confirm the new SA can actually read a product
   folder — hit the image-preview path / list a product's images and check the
   images load (not a 403). This is the highest-risk path: it depends on every
   folder being re-shared with the new SA email, so revoking early causes silent
   403s.
5. Revoke the old key once the new one is verified.

**3. API key — `GOOGLE_API_KEY`**
(used only when `GOOGLE_GENAI_USE_VERTEXAI` is unset/false)

1. Regenerate the key in the Google Cloud Console.
2. Update `GOOGLE_API_KEY` in local `.env` and in the HF Space secrets, then redeploy.
3. Revoke the old key once verified.

### Frontend → Vercel

1. New Project → import this repo → set **Root Directory** to `frontend`
   (framework auto-detects as Vite; `vercel.json` handles SPA routing).
2. Project → **Settings → Environment Variables**:
   - `VITE_SUPABASE_URL=https://<your-project-ref>.supabase.co`
   - `VITE_SUPABASE_ANON_KEY=<same as SUPABASE_PUBLISHABLE_KEY>`  (client-safe; never the secret key)
   - `VITE_API_URL=https://<user>-<space>.hf.space`  ← the HF backend URL
3. Deploy. Note the Vercel URL and put it in the backend's `FRONTEND_ORIGINS`.

### Wire up auth for production

- Supabase → Authentication → URL Configuration: add the **Vercel URL** to the
  redirect allowlist (the app uses `redirectTo = window.location.origin`).
- Google Cloud OAuth client: add the Vercel URL to **Authorized JavaScript
  origins**, and `https://<your-project-ref>.supabase.co/auth/v1/callback`
  to **Authorized redirect URIs**.

---

## API

| Method | Path          | Auth         | Description                        |
|--------|---------------|--------------|------------------------------------|
| GET    | `/api/health` | none         | Liveness check                     |
| GET    | `/api/me`     | Bearer token | Authenticated user id, email, role |

Authenticated requests send `Authorization: Bearer <supabase access token>`.

## Data model (Supabase `Inventory-Management`)

`profiles` = team allowlist; `products` (`producturl` = Drive input folder,
`categoryid` = garment type); `categories`; `mockups` = per-product status
(`base_mockup` etc. — the dedup signal); `productimages` = published outputs.

## Roadmap

See `docs/plans/2026-06-21-implementation-plan.md`. Phases:

0. ✅ Refactor core into a framework-agnostic package
1. ✅ Google login + `profiles` gate (React + FastAPI)
2. ⬜ Product list + prompts + status API
3. ⬜ Generation + Google Drive + variations
4. ⬜ Review UI (input vs output, feedback / approve → Supabase Storage)
5. ⬜ Polish (video in UI, backfill, more prompts, deploy)

## Plans & specs

Implementation plans live in `docs/superpowers/plans/` and their design specs in
`docs/superpowers/specs/` (later phases); earlier phase design + plan docs are in
`docs/plans/`.

## Garment categories

Prompts are keyed by Supabase `categoryid`: `SA` (Saree), `KP` (Kurta Pajama),
`C-KP` (Kurta Pajama child), `GWN` (Gown), `LE` (Lehenga), `SHT` (Shirt),
`KUR` (Kurti), `NHJ` (Nehru Jacket), `SKT-TOP` (Skirt-Crop Top), `CRD`
(Cord Set), `TOP` (Women's Top). Categories without a tailored prompt fall back
gracefully.
