"""YouTube thumbnail generation with LLM-generated clickbait phrase.

Strategy:
  - Pick the most dramatic scene image (default: scene_001) as background
  - Apply dramatic darkening + vignette
  - Overlay title (large, outlined) + LLM-generated hook/catch phrase (medium)
  - Output 1280x720 (16:9) or 720x1280 (9:16) PNG

CTR-optimized: hook phrase is designed to tease without spoiling.
"""
from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from app.config import get as cfg_get
from app.services.clients import get_gemini_text
from app.services.image_generator import (
    _draw_text_with_outline,
    _find_cjk_font,
    _wrap_title,
)
from app.utils.log import get_logger

log = get_logger("kaidan.thumbnail")


def generate_thumbnail_phrase(story_title: str, full_text: str) -> str | None:
    """LLM-generate a short clickbait phrase for thumbnail overlay.

    Different from title and hook — optimized for BIG BOLD text readable at
    small sizes (YouTube thumbnail). 10-18 chars.
    """
    prompt = (
        f"以下は怪談「{story_title}」の本文です。\n"
        f"YouTube サムネイルに **1行** で載せる煽り文言を作ってください。\n\n"
        f"制約:\n"
        f"・**10-18文字** の短くインパクトのある文\n"
        f"・ネタバレしない、続きが気になるフック\n"
        f"・小さく表示しても読める簡潔さ\n"
        f"・絵文字 1個まで (末尾推奨)、必須ではない\n"
        f"・語尾は体言止め/余韻 (例: 「真夜中の足音…」「誰が来たのか」)\n"
        f"・出力は煽り文のみ。説明・マーク不要\n\n"
        f"例: 「消えた妹の謎」「3人が見たもの」「誰が呼んでいる？」\n\n"
        f"本文:\n{full_text[:2000]}"
    )
    try:
        client = get_gemini_text()
        model_name = cfg_get("gemini_model") or "gemini-2.5-flash-lite"
        response = client.models.generate_content(model=model_name, contents=prompt)
        text = (response.text or "").strip()
        text = re.sub(r"^```[\w]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.splitlines()[0] if text else ""
        text = text.strip().strip("「」\"'")
        if 4 <= len(text) <= 25:
            return text
        log.warning("thumbnail phrase length out of bounds: %s", text[:40])
        return None
    except Exception as e:
        log.warning("thumbnail phrase gen failed: %s", e)
        return None


def create_thumbnail(
    title: str,
    bg_image_path: Path,
    output_path: Path,
    phrase: str | None = None,
    width: int = 1280,
    height: int = 720,
) -> Path:
    """Create a YouTube thumbnail PNG.

    Args:
        title: story title for large text overlay
        bg_image_path: source image (scene image or title card)
        output_path: where to save the thumbnail
        phrase: optional clickbait phrase for medium-sized subtitle
        width/height: output size (YouTube prefers 1280x720)
    """
    # --- Background ---
    bg = Image.open(bg_image_path).convert("RGB")
    bg = bg.resize((width, height), Image.LANCZOS)

    # Darken + slight desaturate so text pops
    bg = ImageEnhance.Brightness(bg).enhance(0.4)
    bg = ImageEnhance.Color(bg).enhance(0.5)
    # Minimal blur for cinematic feel (less than title_card for thumbnail clarity)
    bg = bg.filter(ImageFilter.GaussianBlur(radius=1.5))

    # Vignette (darken edges)
    import numpy as np
    cx, cy = width / 2, height / 2
    max_dist = np.sqrt(cx ** 2 + cy ** 2)
    y_coords, x_coords = np.mgrid[0:height, 0:width]
    dist = np.sqrt((x_coords - cx) ** 2 + (y_coords - cy) ** 2)
    ratio = dist / max_dist
    darken = np.clip(1.0 - ratio * ratio * 0.8, 0, 1).astype(np.float32)
    bg_arr = np.array(bg, dtype=np.float32)
    bg_arr *= darken[:, :, np.newaxis]
    bg = Image.fromarray(np.clip(bg_arr, 0, 255).astype(np.uint8))

    draw = ImageDraw.Draw(bg)

    # --- Layout ---
    # Title at top/center, larger. Phrase below (if provided).
    title_lines = _wrap_title(title, max_chars_per_line=8)

    # Font size tuned for readability at YouTube thumbnail sizes
    title_font_size = int(height * 0.18)
    if len(title_lines) >= 2:
        title_font_size = int(height * 0.14)
    title_font = _find_cjk_font(title_font_size, use_koin=True) or _find_cjk_font(title_font_size)

    phrase_font_size = int(height * 0.08)
    phrase_font = _find_cjk_font(phrase_font_size, use_koin=True) or _find_cjk_font(phrase_font_size)

    # --- Draw title (upper half, center) ---
    if title_font:
        line_spacing = int(title_font_size * 0.15)
        line_heights = []
        line_widths = []
        line_offsets_x = []
        line_offsets_y = []
        for line in title_lines:
            bbox = draw.textbbox((0, 0), line, font=title_font)
            line_widths.append(bbox[2] - bbox[0])
            line_heights.append(bbox[3] - bbox[1])
            line_offsets_x.append(bbox[0])
            line_offsets_y.append(bbox[1])
        total_title_h = sum(line_heights) + line_spacing * (len(title_lines) - 1)
        # Title centered vertically, slightly upper
        title_start_y = int(height * 0.1) if phrase else (height - total_title_h) // 2

        current_y = title_start_y
        outline_w = max(5, title_font_size // 15)
        for i, line in enumerate(title_lines):
            x = (width - line_widths[i]) // 2 - line_offsets_x[i]
            y = current_y - line_offsets_y[i]
            _draw_text_with_outline(
                draw, (x, y), line, title_font,
                fill=(255, 230, 50),   # bright yellow for kaidan vibe
                outline_fill=(0, 0, 0),
                outline_width=outline_w,
            )
            current_y += line_heights[i] + line_spacing

    # --- Draw phrase (lower area) ---
    if phrase and phrase_font:
        bbox = draw.textbbox((0, 0), phrase, font=phrase_font)
        pw = bbox[2] - bbox[0]
        ph = bbox[3] - bbox[1]
        px = (width - pw) // 2 - bbox[0]
        py = int(height * 0.72) - bbox[1]
        outline_w = max(3, phrase_font_size // 14)
        _draw_text_with_outline(
            draw, (px, py), phrase, phrase_font,
            fill=(255, 80, 80),        # red accent for drama
            outline_fill=(0, 0, 0),
            outline_width=outline_w,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    bg.save(output_path, format="PNG", quality=95)
    return output_path


def generate_story_thumbnail(
    story_title: str, raw_text: str, images_dir: Path, output_path: Path,
) -> Path | None:
    """High-level: pick a scene image, generate phrase, build thumbnail.

    Returns the output path or None on failure.
    """
    # Pick the most dramatic scene: scene_001 preferred (mid-story climax),
    # fallback to scene_000 or title_card.
    candidates = [
        images_dir / "scene_001.png",
        images_dir / "scene_000.png",
        images_dir / "scene_002.png",
        images_dir / "000_title_card.png",
    ]
    bg = next((p for p in candidates if p.exists()), None)
    if not bg:
        log.warning("no usable background image for thumbnail")
        return None

    phrase = generate_thumbnail_phrase(story_title, raw_text)
    try:
        return create_thumbnail(story_title, bg, output_path, phrase=phrase)
    except Exception as e:
        log.warning("thumbnail creation failed: %s", e)
        return None
