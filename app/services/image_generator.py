from __future__ import annotations

import random
import re
import time
from io import BytesIO
from pathlib import Path

import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.config import get as cfg_get
from app.pipeline.retry import with_retry
from app.services.clients import get_gemini_image
from app.utils.log import get_logger

log = get_logger("kaidan.image")

AIRFORCE_URL = "https://api.airforce/v1/images/generations"


def extract_scene_prompts(
    text: str, title: str, num_scenes: int = 3, model: str | None = None
) -> list[str]:
    """Use Gemini to generate image prompts from story text."""
    client = get_gemini_image()
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


def _generate_title_bg_prompt(text: str, title: str) -> str:
    """Use Gemini to generate a title card background prompt based on story content."""
    fallback = (
        "dark atmospheric background, abandoned place, foggy, ominous sky, "
        "empty scene with no people, photorealistic, cinematic, extremely dark and moody, "
        "no text, no letters, no words, no writing, pure scenery only"
    )
    try:
        client = get_gemini_image()
        model_name = cfg_get("gemini_model")
        prompt = (
            f"以下の怪談「{title}」の内容を読み、タイトルカードの背景画像用プロンプトを英語で1つだけ作ってください。\n\n"
            f"要件:\n"
            f"・物語の舞台や象徴的な場所を描写（人物は入れない）\n"
            f"・ダークで不気味な雰囲気、ホラー映画のワンシーンのように\n"
            f"・50〜80語程度で具体的に\n"
            f"・テキストや文字は絶対に含めない\n"
            f"・プロンプトのみ出力、説明不要\n\n"
            f"物語:\n{text[:1000]}"
        )
        response = client.models.generate_content(model=model_name, contents=prompt)
        result = (response.text or "").strip().split("\n")[0].strip()
        if len(result) > 20:
            return result
    except Exception as e:
        log.warning("タイトル背景プロンプト生成失敗: %s", e)
    return fallback




@with_retry(max_attempts=2, base_delay=30.0)
def generate_image_ai(
    prompt: str, model: str | None = None, size: str | None = None,
    aspect_ratio: str | None = None,
) -> bytes:
    """Generate an image using Imagen or AirForce API."""
    img_model = model or cfg_get("image_model")
    style = cfg_get("image_style")
    full_prompt = f"{prompt}, {style}"

    # Use Google API if model starts with "imagen" or "gemini"
    if img_model.startswith("imagen") or img_model.startswith("gemini"):
        return _generate_imagen(full_prompt, img_model, aspect_ratio=aspect_ratio)

    # Fallback to AirForce
    return _generate_airforce(full_prompt, img_model, size)


def _generate_imagen(prompt: str, model: str, aspect_ratio: str | None = None) -> bytes:
    """Generate image using Google Imagen or Gemini Image API."""
    from google.genai import types

    client = get_gemini_image()

    aspect_ratio = aspect_ratio or cfg_get("image_aspect_ratio")
    output_mime = cfg_get("image_output_mime")
    compression = cfg_get("image_compression_quality")

    # Gemini image generation (gemini-*-image models)
    if model.startswith("gemini"):
        image_config = types.ImageConfig(
            aspectRatio=aspect_ratio,
        )
        response = client.models.generate_content(
            model=model,
            contents=f"Generate an image: {prompt}",
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"],
                imageConfig=image_config,
            ),
        )
        for part in response.candidates[0].content.parts:
            if part.inline_data:
                return part.inline_data.data
        raise RuntimeError("Gemini image generation returned no image")

    # Imagen API (imagen-* models)
    # Imagen 4 only supports: numberOfImages, aspectRatio, personGeneration, imageSize
    if model.startswith("imagen-4"):
        imagen_config = types.GenerateImagesConfig(
            numberOfImages=1,
            aspectRatio=aspect_ratio,
        )
    else:
        negative_prompt = cfg_get("image_negative_prompt")
        guidance = cfg_get("image_guidance_scale")
        seed = cfg_get("image_seed")
        enhance = cfg_get("image_enhance_prompt")
        watermark = cfg_get("image_add_watermark")

        imagen_config = types.GenerateImagesConfig(
            numberOfImages=1,
            aspectRatio=aspect_ratio,
            negativePrompt=negative_prompt,
            guidanceScale=guidance,
            enhancePrompt=enhance,
            addWatermark=watermark,
            outputMimeType=output_mime,
            outputCompressionQuality=compression,
        )
        if seed > 0:
            imagen_config.seed = seed

    response = client.models.generate_images(
        model=model,
        prompt=prompt,
        config=imagen_config,
    )
    if response.generated_images:
        return response.generated_images[0].image.image_bytes
    raise RuntimeError("Imagen returned no images")


def _generate_airforce(prompt: str, model: str, size: str | None = None) -> bytes:
    """Generate image using AirForce API."""
    img_size = size or cfg_get("image_size")
    negative_prompt = (
        "text, letters, words, writing, captions, watermark, signature, logo, "
        "title, subtitle, label, UI, numbers, symbols, typography, font, "
        "anime, cartoon, illustration, drawing, painting, sketch, "
        "bright colors, vibrant, cheerful, happy"
    )

    r = requests.post(
        AIRFORCE_URL,
        json={
            "model": model,
            "prompt": prompt,
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


def degrade_to_vhs(image_data: bytes) -> bytes:
    """Degrade a high-quality image to look like VHS/surveillance camera footage."""
    img = Image.open(BytesIO(image_data)).convert("RGB")
    w, h = img.size

    # 1. Downscale to 1/4 then upscale back (pixelation)
    small = img.resize((w // 4, h // 4), Image.BILINEAR)
    img = small.resize((w, h), Image.NEAREST)

    # 2. Slight blur (cheap lens)
    img = img.filter(ImageFilter.GaussianBlur(radius=1.2))

    # 3. Reduce color depth / desaturate
    arr = np.array(img, dtype=np.float32)
    gray = np.mean(arr, axis=2, keepdims=True)
    arr = arr * 0.4 + gray * 0.6  # Partially desaturate
    arr = np.clip(arr * 0.7 + 10, 0, 255)  # Darken + slight lift

    # 4. Add greenish/bluish tint (night vision / CCTV look)
    arr[:, :, 0] *= 0.8   # Reduce red
    arr[:, :, 1] *= 1.05   # Slight green boost
    arr[:, :, 2] *= 0.85   # Reduce blue

    # 5. Add heavy noise
    noise = np.random.normal(0, 25, arr.shape)
    arr = np.clip(arr + noise, 0, 255)

    # 6. Scan lines
    for y in range(0, h, 3):
        arr[y, :, :] *= 0.7

    # 7. Random horizontal distortion lines
    for _ in range(random.randint(3, 8)):
        y = random.randint(0, h - 4)
        shift = random.randint(-15, 15)
        thickness = random.randint(1, 3)
        for dy in range(thickness):
            if 0 <= y + dy < h:
                arr[y + dy] = np.roll(arr[y + dy], shift, axis=0)

    # 8. Vignette (dark corners)
    Y, X = np.ogrid[:h, :w]
    cx, cy = w / 2, h / 2
    dist = np.sqrt((X - cx) ** 2 + (Y - cy) ** 2)
    max_dist = np.sqrt(cx ** 2 + cy ** 2)
    vignette = 1 - (dist / max_dist) ** 2 * 0.6
    arr *= vignette[:, :, np.newaxis]

    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


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


def _find_cjk_font(size: int, use_koin: bool = False) -> ImageFont.FreeTypeFont | None:
    """Find a CJK font on the system.

    Args:
        use_koin: If True, prefer g_コミック古印体 for horror-style text.
    """
    if use_koin:
        koin_paths = [
            "fonts/Zomzi.TTF",
            "/app/fonts/Zomzi.TTF",
        ]
        for fp in koin_paths:
            if Path(fp).exists():
                try:
                    return ImageFont.truetype(fp, size)
                except Exception:
                    continue

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


def _draw_text_with_outline(
    draw: ImageDraw.Draw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int],
    outline_fill: tuple[int, int, int] = (0, 0, 0),
    outline_width: int = 6,
):
    """Draw text with thick outline for readability on busy backgrounds."""
    x, y = xy
    # Draw outline by rendering text at offsets
    for dx in range(-outline_width, outline_width + 1):
        for dy in range(-outline_width, outline_width + 1):
            if dx * dx + dy * dy <= outline_width * outline_width:
                draw.text((x + dx, y + dy), text, font=font, fill=outline_fill)
    # Draw main text
    draw.text((x, y), text, font=font, fill=fill)


def _wrap_title(title: str, max_chars_per_line: int = 8) -> list[str]:
    """Split title into multiple lines for dramatic layout."""
    if len(title) <= max_chars_per_line:
        return [title]

    lines = []
    # Try splitting at natural breakpoints
    for sep in ["の", "を", "が", "に", "で", "と", "は", "へ", "…", "、"]:
        if sep in title:
            parts = title.split(sep, 1)
            if len(parts[0]) > 0:
                lines.append(parts[0] + sep)
                remaining = parts[1]
                if len(remaining) > max_chars_per_line:
                    lines.extend(_wrap_title(remaining, max_chars_per_line))
                elif remaining:
                    lines.append(remaining)
                return lines

    # Hard wrap
    for i in range(0, len(title), max_chars_per_line):
        lines.append(title[i:i + max_chars_per_line])
    return lines


def create_title_card(
    title: str,
    width: int = 1792,
    height: int = 1024,
    bg_image_data: bytes | None = None,
    category: str = "怪談",
) -> bytes:
    """Create a cinematic horror-themed title card.

    Uses AI-generated background if provided, otherwise generates a dark procedural bg.
    Overlays title text with thick outlines, multiple lines, and dramatic layout.
    """
    from PIL import ImageEnhance, ImageFilter

    # --- Background ---
    if bg_image_data:
        bg = Image.open(BytesIO(bg_image_data)).convert("RGB")
        bg = bg.resize((width, height), Image.LANCZOS)
    else:
        bg = Image.new("RGB", (width, height), (10, 5, 5))

    # Darken and desaturate background so text pops
    bg = ImageEnhance.Brightness(bg).enhance(0.35)
    bg = ImageEnhance.Color(bg).enhance(0.4)
    # Slight blur for depth-of-field feel
    bg = bg.filter(ImageFilter.GaussianBlur(radius=3))

    draw = ImageDraw.Draw(bg)

    # Dark vignette overlay
    import math
    cx, cy = width // 2, height // 2
    max_dist = math.sqrt(cx ** 2 + cy ** 2)
    vignette = Image.new("RGB", (width, height), (0, 0, 0))
    vdraw = ImageDraw.Draw(vignette)
    for y_pos in range(0, height, 4):
        for x_pos in range(0, width, 4):
            dist = math.sqrt((x_pos - cx) ** 2 + (y_pos - cy) ** 2)
            ratio = dist / max_dist
            # Stronger at edges
            alpha = int(min(255, ratio * ratio * 300))
            vdraw.rectangle(
                [x_pos, y_pos, x_pos + 3, y_pos + 3],
                fill=(alpha, alpha, alpha),
            )
    # Blend vignette (darken edges)
    from PIL import ImageChops
    vignette_inv = Image.eval(vignette, lambda v: 255 - v)
    bg = ImageChops.multiply(bg, vignette_inv.point(lambda v: v / 255.0 * 255))

    # Top gradient overlay (darkens top 30%)
    for y_pos in range(int(height * 0.3)):
        alpha = int(180 * (1 - y_pos / (height * 0.3)))
        draw.line([(0, y_pos), (width, y_pos)], fill=(0, 0, 0))

    draw = ImageDraw.Draw(bg)

    # --- Title text ---
    # Vertical video: fewer chars per line for larger text
    is_vertical = height > width
    chars_per_line = 5 if is_vertical else 7
    lines = _wrap_title(title, max_chars_per_line=chars_per_line)

    # Calculate font size: fill most of the image
    padding_x = width // 8 if is_vertical else width // 10
    available_w = width - padding_x * 2
    # Font size based on widest line
    max_line_len = max(len(line) for line in lines)
    font_size = min(available_w // max(max_line_len, 1), height // (len(lines) + 2))
    max_font = 360 if is_vertical else 240
    font_size = min(font_size, max_font)
    font_size = max(font_size, 100 if is_vertical else 80)

    font = _find_cjk_font(font_size, use_koin=True)
    if font is None:
        font = ImageFont.load_default()

    # Measure all lines (accounting for bbox offset)
    line_heights = []
    line_widths = []
    line_offsets_x = []
    line_offsets_y = []
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_widths.append(bbox[2] - bbox[0])
        line_heights.append(bbox[3] - bbox[1])
        line_offsets_x.append(bbox[0])
        line_offsets_y.append(bbox[1])

    line_spacing = int(font_size * 0.25)
    total_text_height = sum(line_heights) + line_spacing * (len(lines) - 1)

    # Start y: center vertically
    start_y = (height - total_text_height) // 2

    # Draw each line
    outline_w = max(4, font_size // 18)
    current_y = start_y
    for i, line in enumerate(lines):
        lw = line_widths[i]
        x = (width - lw) // 2 - line_offsets_x[i]
        y = current_y - line_offsets_y[i]

        # Draw text with thick black outline + red fill
        _draw_text_with_outline(
            draw, (x, y), line, font,
            fill=(230, 20, 20),
            outline_fill=(0, 0, 0),
            outline_width=outline_w,
        )

        current_y += line_heights[i] + line_spacing

    # --- Category badge (top-right corner) ---
    badge_font = _find_cjk_font(112, use_koin=True)
    if badge_font:
        badge_text = category
        badge_bbox = draw.textbbox((0, 0), badge_text, font=badge_font)
        text_w = badge_bbox[2] - badge_bbox[0]
        text_h = badge_bbox[3] - badge_bbox[1]
        pad_x, pad_y = 16, 10
        bw = text_w + pad_x * 2
        bh = text_h + pad_y * 2
        bx = width - bw - 40
        by = 40
        # Red badge with border
        draw.rectangle([bx - 2, by - 2, bx + bw + 2, by + bh + 2], fill=(100, 5, 5))
        draw.rectangle([bx, by, bx + bw, by + bh], fill=(160, 15, 15))
        # Center text in badge
        tx = bx + (bw - text_w) // 2 - badge_bbox[0]
        ty = by + (bh - text_h) // 2 - badge_bbox[1]
        draw.text((tx, ty), badge_text, font=badge_font, fill=(255, 240, 240))

    buf = BytesIO()
    bg.save(buf, format="PNG", quality=95)
    return buf.getvalue()


def generate_images_for_story(
    text: str, title: str, output_dir: Path, category: str = "怪談",
    progress_callback=None, content_type: str = "long",
) -> list[Path]:
    """Generate all images for a story."""
    is_short = content_type == "short"
    num_scenes = cfg_get("shorts_num_scenes") if is_short else cfg_get("num_scenes")
    rate_limit = cfg_get("image_rate_limit")
    use_vhs = cfg_get("shorts_vhs_enabled") if is_short else True

    image_paths: list[Path] = []

    # Generate title card
    if is_short:
        tc_w, tc_h = 1080, 1920
    else:
        tc_w, tc_h = 1792, 1024

    title_bg_prompt = _generate_title_bg_prompt(text, title)
    title_bg_data = None
    try:
        ar = cfg_get("shorts_image_aspect_ratio") if is_short else None
        title_bg_data = generate_image_ai(title_bg_prompt, aspect_ratio=ar)
        log.info("タイトル背景画像生成成功")
    except Exception as e:
        log.warning("タイトル背景生成失敗、プロシージャル背景を使用: %s", e)

    title_path = output_dir / "000_title_card.png"
    title_path.write_bytes(
        create_title_card(title, width=tc_w, height=tc_h, bg_image_data=title_bg_data, category=category)
    )
    image_paths.append(title_path)

    if rate_limit > 0:
        time.sleep(rate_limit)

    # Scene prompts
    prompts = extract_scene_prompts(text, title, num_scenes)

    fb_w, fb_h = (1080, 1920) if is_short else (1792, 1024)

    for i, prompt in enumerate(prompts):
        if progress_callback:
            offset = 0 if is_short else 1  # +1 for title card in long-form
            progress_callback(i + offset, len(prompts) + offset)
        log.info("AI画像生成中 (%d/%d): %s", i + 1, len(prompts), prompt[:60])
        img_path = output_dir / f"scene_{i:03d}.png"

        try:
            ar = cfg_get("shorts_image_aspect_ratio") if is_short else None
            img_data = generate_image_ai(prompt, aspect_ratio=ar)
            if use_vhs:
                img_data = degrade_to_vhs(img_data)
            img_path.write_bytes(img_data)
        except Exception as e:
            log.warning("画像生成失敗、フォールバック使用: %s", e)
            img_path.write_bytes(generate_fallback_image(width=fb_w, height=fb_h))

        image_paths.append(img_path)

        if i < len(prompts) - 1:
            time.sleep(rate_limit)

    return image_paths
