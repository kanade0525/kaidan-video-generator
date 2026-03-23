from __future__ import annotations

import os
import re

from app.config import get as cfg_get
from app.pipeline.retry import with_retry
from app.utils.log import get_logger

log = get_logger("kaidan.text")

_gemini_client = None
_openai_client = None


def _get_gemini():
    global _gemini_client
    if _gemini_client is None:
        from google import genai
        _gemini_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY", ""))
    return _gemini_client


def _get_openai():
    global _openai_client
    if _openai_client is None:
        from openai import OpenAI
        _openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
    return _openai_client


@with_retry(max_attempts=3, base_delay=5.0)
def process_text(text: str, prompt_template: str | None = None, model: str | None = None) -> str:
    """Convert kanji text to hiragana using LLM API."""
    model_name = model or cfg_get("text_model") or "gemini-2.5-flash"
    template = prompt_template or cfg_get("text_prompt")

    if model_name.startswith("gpt"):
        client = _get_openai()
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": template},
                {"role": "user", "content": text},
            ],
            temperature=0.0,
        )
        result = response.choices[0].message.content or ""
    else:
        client = _get_gemini()
        prompt = f"{template}\n\n{text}"
        response = client.models.generate_content(model=model_name, contents=prompt)
        result = response.text or ""

    # Strip markdown code blocks
    result = re.sub(r"```[\s\S]*?```", "", result)
    result = result.strip()

    # Post-process: convert particle は→わ, へ→え
    result = _fix_particles(result)
    return result


def _fix_particles(text: str) -> str:
    """Convert particle は to わ and へ to え for correct TTS pronunciation.

    Geminiに任せると無視されるため、後処理で変換する。
    助詞の「は」は直後にひらがなが続かないパターンで判定する。
    """
    # 助詞の「は」: 直後が句読点、改行、EOF、または次の文節の開始
    # 「は」の後に続くひらがなが助詞の一部でないケースを狙う
    # 安全なパターン: は + 句読点/改行/EOF
    text = re.sub(r"は(?=[、。！？\n]|$)", "わ", text)
    # は + 助動詞/補助的表現のパターン
    for pattern in ["はない", "はある", "はいる", "はおも", "はしろ",
                     "はして", "はされ", "はでき", "はなく", "はなか",
                     "はわか", "はいえ", "はいけ", "はいた", "はいい",
                     "はいっ", "はおお", "はこの", "はその", "はあの",
                     "はどう", "はなに", "はとて", "はまだ", "はもう",
                     "はただ", "はつね"]:
        replacement = "わ" + pattern[1:]
        text = text.replace(pattern, replacement)

    # 助詞の「へ」: へ + 助詞的パターン
    text = re.sub(r"へ(?=[、。！？\n]|$)", "え", text)
    for pattern in ["へいく", "へいっ", "へいき", "へむか", "へつれ",
                     "へはい", "へでか"]:
        replacement = "え" + pattern[1:]
        text = text.replace(pattern, replacement)

    return text


def split_into_chunks(text: str, max_length: int | None = None) -> list[str]:
    """Split text into chunks by sentence boundaries."""
    max_len = max_length or cfg_get("max_chunk")
    sentences = re.split(r"(?<=[。！？])", text)
    sentences = [s.strip() for s in sentences if s.strip()]

    chunks = []
    current = ""
    for sentence in sentences:
        if len(current) + len(sentence) > max_len and current:
            chunks.append(current)
            current = sentence
        else:
            current += sentence

    if current:
        chunks.append(current)

    return chunks
