from __future__ import annotations

import hashlib
import random
import re
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from app.config import get as cfg_get
from app.pipeline.retry import with_retry
from app.services import image_pool
from app.services.clients import get_gemini_image, get_gemini_text
from app.utils.log import get_logger

log = get_logger("kaidan.image")

AIRFORCE_URL = "https://api.airforce/v1/images/generations"


def _record_usage(response, model: str, purpose: str) -> None:
    """利用量を記録する。記録の失敗で生成を止めない。"""
    try:
        from app.services.usage import record_response

        record_response(response, model, purpose)
    except Exception as e:  # noqa: BLE001
        log.debug("利用量の記録をスキップ: %s", e)


def _record_image(model: str, purpose: str) -> None:
    """画像1枚の生成を記録する (枚数課金)。"""
    try:
        from app.services.usage import record_image

        record_image(model, purpose)
    except Exception as e:  # noqa: BLE001
        log.debug("利用量の記録をスキップ: %s", e)


class ImageUnavailableError(RuntimeError):
    """画像を用意できなかった。

    以前はここで真っ黒のグラデーションを返していたため、生成に失敗しても
    パイプラインが止まらず、そのまま動画化されて YouTube に投稿されていた。
    画像は動画の中身そのものなので、揃わないなら止める。
    """


@dataclass
class VisualStyleProfile:
    """A coherent visual style for one Short — drives prompt + render + post-fx.

    Each profile pulls the AI image generation toward a distinct aesthetic so
    consecutive uploads do not look interchangeable. Selected per-story via
    `pick_shorts_visual_style(title)`.
    """
    name: str
    # Inserted into the Gemini prompt instructions as the aesthetic the
    # scene/title-bg prompts should target.
    aesthetic_focus: str
    # Appended to the final image-API prompt as the rendering style suffix
    # (replaces the global `image_style` config when this profile is active).
    style_suffix: str
    # If False, skip the green/scanline VHS post-process for this profile so
    # the chosen aesthetic shows through.
    apply_vhs: bool


# Brand identity = VHS aesthetic + degraded camcorder / surveillance footage,
# applied uniformly to every Short. Diversification across uploads happens in
# WHAT the camera is recording (corridor / outdoor / indoor / object / figure)
# rather than HOW it is rendered. Like Ghibli: many stories, one look.
_VHS_STYLE_SUFFIX = (
    "found footage style, low quality home video camera capture, "
    "VHS tape quality, scan lines, colour bleed, grainy, washed out colors, "
    "slight tape distortion, low resolution, photorealistic"
)


SHORTS_VISUAL_STYLES: list[VisualStyleProfile] = [
    VisualStyleProfile(
        name="vhs_corridor_security",
        aesthetic_focus=(
            "narrow dark Japanese corridor, hallway, or stairwell viewed through "
            "an old fixed CCTV camera — could be a school, office, hospital, "
            "or apartment building"
        ),
        style_suffix=_VHS_STYLE_SUFFIX,
        apply_vhs=True,
    ),
    VisualStyleProfile(
        name="vhs_outdoor_rural_night",
        aesthetic_focus=(
            "rural Japanese outdoor night scene captured on a 1990s handheld "
            "camcorder — torii gate, mountain path, abandoned shrine, deserted "
            "country road, moonlit forest"
        ),
        style_suffix=_VHS_STYLE_SUFFIX,
        apply_vhs=True,
    ),
    VisualStyleProfile(
        name="vhs_traditional_indoor",
        aesthetic_focus=(
            "interior of an old traditional Japanese house captured on a 1990s "
            "home camcorder at night — tatami floor, shoji and fusuma, butsudan "
            "altar, dim oil lamp or single bare bulb"
        ),
        style_suffix=_VHS_STYLE_SUFFIX,
        apply_vhs=True,
    ),
    VisualStyleProfile(
        name="vhs_urban_mundane_night",
        aesthetic_focus=(
            "modern Japanese urban location at 3am captured on a 90s camcorder — "
            "convenience store, narrow alley, parking lot, station platform, "
            "empty apartment hallway under fluorescent light"
        ),
        style_suffix=_VHS_STYLE_SUFFIX,
        apply_vhs=True,
    ),
    VisualStyleProfile(
        name="vhs_object_closeup",
        aesthetic_focus=(
            "close-up of a single object on a fixed-position camcorder — old "
            "rotary phone, ningyo doll, broken mirror, family photo, religious "
            "talisman, cassette tape, broken brown TV — something ordinary that "
            "feels wrong"
        ),
        style_suffix=_VHS_STYLE_SUFFIX,
        apply_vhs=True,
    ),
    VisualStyleProfile(
        name="vhs_figure_distant",
        aesthetic_focus=(
            "grainy VHS recording showing a single distant unmoving figure at the "
            "edge of frame — silhouette in white kimono, child standing alone, "
            "elderly figure facing away, salaryman in deep shadow"
        ),
        style_suffix=_VHS_STYLE_SUFFIX,
        apply_vhs=True,
    ),
]


def pick_shorts_visual_style(title: str) -> VisualStyleProfile:
    """Deterministically select a visual style profile for a Short.

    Same `title` → same profile across re-runs. Different titles spread roughly
    evenly so a channel feed shows visibly different aesthetics day-to-day.
    """
    digest = hashlib.md5(title.encode("utf-8")).hexdigest()
    idx = int(digest[8:16], 16) % len(SHORTS_VISUAL_STYLES)
    return SHORTS_VISUAL_STYLES[idx]


def extract_scene_prompts(
    text: str, title: str, num_scenes: int = 3, model: str | None = None,
    style: VisualStyleProfile | None = None,
) -> list[str]:
    """Use Gemini to generate image prompts from story text.

    `style` injects a visual aesthetic the generated prompts should target,
    so each Short renders in its own style instead of the same VHS look.
    """
    client = get_gemini_image()
    model_name = model or cfg_get("gemini_model")

    aesthetic_line = (
        f"・全体の美術スタイル: {style.aesthetic_focus}（このスタイルに沿って描写）\n"
        if style is not None else ""
    )

    prompt = (
        f"以下の怪談「{title}」の内容を読み、怪談朗読動画の背景画像として使う{num_scenes}枚の画像生成プロンプトを英語で作ってください。\n\n"
        f"重要な要件:\n"
        f"{aesthetic_line}"
        f"・物語の象徴的な場面を具体的に描写すること。空間・情景・人物・霊的存在のいずれが主題でも可\n"
        f"・各画像は異なるシーン・異なる構図にすること。似た画像は絶対に避ける\n"
        f"  - 1枚目: 物語の舞台となる場所や状況の全景（wide establishing shot）\n"
        f"  - 2枚目: 物語の転換点や恐怖の核心となるモチーフ/存在のクローズアップ\n"
        f"  - 3枚目以降: クライマックスの情景や余韻を残すシーン\n"
        f"・各プロンプトは50〜80語程度で具体的に\n"
        f"・構図（wide shot, close-up, medium shot, overhead, dutch angle等）を必ず含める\n"
        f"・照明（moonlight, flickering light, backlit, single dim lamp等）を必ず含める\n"
        f"・人物・霊的存在を描いて良い。怪談に登場する場合はむしろ中心主題として描写する\n"
        f"・ただし画像生成AIの安全フィルタに引っかかる描写は避ける:\n"
        f"  - 子供・未成年を主題にしない(登場する話でも、大人・人影・場所に置き換える)\n"
        f"  - 恐怖や苦痛に歪んだ顔のクローズアップにしない\n"
        f"  - 流血・損傷・遺体の直接描写をしない\n"
        f"  怖さは暗さ・影・気配・不穏な構図で出す(日本の怪談の静かな恐怖)\n"
        f"・テキストや文字は絶対に含めない\n"
        f"・1行に1プロンプト、番号やマーカーは不要\n\n"
        f"物語:\n{text[:2000]}"
    )
    try:
        response = client.models.generate_content(model=model_name, contents=prompt)
        _record_usage(response, model_name, "scene_prompts")
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


def _generate_title_bg_prompt(
    text: str, title: str, style: VisualStyleProfile | None = None,
) -> str:
    """Use Gemini to generate a title card background prompt based on story content.

    `style` shifts the requested aesthetic so the title card BG matches the
    story's chosen visual profile.
    """
    if style is not None:
        fallback = (
            f"{style.aesthetic_focus}, no people, atmospheric, "
            "no text, no letters, no words, pure scenery only"
        )
        aesthetic_line = (
            f"・全体の美術スタイル: {style.aesthetic_focus}（このスタイルに沿って描写）\n"
        )
    else:
        fallback = (
            "dark atmospheric background, abandoned place, foggy, ominous sky, "
            "empty scene with no people, photorealistic, cinematic, "
            "extremely dark and moody, no text, no letters, no words, "
            "no writing, pure scenery only"
        )
        aesthetic_line = "・ダークで不気味な雰囲気、ホラー映画のワンシーンのように\n"
    try:
        client = get_gemini_image()
        model_name = cfg_get("gemini_model")
        prompt = (
            f"以下の怪談「{title}」の内容を読み、タイトルカードの背景画像用プロンプトを英語で1つだけ作ってください。\n\n"
            f"要件:\n"
            f"{aesthetic_line}"
            f"・物語の舞台や象徴的な場所を描写（人物は入れない）\n"
            f"・50〜80語程度で具体的に\n"
            f"・テキストや文字は絶対に含めない\n"
            f"・プロンプトのみ出力、説明不要\n\n"
            f"物語:\n{text[:1000]}"
        )
        response = client.models.generate_content(model=model_name, contents=prompt)
        _record_usage(response, model_name, "title_bg_prompt")
        result = (response.text or "").strip().split("\n")[0].strip()
        if len(result) > 20:
            return result
    except Exception as e:
        log.warning("タイトル背景プロンプト生成失敗: %s", e)
    return fallback




# 画像生成は 1 枚失敗すると作品ごと止まるので、一時的な切断には粘る。
# 30秒×2回より、短い間隔で回数を稼ぐほうが復帰率が高い。
@with_retry(max_attempts=4, base_delay=5.0, max_delay=60.0)
def generate_image_ai(
    prompt: str, model: str | None = None, size: str | None = None,
    aspect_ratio: str | None = None, style_override: str | None = None,
) -> bytes:
    """Generate an image using Imagen or AirForce API.

    `style_override` replaces the global `image_style` config — used by per-Short
    visual style profiles so the rendering aesthetic varies across uploads.
    """
    img_model = model or cfg_get("image_model")
    style = style_override if style_override is not None else cfg_get("image_style")
    # style が空文字なら何も足さない (プロンプト側で画作りが完結している場合)
    full_prompt = f"{prompt}, {style}" if style else prompt

    # Use Google API if model starts with "imagen" or "gemini"
    if img_model.startswith("imagen") or img_model.startswith("gemini"):
        return _generate_imagen(full_prompt, img_model, aspect_ratio=aspect_ratio)

    # Fallback to AirForce
    return _generate_airforce(full_prompt, img_model, size)


def score_image_quality(image_data: bytes) -> float:
    """生成画像の「使えそう度」を 0.0〜1.0 で採点する。

    タイトルカード背景として破綻している画像 (ほぼ真っ黒 / 真っ白 / のっぺりして
    情報量が無い) を弾くのが目的。芸術性の評価ではなく、明らかな失敗作を
    候補から落とすためのフィルタ。

    - detail: ラプラシアン相当の隣接差分の分散。輪郭やテクスチャの量。
    - exposure: 平均輝度が極端に暗い/明るいほど減点。
    """
    try:
        img = Image.open(BytesIO(image_data)).convert("L")
    except Exception:
        return 0.0
    # 大きい画像をそのまま扱うと遅いので縮小してから評価する
    img.thumbnail((256, 256), Image.BILINEAR)
    arr = np.asarray(img, dtype=np.float32)
    if arr.size == 0:
        return 0.0

    dx = np.diff(arr, axis=1)
    dy = np.diff(arr, axis=0)
    detail = float(dx.var() + dy.var())
    # 経験的に分散 400 前後で「十分ディテールがある」ので、そこを上限に正規化
    detail_score = min(detail / 400.0, 1.0)

    mean = float(arr.mean()) / 255.0
    # 平均輝度 0.35 付近 (暗めだが潰れていない) を満点とし、離れるほど減点
    exposure_score = max(0.0, 1.0 - abs(mean - 0.35) / 0.35)

    return 0.7 * detail_score + 0.3 * exposure_score


def generate_best_image(
    prompt: str, candidates: int = 1, model: str | None = None,
    size: str | None = None, aspect_ratio: str | None = None,
    style_override: str | None = None,
) -> bytes:
    """候補を複数生成し、採点が最も高い 1 枚を返す。

    `candidates <= 1` なら従来どおり 1 回だけ生成する。生成に失敗した候補は
    無視し、全滅した場合は最後の例外を送出して呼び出し側のフォールバックに
    委ねる。
    """
    if candidates <= 1:
        return generate_image_ai(
            prompt, model=model, size=size,
            aspect_ratio=aspect_ratio, style_override=style_override,
        )

    best: bytes | None = None
    best_score = -1.0
    last_exc: Exception | None = None
    for i in range(candidates):
        try:
            data = generate_image_ai(
                prompt, model=model, size=size,
                aspect_ratio=aspect_ratio, style_override=style_override,
            )
        except Exception as e:  # noqa: BLE001 — 候補単位の失敗は許容
            last_exc = e
            log.warning("画像候補 %d/%d の生成に失敗: %s", i + 1, candidates, e)
            continue
        score = score_image_quality(data)
        log.info("画像候補 %d/%d のスコア: %.3f", i + 1, candidates, score)
        if score > best_score:
            best, best_score = data, score

    if best is None:
        raise last_exc or RuntimeError("画像候補をすべて生成できませんでした")
    log.info("画像候補 %d 枚から採用 (スコア %.3f)", candidates, best_score)
    return best


# Imagen (`generate_images`) は Vertex AI 専用で、Gemini Developer API キーでは
# 動かない。google-genai 2.x は
#   "This method is only supported in Gemini Enterprise Agent Platform mode"
# という ValueError を投げる。設定で選ばれていても気づきにくいので、実行前に
# 何が悪くてどう直すのかを明示して止める。
_IMAGEN_NOT_SUPPORTED = (
    "{model} は Gemini Developer API キーでは使えません "
    "(Imagen は Vertex AI 専用)。設定の画像モデルを "
    "gemini-2.5-flash-image に変更してください。"
)


def _generate_imagen(prompt: str, model: str, aspect_ratio: str | None = None) -> bytes:
    """Generate image using Google Imagen or Gemini Image API."""
    from google.genai import types

    if model.startswith("imagen"):
        raise ImageUnavailableError(_IMAGEN_NOT_SUPPORTED.format(model=model))

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
        parts = response.candidates[0].content.parts or []
        for part in parts:
            if part.inline_data:
                _record_image(model, "image")
                return part.inline_data.data
        # 画像が返らないときは、たいてい安全性フィルタによる拒否で、その理由が
        # テキストで返っている。"returned no image" だけでは何が悪いか分からず
        # 直しようがないので、モデルの言い分をそのままエラーに載せる。
        reason = " ".join(p.text.strip() for p in parts if getattr(p, "text", None))
        finish = getattr(response.candidates[0], "finish_reason", None)
        raise RuntimeError(
            f"画像が返りませんでした (finish_reason={finish}): "
            f"{reason[:300] or '理由の記載なし'}"
        )

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


def replace_scene_image(
    target_path: Path,
    source_bytes: bytes,
    content_type: str = "short",
) -> Path:
    """Overwrite an existing scene PNG with a user-supplied image.

    The source is cover-fit (scale up so the smaller dim fills the target,
    then center-crop) to the canonical dimensions for the content type:
    Shorts → 1080×1920, Long → 1792×1024. This matches what the slideshow
    pipeline expects so the resulting video doesn't letterbox or break
    libx264 even-dimension constraints.
    """
    target_w, target_h = (1080, 1920) if content_type == "short" else (1792, 1024)
    img = Image.open(BytesIO(source_bytes))
    img = ImageOps.exif_transpose(img)
    img = ImageOps.fit(img, (target_w, target_h), method=Image.LANCZOS, centering=(0.5, 0.5))
    if img.mode != "RGB":
        img = img.convert("RGB")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(target_path, "PNG")
    log.info("シーン画像を差し替え: %s (%dx%d)", target_path, target_w, target_h)
    return target_path


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


@dataclass
class TitleCardTemplate:
    """Style config for the title card.

    Brand identity (red text + 怪談 badge + VHS aesthetic) is held constant
    across every Short — diversification happens in the AI-generated background
    content, not the text overlay. This dataclass is kept for back-compat but
    every Short now uses the single `classic_red` profile below.
    """
    name: str
    bg_brightness: float = 0.35
    bg_saturation: float = 0.4
    bg_blur: float = 3.0
    vignette_strength: float = 300 / 255  # 0 = none, larger = stronger edge darkening
    top_gradient_alpha: int = 180          # 0 disables the top-fade overlay
    text_color: tuple[int, int, int] = (230, 20, 20)
    outline_color: tuple[int, int, int] = (0, 0, 0)
    text_position: str = "center"          # "center" | "top" | "bottom"
    text_band: bool = False                # paint a translucent band behind text
    text_band_alpha: int = 140
    badge_color: tuple[int, int, int] | None = (160, 15, 15)  # None hides the badge
    badge_border_color: tuple[int, int, int] = (100, 5, 5)
    badge_text_color: tuple[int, int, int] = (255, 240, 240)
    badge_position: str = "top_right"      # "top_right" | "bottom_right" | "top_left"


# Distinct enough that side-by-side thumbnails do not look like the same template
# with different titles. Order is stable — picked deterministically by hash(title).
# Single, brand-locked template. Every Short uses this so the channel keeps
# the recognisable red-text + 怪談-badge identity. Background diversity comes
# from the AI image generation, never from the overlay.
SHORTS_TITLE_TEMPLATES: list[TitleCardTemplate] = [
    TitleCardTemplate(name="classic_red"),
]


def pick_shorts_title_template(title: str) -> TitleCardTemplate:
    """Deterministically select a Shorts title-card template from `title`.

    Same title always returns the same template so re-runs produce the same
    output. Different titles spread across all variants roughly evenly.
    """
    digest = hashlib.md5(title.encode("utf-8")).hexdigest()
    idx = int(digest[:8], 16) % len(SHORTS_TITLE_TEMPLATES)
    return SHORTS_TITLE_TEMPLATES[idx]


def create_title_card(
    title: str,
    width: int = 1792,
    height: int = 1024,
    bg_image_data: bytes | None = None,
    category: str = "怪談",
    template: TitleCardTemplate | None = None,
) -> bytes:
    """Create a cinematic horror-themed title card.

    Uses AI-generated background if provided, otherwise generates a dark procedural bg.
    Overlays title text with thick outlines, multiple lines, and dramatic layout.

    `template` selects the visual variant. Defaults to the original "classic_red"
    style for back-compat with long-form videos.
    """
    from PIL import ImageEnhance, ImageFilter

    if template is None:
        template = SHORTS_TITLE_TEMPLATES[0]

    # --- Background ---
    if bg_image_data:
        bg = Image.open(BytesIO(bg_image_data)).convert("RGB")
        bg = bg.resize((width, height), Image.LANCZOS)
    else:
        bg = Image.new("RGB", (width, height), (10, 5, 5))

    bg = ImageEnhance.Brightness(bg).enhance(template.bg_brightness)
    bg = ImageEnhance.Color(bg).enhance(template.bg_saturation)
    if template.bg_blur > 0:
        bg = bg.filter(ImageFilter.GaussianBlur(radius=template.bg_blur))

    # Vignette
    if template.vignette_strength > 0:
        cx, cy = width / 2, height / 2
        max_dist = np.sqrt(cx ** 2 + cy ** 2)
        y_coords, x_coords = np.mgrid[0:height, 0:width]
        dist = np.sqrt((x_coords - cx) ** 2 + (y_coords - cy) ** 2)
        ratio = dist / max_dist
        darken = np.clip(
            1.0 - ratio * ratio * template.vignette_strength, 0, 1
        ).astype(np.float32)
        bg_arr = np.array(bg, dtype=np.float32)
        bg_arr *= darken[:, :, np.newaxis]
        bg = Image.fromarray(np.clip(bg_arr, 0, 255).astype(np.uint8))

    draw = ImageDraw.Draw(bg)

    # Top fade gradient
    if template.top_gradient_alpha > 0:
        gradient_h = int(height * 0.3)
        overlay = Image.new("RGBA", (width, gradient_h), (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        for y_pos in range(gradient_h):
            alpha = int(template.top_gradient_alpha * (1 - y_pos / gradient_h))
            overlay_draw.line([(0, y_pos), (width, y_pos)], fill=(0, 0, 0, alpha))
        bg.paste(overlay, (0, 0), overlay)
        draw = ImageDraw.Draw(bg)

    # --- Title text ---
    is_vertical = height > width
    chars_per_line = 5 if is_vertical else 7
    lines = _wrap_title(title, max_chars_per_line=chars_per_line)

    padding_x = width // 8 if is_vertical else width // 10
    available_w = width - padding_x * 2
    max_line_len = max(len(line) for line in lines)
    font_size = min(available_w // max(max_line_len, 1), height // (len(lines) + 2))
    if is_vertical:
        max_font = 500 if max_line_len <= 3 else (420 if max_line_len <= 5 else 360)
    else:
        max_font = 300 if max_line_len <= 3 else 240
    font_size = min(font_size, max_font)
    font_size = max(font_size, 120 if is_vertical else 80)

    font = _find_cjk_font(font_size, use_koin=True)
    if font is None:
        font = ImageFont.load_default()

    line_heights: list[int] = []
    line_widths: list[int] = []
    line_offsets_x: list[int] = []
    line_offsets_y: list[int] = []
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_widths.append(bbox[2] - bbox[0])
        line_heights.append(bbox[3] - bbox[1])
        line_offsets_x.append(bbox[0])
        line_offsets_y.append(bbox[1])

    line_spacing = int(font_size * 0.25)
    total_text_height = sum(line_heights) + line_spacing * (len(lines) - 1)

    if template.text_position == "top":
        start_y = int(height * 0.12)
    elif template.text_position == "bottom":
        start_y = height - total_text_height - int(height * 0.12)
    else:
        start_y = (height - total_text_height) // 2

    # Translucent band behind text (improves contrast on busy backgrounds)
    if template.text_band:
        band_pad = int(font_size * 0.4)
        band_top = max(0, start_y - band_pad)
        band_bottom = min(height, start_y + total_text_height + band_pad)
        band = Image.new("RGBA", (width, band_bottom - band_top),
                         (0, 0, 0, template.text_band_alpha))
        bg.paste(band, (0, band_top), band)
        draw = ImageDraw.Draw(bg)

    outline_w = max(4, font_size // 18)
    current_y = start_y
    for i, line in enumerate(lines):
        lw = line_widths[i]
        x = (width - lw) // 2 - line_offsets_x[i]
        y = current_y - line_offsets_y[i]
        _draw_text_with_outline(
            draw, (x, y), line, font,
            fill=template.text_color,
            outline_fill=template.outline_color,
            outline_width=outline_w,
        )
        current_y += line_heights[i] + line_spacing

    # --- Category badge ---
    if template.badge_color is not None:
        badge_font = _find_cjk_font(112, use_koin=True)
        if badge_font:
            badge_text = category
            badge_bbox = draw.textbbox((0, 0), badge_text, font=badge_font)
            text_w = badge_bbox[2] - badge_bbox[0]
            text_h = badge_bbox[3] - badge_bbox[1]
            pad_x, pad_y = 16, 10
            bw = text_w + pad_x * 2
            bh = text_h + pad_y * 2
            margin = 40
            if template.badge_position == "top_right":
                bx, by = width - bw - margin, margin
            elif template.badge_position == "bottom_right":
                bx, by = width - bw - margin, height - bh - margin
            else:  # top_left
                bx, by = margin, margin
            draw.rectangle(
                [bx - 2, by - 2, bx + bw + 2, by + bh + 2],
                fill=template.badge_border_color,
            )
            draw.rectangle([bx, by, bx + bw, by + bh], fill=template.badge_color)
            tx = bx + (bw - text_w) // 2 - badge_bbox[0]
            ty = by + (bh - text_h) // 2 - badge_bbox[1]
            draw.text((tx, ty), badge_text, font=badge_font,
                      fill=template.badge_text_color)

    buf = BytesIO()
    bg.save(buf, format="PNG", quality=95)
    return buf.getvalue()


def thumbnail_subject(text: str, title: str) -> str:
    """本文から「サムネイルに何を大きく写すか」を英語1文で起こす。

    失敗したらタイトルだけを手掛かりにした無難な描写を返す。ここは絵の題材選び
    であって、失敗しても真っ黒にはならない (画像生成側は失敗すれば止まる)。
    """
    prompt = (
        f"以下の怪談「{title}」を読み、サムネイルの主役として大きく写すべき"
        f"最も印象的で不気味な対象を、英語1文(40語以内)で描写してください。"
        f"人物・怪異・場所・物体のどれでも可。説明や前置きは不要、描写のみ。\n\n"
        f"{text[:1500]}"
    )
    try:
        client = get_gemini_text()
        model_name = cfg_get("gemini_model")
        response = client.models.generate_content(model=model_name, contents=prompt)
        _record_usage(response, model_name, "thumbnail_subject")
        result = (response.text or "").strip().split("\n")[0].strip()
        if len(result) > 15:
            return result
    except Exception as e:  # noqa: BLE001 — 題材選びの失敗は致命的でない
        log.warning("サムネイル主役の抽出に失敗: %s", e)
    return (
        "a dark, ominous Japanese scene evoking quiet dread, "
        "with a single unsettling presence"
    )


def thumbnail_badge(category: str) -> str:
    """サムネイル左上のバッジ文言。カテゴリ名をそのまま使う。

    怪談と人怖では話の性質が違うので、同じ「怪談朗読」を貼るのは正しくない。
    長すぎる等で別名にしたいカテゴリは `shorts_badge_overrides` で上書きする。
    カテゴリが無い作品は空文字を返し、バッジ自体を出さない。
    """
    category = (category or "").strip()
    if not category:
        return ""
    overrides = cfg_get("shorts_badge_overrides") or {}
    return overrides.get(category, category)


def _badge_instruction(badge: str) -> str:
    """バッジの有無をプロンプトの指示文に落とす。"""
    if not badge:
        return (
            "- Do NOT draw any stamp, plaque, label or badge in the corners. "
            "Only the title text below.\n"
        )
    return (
        "- Top-LEFT corner: a small red weathered stamp/plaque reading exactly: "
        f"{badge}\n"
    )


def generate_shorts_thumbnail(title: str, text: str, category: str = "") -> bytes:
    """Shorts の 1 枚目 (タイトル文字入りサムネイル) を生成する。

    タイトルと「怪談朗読」を画像生成AI自身に描かせる。後段で文字を合成しないので、
    暗くする・ぼかす等の加工も掛けない (掛けると絵が潰れて読めなくなる)。
    モデルは日本語を正しく描けるものを選ぶこと — 2.5-flash-image は
    「赤い土」を「我い土」にする。
    """
    template = cfg_get("shorts_thumbnail_prompt") or ""
    if "{title}" not in template:
        raise ImageUnavailableError(
            "shorts_thumbnail_prompt に {title} がありません。設定を確認してください。"
        )
    subject = thumbnail_subject(text, title)
    badge = thumbnail_badge(category)
    prompt = (
        template.replace("{title}", title)
        .replace("{subject}", subject)
        .replace("{badge_instruction}", _badge_instruction(badge))
    )
    log.info(
        "サムネイル生成: %s / バッジ: %s / 主役: %s",
        title, badge or "(なし)", subject[:60],
    )

    model = shorts_image_model() or cfg_get("image_model")
    try:
        # style_override="" でグローバルの VHS スタイル文を付けない。
        # プロンプト側で画作りを完結させているため。
        return generate_image_ai(
            prompt, model=model,
            aspect_ratio=cfg_get("shorts_image_aspect_ratio"),
            style_override="",
        )
    except Exception as e:
        raise ImageUnavailableError(
            f"サムネイル生成に失敗しました (model={model}): {e}"
        ) from e


def _needs_ai(source: str, kind: str) -> bool:
    """この調達方法で AI 生成が発生しうるか (プロンプト生成の要否判定用)。"""
    if source == "pool":
        return False
    if source == "pool_first":
        return not image_pool.list_pool(kind)
    return True


def shorts_image_model() -> str | None:
    """Shorts 用の画像モデル。未設定なら None (= 共通の image_model を使う)。

    1 枚目の品質が安定しないため、長編と別のモデルを指定できるようにしている。
    """
    model = (cfg_get("shorts_image_model") or "").strip()
    return model or None


def _resolve_shorts_image(
    kind: str,
    source: str,
    key: str,
    offset: int,
    prompt: str,
    width: int,
    height: int,
    aspect_ratio: str | None,
    style_suffix: str | None,
    use_vhs: bool,
    candidates: int,
) -> tuple[bytes | None, bool]:
    """Shorts 用の画像 1 枚を調達する。戻り値は (画像バイト列, プール由来か)。

    `source` が "pool" / "pool_first" のときは `assets/shorts/<kind>/` から
    決定的に選ぶ。"pool_first" でプールが空なら AI 生成にフォールバックする。
    画像を用意できない場合は `ImageUnavailableError` を送出する。黙って黒背景を
    返すと、真っ黒な動画がそのまま YouTube まで流れてしまうため。
    """
    if source in ("pool", "pool_first"):
        picked = image_pool.pick(kind, key, offset=offset)
        if picked is not None:
            try:
                data = image_pool.load_fitted(picked, width, height)
                if cfg_get("shorts_pool_apply_vhs"):
                    data = degrade_to_vhs(data)
                log.info("%s: プール画像を使用 (%s)", kind, picked.name)
                return data, True
            except Exception as e:  # noqa: BLE001 — 壊れた画像は AI 生成へ回す
                log.warning("プール画像の読込に失敗 (%s): %s", picked, e)
        if source == "pool":
            raise ImageUnavailableError(
                f"{kind}: 画像プールが空です。"
                f"assets/shorts/{kind}/ に画像を入れるか、設定で「プール優先」"
                f"または「毎回AI生成」に変更してください。"
            )

    try:
        data = generate_best_image(
            prompt,
            candidates=max(1, candidates),
            model=shorts_image_model(),
            aspect_ratio=aspect_ratio,
            style_override=style_suffix,
        )
    except Exception as e:
        raise ImageUnavailableError(
            f"{kind}: 画像生成に失敗しました (model={shorts_image_model()}): {e}"
        ) from e
    if use_vhs:
        data = degrade_to_vhs(data)
    log.info("%s: AI 画像生成成功", kind)
    return data, False


def generate_images_for_story(
    text: str, title: str, output_dir: Path, category: str = "怪談",
    progress_callback=None, content_type: str = "long",
) -> list[Path]:
    """Generate all images for a story."""
    is_short = content_type == "short"
    num_scenes = cfg_get("shorts_num_scenes") if is_short else cfg_get("num_scenes")
    rate_limit = cfg_get("image_rate_limit")
    use_vhs_default = cfg_get("shorts_vhs_enabled") if is_short else True

    # Pick a coherent visual style profile per Short to break the visual
    # uniformity that triggers YouTube similarity dampening. Long-form keeps
    # the global style for now.
    style_profile = pick_shorts_visual_style(title) if is_short else None
    if style_profile is not None:
        log.info("ビジュアルスタイル: %s", style_profile.name)
    use_vhs = use_vhs_default and (
        style_profile.apply_vhs if style_profile is not None else True
    )

    image_paths: list[Path] = []

    if is_short:
        tc_w, tc_h = 1080, 1920
    else:
        tc_w, tc_h = 1792, 1024

    scene_w, scene_h = (1080, 1920) if is_short else (1792, 1024)

    # ----- Shorts: scene_000.png IS the title card -----
    # The first AI image gets the title text/badge composited directly onto
    # it and is saved as scene_000.png; no separate 000_title_card.png file
    # is produced. Subsequent prompts become bare scene_001.png onwards.
    # Total output for Shorts: 2 PNGs (scene_000.png + scene_001.png) under
    # the default shorts_num_scenes=2.
    if is_short:
        scene_source = cfg_get("shorts_scene_source") or "ai"
        # 2枚目以降をプールで賄えるなら、シーンプロンプトの生成自体を省く。
        prompts: list[str] = []
        if num_scenes > 1 and _needs_ai(scene_source, image_pool.SCENE):
            prompts = extract_scene_prompts(text, title, num_scenes, style=style_profile)
        if not prompts:
            prompts = [f"dark Japanese horror scene, {title}"] * max(num_scenes, 1)

        style_suffix = style_profile.style_suffix if style_profile else None
        ar = cfg_get("shorts_image_aspect_ratio")

        # ── scene_000.png = サムネイル ──
        # タイトル文字と「怪談朗読」は画像生成AIが絵の中に描く。以前はここで
        # 生成画像を暗くしてぼかし、その上に文字を合成していたが、加工が強すぎて
        # どんな絵を入れても黒い板になっていた。合成も加工もしない。
        scene0_path = output_dir / "scene_000.png"
        scene0_path.write_bytes(generate_shorts_thumbnail(title, text, category))
        image_paths.append(scene0_path)
        if progress_callback:
            progress_callback(1, num_scenes)
        if rate_limit > 0 and num_scenes > 1:
            time.sleep(rate_limit)

        # ── scene_001.png 以降 = 本編のシーン画像 ──
        for scene_idx in range(1, num_scenes):
            img_path = output_dir / f"scene_{scene_idx:03d}.png"
            prompt = prompts[scene_idx] if scene_idx < len(prompts) else prompts[-1]
            img_data, from_pool = _resolve_shorts_image(
                kind=image_pool.SCENE,
                source=scene_source,
                key=title,
                offset=scene_idx,
                prompt=prompt,
                width=scene_w, height=scene_h,
                aspect_ratio=ar,
                style_suffix=style_suffix,
                use_vhs=use_vhs,
                candidates=1,
            )
            img_path.write_bytes(img_data)
            image_paths.append(img_path)
            if progress_callback:
                progress_callback(scene_idx + 1, num_scenes)
            if scene_idx < num_scenes - 1 and rate_limit > 0 and not from_pool:
                time.sleep(rate_limit)
        return image_paths

    # ----- Long-form: separate title BG generation, full scene set -----
    title_bg_prompt = _generate_title_bg_prompt(text, title, style=style_profile)
    try:
        title_bg_data = generate_image_ai(title_bg_prompt)
        log.info("タイトル背景画像生成成功")
    except Exception as e:
        raise ImageUnavailableError(
            f"タイトル背景の生成に失敗しました (model={cfg_get('image_model')}): {e}"
        ) from e

    title_path = output_dir / "000_title_card.png"
    title_path.write_bytes(
        create_title_card(
            title, width=tc_w, height=tc_h,
            bg_image_data=title_bg_data, category=category,
        )
    )
    image_paths.append(title_path)

    if rate_limit > 0:
        time.sleep(rate_limit)

    prompts = extract_scene_prompts(text, title, num_scenes, style=style_profile)

    for i, prompt in enumerate(prompts):
        if progress_callback:
            progress_callback(i + 1, len(prompts) + 1)
        log.info("AI画像生成中 (%d/%d): %s", i + 1, len(prompts), prompt[:60])
        img_path = output_dir / f"scene_{i:03d}.png"

        try:
            img_data = generate_image_ai(prompt)
        except Exception as e:
            raise ImageUnavailableError(
                f"シーン {i + 1} の画像生成に失敗しました "
                f"(model={cfg_get('image_model')}): {e}"
            ) from e
        if use_vhs:
            img_data = degrade_to_vhs(img_data)
        img_path.write_bytes(img_data)

        image_paths.append(img_path)

        if i < len(prompts) - 1:
            time.sleep(rate_limit)

    return image_paths
