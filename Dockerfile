# Backend (FastAPI) container for Hugging Face Spaces (Docker SDK).
# Serves only the API; the React frontend is hosted separately on Vercel.
FROM python:3.10-slim

WORKDIR /app

# Build deps for any wheels that need compiling, plus libgomp1 (OpenMP runtime
# required by onnxruntime / numba at import), then clean up.
RUN apt-get update && apt-get install -y --no-install-recommends gcc libgomp1 \
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

# Pre-cache the BiRefNet-lite model (~214 MB) into the image so the first
# product-shot import request doesn't pay a cold download. U2NET_HOME is baked
# read-only; the running process only reads it. Set REMBG_MODEL to change model.
ENV U2NET_HOME=/app/.u2net
RUN python -c "from rembg import new_session; new_session('birefnet-general-lite')"

# App code last: only these layers rebuild on a code-only push. The reinstall is
# --no-deps (deps are already present) purely to replace the stub in site-packages
# with the real package.
COPY mockup_generator ./mockup_generator
COPY backend ./backend
RUN pip install --no-cache-dir --no-deps .

# HF Spaces routes traffic to port 7860 by default.
ENV PORT=7860
EXPOSE 7860

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "7860"]
