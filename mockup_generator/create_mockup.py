# requirements: openai>=1.40.0 pillow python-dotenv
# env: OPENAI_API_KEY in .env

import os
import base64
from io import BytesIO
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from openai import OpenAI
from PIL import Image, PngImagePlugin, ImageOps

from prompt_config import MALE_KURTA_PROMPT

load_dotenv()

TARGET_W, TARGET_H = 1080, 1920

def to_story_canvas(im: Image.Image) -> Image.Image:
    # keep aspect, upscale by width to 1080 using LANCZOS
    w, h = im.size
    new_h = int(h * (TARGET_W / w))
    im_resized = im.resize((TARGET_W, new_h), Image.LANCZOS)

    # pad to 1080x1920 on a white canvas without cropping head or toe
    pad_top = max((TARGET_H - new_h) // 2, 0)
    pad_bottom = max(TARGET_H - new_h - pad_top, 0)
    im_padded = ImageOps.expand(
        im_resized,
        border=(0, pad_top, 0, pad_bottom),
        fill="white",
    )
    return im_padded

def create_mockup_image(
    input_path: str,
    base_prompt: str,
    prompt_extra: str | None = None,
    output_path: str = "polished.png",
    mask_path: str | None = None,
    size: str = "1024x1536",  # must be one of 1024x1024, 1024x1536, 1536x1024, or auto
) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    client = OpenAI(api_key=api_key)
    model_name = "gpt-image-1"

    text_prompt = base_prompt if not prompt_extra else base_prompt + "\nAdditional Notes\n" + prompt_extra

    with open(input_path, "rb") as img_f:
        params = dict(
            model=model_name,
            image=img_f,
            prompt=text_prompt,
            size=size,
        )
        if mask_path:
            with open(mask_path, "rb") as m:
                params["mask"] = m
                resp = client.images.edit(**params)
        else:
            resp = client.images.edit(**params)

    b64 = resp.data[0].b64_json
    img_bytes = base64.b64decode(b64)

    meta = {
        "referenced_images": Path(input_path).name,
        "garment_details_prioritized": "fabric texture, border motifs, embroidery, color tones, buttons below chest, pajama white",
        "generation_datetime_utc": datetime.utcnow().isoformat() + "Z",
        "notes": prompt_extra or "",
    }

    pnginfo = PngImagePlugin.PngInfo()
    for k, v in meta.items():
        pnginfo.add_text(k, str(v))

    out_path = Path(output_path)
    with Image.open(BytesIO(img_bytes)) as im:
        final = to_story_canvas(im)  # LANCZOS resize and white padding to 1080x1920
        final.save(out_path, format="PNG", pnginfo=pnginfo)

    return str(out_path.resolve())

if __name__ == "__main__":
    result = create_mockup_image(
        input_path="BC25672.jpeg",
        base_prompt=MALE_KURTA_PROMPT,
        prompt_extra="Ensure calm confident gaze and relaxed posture. Maintain natural skin texture and subtle soft studio lighting.",
        output_path="polished.png",
        mask_path=None,
        size="1024x1536",  # portrait render from API
    )
    print("Saved to", result)
