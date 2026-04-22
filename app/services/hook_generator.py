"""Opening hook auto-generation for kaidan videos.

Generates a short (1-2 sentences, ~10-15s) dramatic "hook" narration that
plays at the very beginning of the video to grab viewer attention. This is
critical for YouTube's first-3-seconds retention metric.

Flow:
  1. LLM reads the full story and writes a hook line (30-60 chars)
     ending with a cliffhanger ("...だった。" etc.), NOT revealing the ending
  2. Hook is converted to hiragana via text_processor (same reading rules)
  3. Hook is synthesized via VOICEVOX with the same speaker

Output: a .wav file at story_dir / 'hook.wav' ready for video concat
"""
from __future__ import annotations

import re
from pathlib import Path

from app.config import get as cfg_get
from app.services import text_processor, voice_generator
from app.services.clients import get_gemini_text
from app.utils.log import get_logger

log = get_logger("kaidan.hook")


def generate_hook_text(story_title: str, full_text: str) -> str | None:
    """Ask LLM to write a dramatic 30-60 char hook line.

    Returns None on failure.
    """
    prompt = (
        f"以下は怪談「{story_title}」の本文です。\n"
        f"動画冒頭（5-8秒）で視聴者を惹きつける「フック」を1行で書いてください。\n\n"
        f"制約:\n"
        f"・**30-60文字**、1-2文以内\n"
        f"・**結末は伏せ**、続きが気になるように ('…' などで余韻)\n"
        f"・物語の核心に触れる具体的な描写 (漠然とした煽りは避ける)\n"
        f"・語尾は常体 (「…だった」「…している」「…なのか」) 推奨\n"
        f"・ハッシュタグ・絵文字・記号過多は避ける (朗読するのに自然な文)\n"
        f"・出力はフック文のみ。説明・前置き・マークダウン不要\n\n"
        f"本文:\n{full_text[:3000]}"
    )
    try:
        client = get_gemini_text()
        model_name = cfg_get("gemini_model") or "gemini-2.5-flash-lite"
        response = client.models.generate_content(model=model_name, contents=prompt)
        text = (response.text or "").strip()
        text = re.sub(r"^```[\w]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.splitlines()[0] if text else ""
        text = text.strip()
        # Basic sanity
        if 10 <= len(text) <= 120:
            return text
        log.warning("hook length out of bounds (%d chars): %s", len(text), text[:60])
        return None
    except Exception as e:
        log.warning("hook text generation failed: %s", e)
        return None


def generate_hook_audio(
    story_title: str, full_text: str, output_path: Path, speed: float | None = None,
) -> tuple[Path, str] | None:
    """Generate hook audio .wav file for a story.

    Returns (path, hook_text) on success, None on failure.
    """
    hook = generate_hook_text(story_title, full_text)
    if not hook:
        return None

    # Convert hook to hiragana for VOICEVOX (same pipeline as body narration)
    try:
        hiragana_hook = text_processor.process_text(hook)
    except Exception as e:
        log.warning("hook text_processor failed, using raw: %s", e)
        hiragana_hook = hook

    try:
        audio_data = voice_generator.text_to_speech(hiragana_hook, speed=speed)
        output_path.write_bytes(audio_data)
        log.info("[hook] 生成: %s (%d chars)", output_path.name, len(hook))
        return output_path, hook
    except Exception as e:
        log.warning("hook audio synthesis failed: %s", e)
        return None
