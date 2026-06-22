# Backend (FastAPI) container for Hugging Face Spaces (Docker SDK).
# Serves only the API; the React frontend is hosted separately on Vercel.
FROM python:3.10-slim

WORKDIR /app

# Build deps for any wheels that need compiling, then clean up.
RUN apt-get update && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

# Install the core package + its runtime deps (no dev group).
COPY pyproject.toml poetry.lock README.md ./
COPY mockup_generator ./mockup_generator
COPY backend ./backend
RUN pip install --no-cache-dir .

# HF Spaces routes traffic to port 7860 by default.
ENV PORT=7860
EXPOSE 7860

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "7860"]
