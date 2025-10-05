import os
from pathlib import Path
from io import BytesIO
import io
from PIL import Image

from google import genai
from google.genai import types
from dotenv import load_dotenv
import time
from prompt import VIDEO_PROMPT

load_dotenv()

MAX_SIDE = 1024
aspect_ratio = "9:16"
resolution = "720p"
VEO_MODEL = "veo-3.0-fast-generate-001"
negative_prompt = "floating pieces, detached fabric, fragments, torn cloth, broken geometry, jerky movement, fabric merging, missing dupatta, cartoon effects, distorted clothing layers"
person_generation = "allow_adult"
number_of_videos = 1
duration_seconds = 8
MODEL_NAME = "gemini-2.5-flash-image"
ASPECT_RATIO = "1:1"
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}

API_KEY = os.getenv("GOOGLE_API_KEY")
if not API_KEY:
    raise RuntimeError("GOOGLE_API_KEY is not set")

client = genai.Client(api_key=API_KEY)

def part_from_pil(im: Image.Image, fmt: str = "JPEG", quality: int = 90):
    buf = BytesIO()
    im.save(buf, format=fmt, quality=quality)
    return types.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg")

def refine_and_create_video(input_folder: Path, prompt: str, output_folder: Path, generate_image: bool = False):
    """
    Refine every image in the input_folder and save it to output_folder
    with the same filename.
    """
    for p in sorted(input_folder.iterdir()):
        if p.is_file() and p.suffix.lower() in ALLOWED_EXT and not p.name.startswith("."):
            if generate_image:
                im = Image.open(p).convert("RGB")
                im.thumbnail((MAX_SIDE, MAX_SIDE))
                image_part = part_from_pil(im)

                contents = [prompt, image_part]
                print(f"Creating video for {p.name}...")
                imagen = client.models.generate_content(
                    model=MODEL_NAME,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        response_modalities=["Image"],
                        image_config=types.ImageConfig(
                            aspect_ratio=ASPECT_RATIO
                        ),
                    ),
                )
                for part in imagen.candidates[0].content.parts:
                    if getattr(part, "inline_data", None):
                        img = Image.open(BytesIO(part.inline_data.data))
                img.save(output_folder / (p.name.split('.')[0] + ".jpg"))
                generate_video(prompt, output_folder, p.name)
    if not generate_image:
        print("Generating video for BC251476_3.jpg")
        generate_video(prompt, output_folder, "BC251476_3.jpg")


def generate_video(prompt: str, output_folder: Path, p_name: str):
    
    im = Image.open(output_folder / (p_name.split('.')[0] + ".jpg"))
    image_bytes_io = io.BytesIO()
    im.save(image_bytes_io, format=im.format)
    image_bytes = image_bytes_io.getvalue()

    
    operation = client.models.generate_videos(
        model=VEO_MODEL,
        prompt=prompt,
        image=types.Image(image_bytes=image_bytes, mime_type=im.format),
        config=types.GenerateVideosConfig(
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            negative_prompt=negative_prompt,
            person_generation=person_generation,
            number_of_videos=number_of_videos,
            duration_seconds=duration_seconds,
        ),
    )
    while not operation.done:
        time.sleep(20)
        operation = client.operations.get(operation)
    
    video = operation.response.generated_videos[0]
    output_filename = Path(p_name).with_suffix('.mp4').name
    output_path = output_folder / output_filename   
    for n, generated_video in enumerate(operation.result.generated_videos):
        client.files.download(file=generated_video.video)
        generated_video.video.save(output_path) # Saves the video(s)
    print(f"Saved video for {p_name}")

if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    input_root = Path("try_in")
    output_root = Path("try_out")

    user_prompt = VIDEO_PROMPT
    refine_and_create_video(input_root, user_prompt, output_root, generate_image=False)
    
        
        
