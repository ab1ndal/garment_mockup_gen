"""Primary image generation engine (Google Gemini).

Refactored from the former ``create_base`` module: shared helpers now live in
``generation.common`` and the GenAI client is created lazily (no module-level
client, no Streamlit import).
"""

from __future__ import annotations

from pathlib import Path

from mockup_generator.generation.common import (
    ALLOWED_EXT,
    MAX_SIDE,
    generate_with_retries,
    load_images_from_folder,
    part_from_pil,
    save_first_image_part,
)
from PIL import Image

MODEL_NAME = "gemini-3-pro-image-preview"
ASPECT_RATIO = "1:1"
RESOLUTION = "4K"


def _generate(contents):
    return generate_with_retries(
        MODEL_NAME, contents, aspect_ratio=ASPECT_RATIO, resolution=RESOLUTION
    )


def generate_image_for_product(
    product_dir: Path, prompt: str, out_dir: Path, process_image_sep: bool = False
):
    product_id = product_dir.name
    images = load_images_from_folder(product_dir, limit=14)  # load more for individual processing
    if not images:
        print(f"No images in {product_dir}")
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    if process_image_sep:
        # Process each image individually with suffix A, B, C, ...
        for idx, im in enumerate(images):
            suffix = chr(ord("A") + idx)
            contents = [prompt, part_from_pil(im)]

            print(f"Generating for {product_id}_{suffix} using 1 ref image")
            try:
                response = _generate(contents)
            except Exception:
                continue

            save_path = out_dir / f"{product_id}_{suffix}.png"
            if save_first_image_part(response, save_path):
                print(f"Saved {save_path}")
            else:
                print(f"No image returned for {product_id}_{suffix}")
    else:
        # Default: use first few images together
        image_parts = [part_from_pil(im) for im in images]
        contents = [prompt, *image_parts[:14]]
        print(f"Generating for {product_id} using {len(images[:14])} ref image(s)")
        try:
            response = _generate(contents)
        except Exception:
            if len(image_parts) > 1:
                contents = [prompt, image_parts[0]]
                response = _generate(contents)
            else:
                raise

        save_path = out_dir / f"{product_id}.png"
        if save_first_image_part(response, save_path):
            print(f"Saved {save_path}")
        else:
            print(f"No image returned for {product_id}")


def output_exists(out_dir: Path, product_id: str) -> bool:
    for ext in (".png", ".jpeg", ".jpg"):
        if (out_dir / f"{product_id}{ext}").exists():
            return True
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if (out_dir / f"{product_id}_{letter}.png").exists():
            return True
    return False


def refine_only_folder(input_folder: Path, output_folder: Path, prompt: str):
    """Refine every image in input_folder, saving to output_folder with same name."""
    output_folder.mkdir(parents=True, exist_ok=True)
    for p in sorted(input_folder.iterdir()):
        if p.is_file() and p.suffix.lower() in ALLOWED_EXT and not p.name.startswith("."):
            try:
                im = Image.open(p).convert("RGB")
                im.thumbnail((MAX_SIDE, MAX_SIDE))
                contents = [prompt, part_from_pil(im)]
                print(f"Refining {p.name}...")

                response = _generate(contents)

                save_path = output_folder / p.name
                if save_first_image_part(response, save_path):
                    print(f"Saved refined {save_path}")
                else:
                    print(f"No image returned for {p.name}")
            except Exception as e:
                print(f"Failed to process {p}: {e}")
