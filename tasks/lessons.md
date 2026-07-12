# Lessons

Project-specific lessons captured from user corrections and gotchas. Reviewed at session start. Update on any correction — write the rule behind it, apply same session.

## Format

- **[YYYY-MM-DD] Short rule** — what went wrong, what to do instead, why.

## Entries

- **[2026-07-12] rembg on Python 3.10 needs three-way pin** — `rembg[cpu]==2.0.69` is the last release supporting py3.10 (2.0.70+ requires 3.11). Bare `numpy`/`onnxruntime` resolve wrong: numpy latest needs py3.12 (pin `>=1.26,<2.3`, 2.2.x is the 3.10 ceiling); onnxruntime latest lacks cp310 macOS-arm64 wheels (pin `>=1.19,<1.20` — 1.19.2 ships cp310 for mac-arm64 dev + linux-x64 HF). Why: strict `requires-python = ">=3.10,<3.11"`. Applies: any new sci-py-adjacent dep here — dry-run `poetry add --dry-run` and check wheel abi tags before committing.
- **[2026-07-12] BiRefNet-lite ~8s/image warm on CPU** — `birefnet-general-lite` cutout on mac-arm64 CPU is ~8s/image (1144×811). Design for one-at-a-time interactive use with debounced preview; never batch-block a request. Full `birefnet-general` (~928MB) is heavier still. See [[dependency pin lesson]].
