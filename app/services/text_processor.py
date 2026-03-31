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
        api_key = os.environ.get("GEMINI_API_KEY_TEXT_TO_TEXT") or os.environ.get("GEMINI_API_KEY", "")
        _gemini_client = genai.Client(api_key=api_key)
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

    # Remove repetition loops (Gemini sometimes generates infinite repeats)
    result = _remove_repetitions(result)

    # Post-process: convert particle は→わ, へ→え using MeCab
    result = _fix_particles(result)
    return result


def _remove_repetitions(text: str, min_pattern_len: int = 8, max_repeats: int = 2) -> str:
    """Detect and remove repeated phrases that indicate LLM output loops."""
    # Find patterns that repeat more than max_repeats times
    for pattern_len in range(min_pattern_len, 60):
        i = 0
        while i < len(text) - pattern_len * 2:
            pattern = text[i:i + pattern_len]
            count = 1
            j = i + pattern_len
            while j + pattern_len <= len(text) and text[j:j + pattern_len] == pattern:
                count += 1
                j += pattern_len
            if count > max_repeats:
                # Found a loop - keep only max_repeats occurrences
                log.warning("繰り返しパターン検出 (%d回): %s...", count, pattern[:30])
                text = text[:i + pattern_len * max_repeats] + text[j:]
            i += 1
    return text


def _fix_particles(text: str) -> str:
    """Convert particle は to わ and へ to え using MeCab morphological analysis."""
    try:
        import MeCab
        tagger = MeCab.Tagger()
        tagger.parse("")  # Initialize

        output = []
        node = tagger.parseToNode(text)
        while node:
            surface = node.surface
            feature = node.feature.split(",")
            pos = feature[0] if feature else ""

            if surface == "は" and pos == "助詞":
                output.append("わ")
            elif surface == "へ" and pos == "助詞":
                output.append("え")
            else:
                output.append(surface)
            node = node.next

        return "".join(output)
    except ImportError:
        log.warning("MeCab not installed, skipping particle conversion")
        return text
    except Exception as e:
        log.warning("MeCab error: %s", e)
        return text


def split_into_chunks(text: str, max_length: int | None = None) -> list[str]:
    """Split text into chunks by sentence boundaries."""
    max_len = max_length or cfg_get("max_chunk")
    # Split on sentence-ending punctuation, or fall back to commas/newlines
    sentences = re.split(r"(?<=[。！？\n])", text)
    if len(sentences) <= 1 and len(text) > max_len:
        # No sentence-ending punctuation found; split on commas or periods
        sentences = re.split(r"(?<=[、，,.])", text)
    sentences = [s.strip() for s in sentences if s.strip()]

    chunks = []
    current = ""
    for sentence in sentences:
        if len(sentence) > max_len:
            # Force-split oversized sentences
            if current:
                chunks.append(current)
                current = ""
            for i in range(0, len(sentence), max_len):
                chunks.append(sentence[i:i + max_len])
        elif len(current) + len(sentence) > max_len and current:
            chunks.append(current)
            current = sentence
        else:
            current += sentence

    if current:
        chunks.append(current)

    return chunks
