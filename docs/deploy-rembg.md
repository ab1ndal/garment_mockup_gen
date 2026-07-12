# Deploying background removal (rembg / BiRefNet)

The product-shot import flow (`/api/import/*`) removes image backgrounds locally
with [rembg](https://github.com/danielgatis/rembg) + a BiRefNet model — no API,
no Gemini, zero per-image cost. Notes for the Hugging Face Docker Space.

## Model

- Default: **`birefnet-general-lite`** (~214 MB). Override with the `REMBG_MODEL`
  env var (e.g. `birefnet-general`, ~928 MB, higher quality, more RAM/latency).
- Licenses: rembg MIT, BiRefNet MIT — commercial use OK.

## Pre-cache (Dockerfile)

The `Dockerfile` bakes the lite model into the image so the first request doesn't
pay a ~214 MB cold download:

```dockerfile
ENV U2NET_HOME=/app/.u2net
RUN python -c "from rembg import new_session; new_session('birefnet-general-lite')"
```

`libgomp1` is installed because onnxruntime / numba need the OpenMP runtime at
import on the slim base image.

If you change `REMBG_MODEL`, update the model name in this pre-cache `RUN` too, or
the running model will download on first use instead of being baked in.

## Startup warm-up (optional)

`REMBG_WARM` (default off) warms the model session at boot. With the model baked
into the image this only saves the first-request session load (~seconds, no
download), at the cost of extra boot RAM. Set `REMBG_WARM=1` in the Space if you
want the first import request to be fast; leave unset to load lazily.

## Performance

BiRefNet-lite is CPU-bound: ~seconds per image (measured ~8 s / 1 MP on an
arm64 dev CPU; linux x64 differs). The import flow is one-image-at-a-time by
design; the frontend debounces preview calls. Do not batch-block a request.
