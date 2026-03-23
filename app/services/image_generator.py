from __future__ import annotations

import os
import re
import time
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

from app.config import get as cfg_get
from app.pipeline.retry import with_retry
from app.utils.log import get_logger

log = get_logger("kaidan.image")

AIRFORCE_URL = "https://api.airforce/v1/images/generations"

# Gemini client (shared with text_processor)
_gemini_client = None


def _get_gemini():
    global _gemini_client
    if _gemini_client is None:
        from google import genai
        _gemini_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY", ""))
    return _gemini_client


def extract_scene_prompts(
    text: str, title: str, num_scenes: int = 3, model: str | None = None
) -> list[str]:
    """Use Gemini to generate image prompts from story text."""
    client = _get_gemini()
    model_name = model or cfg_get("gemini_model")

    prompt = (
        f"以下の怪談「{title}」の内容から、{num_scenes}個の重要なシーンを抽出し、"
        f"それぞれ英語の画像生成プロンプトにしてください。\n"
        f"・1行に1プロンプト\n"
        f"・番号やマーカーは不要\n"
        f"・プロンプトのみ出力\n\n{text[:2000]}"
    )
    try:
        response = client.models.generate_content(model=model_name, contents=prompt)
        lines = [ln.strip() for ln in (response.text or "").strip().split("\n") if ln.strip()]
        # Remove numbered prefixes
        cleaned = []
        for ln in lines:
            ln = re.sub(r"^\d+[\.\)]\s*", "", ln)
            if len(ln) > 10:
                cleaned.append(ln)
        return cleaned[:num_scenes]
    except Exception as e:
        log.warning("Gemini prompt generation failed: %s", e)
        return [f"dark Japanese horror scene, {title}"] * num_scenes


@with_retry(max_attempts=2, base_delay=30.0)
def generate_image_ai(prompt: str, model: str | None = None, size: str | None = None) -> bytes:
    """Generate an image using AirForce API."""
    img_model = model or cfg_get("image_model")
    img_size = size or cfg_get("image_size")
    style = cfg_get("image_style")

    full_prompt = f"{prompt}, {style}"

    r = requests.post(
        AIRFORCE_URL,
        json={"model": img_model, "prompt": full_prompt, "size": img_size},
        timeout=120,
    )
    r.raise_for_status()
    data = r.json()

    if data.get("data"):
        item = data["data"][0]
        if "url" in item and item["url"]:
            img_r = requests.get(item["url"], timeout=60)
            if img_r.status_code == 200 and len(img_r.content) > 1000:
                return img_r.content
        elif "b64_json" in item:
            import base64
            return base64.b64decode(item["b64_json"])

    raise RuntimeError("Image generation returned empty data")


def generate_fallback_image(width: int = 1792, height: int = 1024) -> bytes:
    """Generate a simple dark gradient background as fallback."""
    img = Image.new("RGB", (width, height), (10, 10, 20))
    draw = ImageDraw.Draw(img)
    for y in range(height):
        r = int(10 + (y / height) * 15)
        g = int(10 + (y / height) * 10)
        b = int(20 + (y / height) * 20)
        draw.line([(0, y), (width, y)], fill=(r, g, b))

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def create_title_card(title: str, width: int = 1792, height: int = 1024) -> bytes:
    """Create a title card image."""
    img = Image.new("RGB", (width, height), (5, 5, 15))
    draw = ImageDraw.Draw(img)

    # Try to find a CJK font
    font = None
    font_paths = [
        "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
    ]
    for fp in font_paths:
        if Path(fp).exists():
            try:
                font = ImageFont.truetype(fp, 72)
                break
            except Exception:
                continue

    if font is None:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), title, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (width - tw) // 2
    y = (height - th) // 2
    draw.text((x, y), title, fill=(200, 200, 220), font=font)

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def generate_images_for_story(
    text: str, title: str, output_dir: Path, progress_callback=None
) -> list[Path]:
    """Generate all images for a story."""
    num_scenes = cfg_get("num_scenes")
    rate_limit = cfg_get("image_rate_limit")

    # Title card
    title_path = output_dir / "title_card.png"
    title_path.write_bytes(create_title_card(title))
    image_paths = [title_path]

    # Scene prompts
    prompts = extract_scene_prompts(text, title, num_scenes)

    for i, prompt in enumerate(prompts):
        if progress_callback:
            progress_callback(i + 1, len(prompts) + 1)  # +1 for title card
        log.info("AI画像生成中 (%d/%d): %s", i + 1, len(prompts), prompt[:60])
        img_path = output_dir / f"scene_{i:03d}.png"

        try:
            img_data = generate_image_ai(prompt)
            img_path.write_bytes(img_data)
        except Exception as e:
            log.warning("画像生成失敗、フォールバック使用: %s", e)
            img_path.write_bytes(generate_fallback_image())

        image_paths.append(img_path)

        if i < len(prompts) - 1:
            time.sleep(rate_limit)

    return image_paths
