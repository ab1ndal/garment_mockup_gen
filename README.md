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
OPENAI_API_KEY=...
GOOGLE_API_KEY=...
SUPABASE_PROJECT_ID=epotsxdugwfhyeiudjox
SUPABASE_PUBLISHABLE_KEY=sb_publishable_...
# optional — only needed for server-side writes to non-profiles tables
SUPABASE_SECRET_KEY=sb_secret_...
```

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
VITE_SUPABASE_URL=https://epotsxdugwfhyeiudjox.supabase.co
VITE_SUPABASE_ANON_KEY=sb_publishable_...   # client-safe; never the secret key
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
3. Space → **Settings → Variables and secrets**, add:
   - `GOOGLE_API_KEY`, `OPENAI_API_KEY`
   - `SUPABASE_PROJECT_ID`, `SUPABASE_PUBLISHABLE_KEY`
   - `SUPABASE_SECRET_KEY` (optional, for non-profiles writes)
   - `FRONTEND_ORIGINS=https://<your-app>.vercel.app` (CORS allowlist)
4. The backend will be live at `https://<user>-<space>.hf.space`
   (verify `…/api/health`).

### Frontend → Vercel

1. New Project → import this repo → set **Root Directory** to `frontend`
   (framework auto-detects as Vite; `vercel.json` handles SPA routing).
2. Project → **Settings → Environment Variables**:
   - `VITE_SUPABASE_URL=https://epotsxdugwfhyeiudjox.supabase.co`
   - `VITE_SUPABASE_ANON_KEY=sb_publishable_...`
   - `VITE_API_URL=https://<user>-<space>.hf.space`  ← the HF backend URL
3. Deploy. Note the Vercel URL and put it in the backend's `FRONTEND_ORIGINS`.

### Wire up auth for production

- Supabase → Authentication → URL Configuration: add the **Vercel URL** to the
  redirect allowlist (the app uses `redirectTo = window.location.origin`).
- Google Cloud OAuth client: add the Vercel URL to **Authorized JavaScript
  origins**, and `https://epotsxdugwfhyeiudjox.supabase.co/auth/v1/callback`
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

## Garment categories

Prompts are keyed by Supabase `categoryid`: `SA` (Saree), `KP` (Kurta Pajama),
`C-KP` (Kurta Pajama child), `GWN` (Gown), `LE` (Lehenga), `SHT` (Shirt),
`KUR` (Kurti), `NHJ` (Nehru Jacket), `SKT-TOP` (Skirt-Crop Top), `CRD`
(Cord Set), `TOP` (Women's Top). Categories without a tailored prompt fall back
gracefully.
