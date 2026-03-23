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
    return result


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
