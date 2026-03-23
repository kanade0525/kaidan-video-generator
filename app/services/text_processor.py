from __future__ import annotations

import os
import re

from google import genai

from app.config import get as cfg_get
from app.pipeline.retry import with_retry
from app.utils.log import get_logger

log = get_logger("kaidan.text")

_client = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY", ""))
    return _client


@with_retry(max_attempts=3, base_delay=5.0)
def process_text(text: str, prompt_template: str | None = None, model: str | None = None) -> str:
    """Convert kanji text to hiragana using Gemini API."""
    client = _get_client()
    model_name = model or cfg_get("gemini_model")
    template = prompt_template or cfg_get("text_prompt")
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
