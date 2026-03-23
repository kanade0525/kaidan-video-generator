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
        f"以下の怪談「{title}」の内容を読み、怪談朗読動画の背景画像として使う{num_scenes}枚の画像生成プロンプトを英語で作ってください。\n\n"
        f"重要な要件:\n"
        f"・物語の「場所」や「状況」を具体的に描写すること。人物ではなく風景・空間・物体を中心に\n"
        f"・各画像は異なるシーン・異なる構図にすること。似た画像は絶対に避ける\n"
        f"  - 1枚目: 物語の舞台となる場所の全景（wide establishing shot）\n"
        f"  - 2枚目: 物語の転換点や恐怖の核心となるモチーフのクローズアップ\n"
        f"  - 3枚目以降: クライマックスの情景や余韻を残すシーン\n"
        f"・写実的でダークな描写。ホラー映画のワンシーンのように\n"
        f"・各プロンプトは50〜80語程度で具体的に\n"
        f"・構図（wide shot, close-up, overhead, dutch angle等）を必ず含める\n"
        f"・照明（moonlight, flickering light, backlit silhouette等）を必ず含める\n"
        f"・人物を描く場合は後ろ姿やシルエットにする\n"
        f"・テキストや文字は絶対に含めない\n"
        f"・1行に1プロンプト、番号やマーカーは不要\n\n"
        f"物語:\n{text[:2000]}"
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
    negative_prompt = (
        "text, letters, words, writing, captions, watermark, signature, logo, "
        "title, subtitle, label, UI, numbers, symbols, typography, font, "
        "anime, cartoon, illustration, drawing, painting, sketch, "
        "bright colors, vibrant, cheerful, happy"
    )

    r = requests.post(
        AIRFORCE_URL,
        json={
            "model": img_model,
            "prompt": full_prompt,
            "negative_prompt": negative_prompt,
            "size": img_size,
        },
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


def _find_cjk_font(size: int) -> ImageFont.FreeTypeFont | None:
    """Find a CJK font on the system."""
    font_paths = [
        "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    ]
    for fp in font_paths:
        if Path(fp).exists():
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                continue
    return None


def create_title_card(title: str, width: int = 1792, height: int = 1024) -> bytes:
    """Create a horror-themed title card image."""
    import math
    import random

    img = Image.new("RGB", (width, height), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Radial dark gradient - darkest at edges, slightly lighter at center
    cx, cy = width // 2, height // 2
    max_dist = math.sqrt(cx**2 + cy**2)
    for y in range(height):
        for x in range(0, width, 2):  # Step 2 for performance
            dist = math.sqrt((x - cx) ** 2 + (y - cy) ** 2)
            ratio = dist / max_dist
            r = int(25 * (1 - ratio))
            g = int(5 * (1 - ratio))
            b = int(5 * (1 - ratio))
            draw.rectangle([x, y, x + 1, y], fill=(r, g, b))

    # Heavy noise/grain
    random.seed(hash(title))
    for _ in range(8000):
        x = random.randint(0, width - 1)
        y = random.randint(0, height - 1)
        v = random.randint(3, 20)
        r_noise = v + random.randint(0, 10)
        draw.point((x, y), fill=(r_noise, v // 2, v // 2))

    # Blood drip-like streaks from top
    for _ in range(15):
        sx = random.randint(width // 4, 3 * width // 4)
        streak_len = random.randint(100, 400)
        for sy in range(0, streak_len):
            alpha = max(0, 40 - sy // 5)
            wx = sx + random.randint(-1, 1)
            draw.point((wx, sy), fill=(alpha, 0, 0))

    # Title text - as large as possible
    # Calculate max font size that fits width with padding
    padding = width // 8
    available_width = width - padding * 2
    font_size = available_width // max(len(title), 1)
    font_size = min(font_size, 280)
    font_size = max(font_size, 100)
    font = _find_cjk_font(font_size)
    if font is None:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), title, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (width - tw) // 2
    y = (height - th) // 2

    # Deep shadow layers
    for offset in range(12, 0, -1):
        intensity = int(15 + offset * 3)
        draw.text(
            (x + offset, y + offset), title,
            fill=(intensity, 0, 0), font=font,
        )

    # Outer glow - dark red spread
    for dx, dy in [(-3, -3), (3, -3), (-3, 3), (3, 3), (-2, 0), (2, 0), (0, -2), (0, 2)]:
        draw.text((x + dx, y + dy), title, fill=(100, 5, 5), font=font)

    # Inner glow
    for dx, dy in [(-1, -1), (1, -1), (-1, 1), (1, 1)]:
        draw.text((x + dx, y + dy), title, fill=(160, 15, 15), font=font)

    # Main text - crimson red
    draw.text((x, y), title, fill=(220, 20, 20), font=font)

    # Highlight on top edge of text for 3D effect
    small_font = _find_cjk_font(font_size)
    if small_font:
        draw.text((x, y - 1), title, fill=(255, 60, 40), font=small_font)

    # Decorative lines
    line_w = tw + 80
    line_x = (width - line_w) // 2

    # Top decorative line with fade
    line_y = y - 50
    for i in range(line_w):
        fade = 1 - abs(i - line_w // 2) / (line_w // 2)
        c = int(130 * fade)
        draw.point((line_x + i, line_y), fill=(c, 5, 5))
        draw.point((line_x + i, line_y + 1), fill=(c // 2, 2, 2))

    # Bottom decorative line with fade
    line_y2 = y + th + 50
    for i in range(line_w):
        fade = 1 - abs(i - line_w // 2) / (line_w // 2)
        c = int(130 * fade)
        draw.point((line_x + i, line_y2), fill=(c, 5, 5))
        draw.point((line_x + i, line_y2 + 1), fill=(c // 2, 2, 2))

    # Corner ornaments
    ornament_size = 30
    for corner_x, corner_y in [(line_x, line_y), (line_x + line_w, line_y),
                                 (line_x, line_y2), (line_x + line_w, line_y2)]:
        for i in range(ornament_size):
            c = int(100 * (1 - i / ornament_size))
            draw.point((corner_x, corner_y - i + ornament_size // 2), fill=(c, 3, 3))
            draw.point((corner_x - i + ornament_size // 2, corner_y), fill=(c, 3, 3))

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
