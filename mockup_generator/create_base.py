import os
from pathlib import Path
from io import BytesIO
from PIL import Image

from google import genai
from google.genai import types
from dotenv import load_dotenv
from google.genai import errors
import time
from random import random
from mockup_generator.prompt import *

load_dotenv()

MAX_SIDE = 1024

API_KEY = os.getenv("GOOGLE_API_KEY")
if not API_KEY:
    raise RuntimeError("GOOGLE_API_KEY is not set")

client = genai.Client(api_key=API_KEY)

ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp"}
MODEL_NAME = "gemini-2.5-flash-image"
ASPECT_RATIO = "1:1"

def part_from_pil(im: Image.Image, fmt: str = "JPEG", quality: int = 90):
    buf = BytesIO()
    im.save(buf, format=fmt, quality=quality)
    return types.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg")

def load_images_from_folder(folder: Path, max_side: int = MAX_SIDE, limit: int = 2):
    imgs = []
    for p in sorted(folder.iterdir()):
        if p.is_file() and p.suffix.lower() in ALLOWED_EXT and not p.name.startswith("."):
            try:
                im = Image.open(p).convert("RGB")
                im.thumbnail((max_side, max_side))
                imgs.append(im)
                if len(imgs) >= limit:
                    break
            except Exception:
                print(f"Skip unreadable image: {p}")
    return imgs

def save_first_image_part(response, save_path: Path):
    for part in response.candidates[0].content.parts:
        if getattr(part, "inline_data", None):
            img = Image.open(BytesIO(part.inline_data.data))
            img.save(save_path)
            return True
    return False

def generate_with_retries(model_name: str, contents, max_attempts: int = 5):
    wait = 8
    for attempt in range(1, max_attempts + 1):
        try:
            return client.models.generate_content(
                model=model_name,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_modalities=["Image"],
                    image_config=types.ImageConfig(
                        aspect_ratio=ASPECT_RATIO
                    ),
                ),
            )
        except errors.ClientError as e:
            if getattr(e, "status_code", None) == 429 and attempt < max_attempts:
                time.sleep(wait)
                wait = min(wait * 2, 60)
                continue
            raise
        except errors.ServerError:
            if attempt < max_attempts:
                time.sleep(int(wait * (1 + random())))
                wait = min(wait * 2, 60)
                continue
            raise

def generate_image_for_product(product_dir: Path, prompt: str, out_dir: Path, process_image_sep: bool = False):
    product_id = product_dir.name
    images = load_images_from_folder(product_dir, limit=10)  # load more for individual processing
    if not images:
        print(f"No images in {product_dir}")
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    prompt_with_ref = prompt.replace("[UPLOADED KURTI IMAGE]", "the provided kurti image")

    if process_image_sep:
        # Process each image individually with suffix A, B, C, ...
        for idx, im in enumerate(images):
            suffix = chr(ord("A") + idx)
            image_part = part_from_pil(im)
            contents = [prompt_with_ref, image_part]

            print(f"Generating for {product_id}_{suffix} using 1 ref image")
            try:
                response = generate_with_retries(MODEL_NAME, contents)
            except Exception:
                continue

            save_path = out_dir / f"{product_id}_{suffix}.png"
            ok = save_first_image_part(response, save_path)
            if ok:
                print(f"Saved {save_path}")
            else:
                print(f"No image returned for {product_id}_{suffix}")
    else:
        # Default: use first few images together
        image_parts = [part_from_pil(im) for im in images]
        contents = [prompt_with_ref, *image_parts[:3]]
        print(f"Generating for {product_id} using {len(images[:3])} ref image(s)")
        try:
            response = generate_with_retries(MODEL_NAME, contents)
        except Exception:
            if len(image_parts) > 1:
                contents = [prompt_with_ref, image_parts[0]]
                response = generate_with_retries(MODEL_NAME, contents)
            else:
                raise

        save_path = out_dir / f"{product_id}.png"
        ok = save_first_image_part(response, save_path)
        if ok:
            print(f"Saved {save_path}")
        else:
            print(f"No image returned for {product_id}")

def output_exists(out_dir: Path, product_id: str) -> bool:
    # Check base outputs
    for ext in (".png", ".jpeg", ".jpg"):
        if (out_dir / f"{product_id}{ext}").exists():
            return True
    # Check suffixed outputs like A, B, C...
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if (out_dir / f"{product_id}_{letter}.png").exists():
            return True
    return False

def refine_only_folder(input_folder: Path, output_folder: Path, prompt: str):
    """
    Refine every image in the input_folder and save it to output_folder
    with the same filename.
    """
    output_folder.mkdir(parents=True, exist_ok=True)
    for p in sorted(input_folder.iterdir()):
        if p.is_file() and p.suffix.lower() in ALLOWED_EXT and not p.name.startswith("."):
            try:
                im = Image.open(p).convert("RGB")
                im.thumbnail((MAX_SIDE, MAX_SIDE))
                image_part = part_from_pil(im)

                contents = [prompt, image_part]
                print(f"Refining {p.name}...")

                response = generate_with_retries(MODEL_NAME, contents)

                save_path = output_folder / p.name
                ok = save_first_image_part(response, save_path)
                if ok:
                    print(f"Saved refined {save_path}")
                else:
                    print(f"No image returned for {p.name}")
            except Exception as e:
                print(f"Failed to process {p}: {e}")

"""
if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    input_root = Path("Saree_in")
    output_root = Path("Saree_out")

    user_prompt = SAREE_PROMPT
    process_image_sep = False  # set flag here

    refine_only = False
    if refine_only:
        refine_only_folder(input_root, output_root, user_prompt)
    else:
        for product_dir in sorted([p for p in input_root.iterdir() if p.is_dir()]):
            pid = product_dir.name
            if output_exists(output_root, pid):
                print(f"Skipping {pid} because an output image already exists")
                continue
            generate_image_for_product(product_dir, user_prompt, output_root, process_image_sep)
"""