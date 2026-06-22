import io
import time
from pathlib import Path
from io import BytesIO
from PIL import Image

from google.genai import types

from mockup_generator.generation.common import get_genai_client, part_from_pil
from mockup_generator.prompts.defaults import VIDEO_PROMPT

# Configuration
MAX_SIDE = 1024
ASPECT_RATIO = "9:16"
RESOLUTION = "720p"
VEO_MODEL = "veo-3.1-generate-preview"
#NEGATIVE_PROMPT = (
#    "floating pieces, detached fabric, fragments, torn cloth, broken geometry, "
#    "jerky movement, jerky camera movement, fabric merging, missing dupatta, cartoon effects, distorted clothing layers"
#)
NEGATIVE_PROMPT = ("morphing faces, melting bodies, changing expressions, cartoon, illustration, drawing, painting, fast movement, jerky camera, blurry, distorted text, bad spelling, extra limbs, warm summer lighting, high contrast.")
NUM_VIDEOS = 1
DURATION_SEC = 4
FLASH_MODEL = "gemini-2.5-flash-image"
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}
PERSON_GENERATION = "allow_adult"


def refine_and_create_video(input_folder: Path, prompt: str, output_folder: Path, generate_image: bool = False):
    """Optionally refine input images with Gemini Flash and create videos using VEO."""
    output_folder.mkdir(parents=True, exist_ok=True)

    input_images = [
        p for p in sorted(input_folder.iterdir())
        if p.is_file() and p.suffix.lower() in ALLOWED_EXT and not p.name.startswith(".")
    ]
    if not input_images:
        raise RuntimeError("No valid input images found")

    client = get_genai_client()
    for img_path in input_images:
        if generate_image:
            im = Image.open(img_path).convert("RGB")
            im.thumbnail((MAX_SIDE, MAX_SIDE))
            image_part = part_from_pil(im)

            print(f"Generating refined image for {img_path.name} ...")
            imagen = client.models.generate_content(
                model=FLASH_MODEL,
                contents=[prompt, image_part],
                config=types.GenerateContentConfig(
                    response_modalities=["Image"],
                    image_config=types.ImageConfig(aspect_ratio="1:1"),
                ),
            )
            refined = None
            for part in imagen.candidates[0].content.parts:
                if getattr(part, "inline_data", None):
                    refined = Image.open(BytesIO(part.inline_data.data))
            if refined is None:
                raise RuntimeError("Image refinement failed")

            refined_name = img_path.with_suffix(".jpg").name
            refined_path = output_folder / refined_name
            refined.save(refined_path, format="JPEG", quality=90)
            generate_video(prompt, refined_path, output_folder, refined_name)
        else:
            generate_video(prompt, img_path, output_folder, img_path.name)


def generate_video(prompt: str, image_path: Path, output_folder: Path, filename: str):
    """Create a short cinematic video using the VEO model."""
    im = Image.open(image_path).convert("RGB")
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=90)
    image_bytes = buf.getvalue()

    client = get_genai_client()
    print(f"Submitting video job for {filename} ...")
    operation = client.models.generate_videos(
        model=VEO_MODEL,
        prompt=prompt,
        image=types.Image(image_bytes=image_bytes, mime_type="image/jpeg"),
        config=types.GenerateVideosConfig(
            aspect_ratio=ASPECT_RATIO,
            resolution=RESOLUTION,
            duration_seconds=DURATION_SEC,
            number_of_videos=NUM_VIDEOS,
            negative_prompt=NEGATIVE_PROMPT
        ),
    )

    while not operation.done:
        print("Waiting for video generation to complete...")
        time.sleep(10)
        operation = client.operations.get(operation)
    
    result = getattr(operation, "result", None) or getattr(operation, "response", None)
    if not result or not getattr(result, "generated_videos", None):
        raise RuntimeError(f"No videos returned. name={operation.name}, error={operation.error}")

    video = operation.response.generated_videos[0]
    output_path = output_folder / Path(filename).with_suffix(".mp4").name
    client.files.download(file=video.video)
    video.video.save(output_path)
    print(f"Saved video to {output_path}")


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    input_root = Path("try_in")
    output_root = Path("try_out")

    user_prompt = VIDEO_PROMPT
    refine_and_create_video(input_root, user_prompt, output_root, generate_image=False)
