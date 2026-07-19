# Backend (FastAPI) container for Hugging Face Spaces (Docker SDK).
# Serves only the API; the React frontend is hosted separately on Vercel.
FROM python:3.10-slim

WORKDIR /app

# Build deps for any wheels that need compiling, then clean up.
RUN apt-get update && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

# Install the runtime deps (no dev group) against a stub package, BEFORE the app
# code is copied. Docker invalidates every layer after a changed COPY, so copying
# code first made a one-line edit reinstall every dependency and re-download the
# model below. Building deps off pyproject/poetry.lock alone keeps this layer —
# and the model layer — cached until the dependency set itself changes.
COPY pyproject.toml poetry.lock README.md ./
RUN mkdir -p mockup_generator && touch mockup_generator/__init__.py \
    && pip install --no-cache-dir . \
    && rm -rf mockup_generator

# App code last: only these layers rebuild on a code-only push. The reinstall is
# --no-deps (deps are already present) purely to replace the stub in site-packages
# with the real package.
COPY mockup_generator ./mockup_generator
COPY backend ./backend
RUN pip install --no-cache-dir --no-deps .

# HF Spaces routes traffic to port 7860 by default; Render (and other PaaS hosts)
# inject their own $PORT at runtime. Bind $PORT when set, else fall back to 7860 so
# HF Spaces and local `docker run` keep working unchanged.
ENV PORT=7860
EXPOSE 7860

CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
