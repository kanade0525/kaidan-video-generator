"""YouTube chapter (timestamp + section label) auto-generation.

For a finished narration (per-chunk audio + chunk text), ask an LLM to
assign a short section label to each chunk, then group consecutive chunks
with the same label to produce a list of (label, start_seconds) entries.

The output is suitable for embedding in a YouTube description:

    0:00 オープニング
    0:10 タイトル
    0:15 事件の発覚
    2:30 異変
    5:00 真相
    ...

Safe-fallback: if LLM fails or returns unexpected output, returns [] so the
caller can fall back to simple 本編/エンディング markers.
"""
from __future__ import annotations

import json
import re

from app.config import get as cfg_get
from app.services.clients import get_gemini_text
from app.utils.log import get_logger

log = get_logger("kaidan.chapter")


def generate_chapter_labels(
    chunks: list[str], story_title: str, max_chapters: int = 6,
) -> list[str] | None:
    """Ask LLM to label each chunk with a short section name.

    Returns a list of labels, one per chunk, or None on failure.
    Labels are kept to ~6-10 chars (怪談 tone), e.g. 「事件の発覚」「真相」.
    """
    if not chunks:
        return []

    numbered = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(chunks))
    prompt = (
        f"以下は怪談「{story_title}」の本文を分割したチャンクです。\n"
        f"各チャンクにセクション名 (例: 「事件の発覚」「異変」「真相」「結末」"
        f"等、6-10文字の短い見出し) を割り振ってください。\n\n"
        f"制約:\n"
        f"・全{len(chunks)}チャンクに同じ数のラベル\n"
        f"・連続する同じセクションは同じラベルで（グルーピングはこちらで行う）\n"
        f"・最終的に {max_chapters} 章程度になるよう適度にグルーピング\n"
        f"・セクション名は YouTube チャプター用、ネタバレ気味に具体的に、"
        f"ただし結末だけは 「真相」「結末」等で伏せる\n"
        f"・JSON配列のみ出力。余計な説明/ mark down 不要\n\n"
        f"チャンク:\n{numbered}\n\n"
        f'出力例 (チャンク数 {len(chunks)} の場合):\n'
        f'["オープニング", "事件の発覚", "事件の発覚", "異変", "異変", "真相"]'
    )
    try:
        client = get_gemini_text()
        model_name = cfg_get("gemini_model") or "gemini-2.5-flash-lite"
        response = client.models.generate_content(model=model_name, contents=prompt)
        text = (response.text or "").strip()
        # Strip markdown fences
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        labels = json.loads(text)
        if not isinstance(labels, list):
            raise ValueError(f"Expected list, got {type(labels).__name__}")
        if len(labels) != len(chunks):
            log.warning(
                "LLM chapter labels mismatch: got %d, expected %d", len(labels), len(chunks),
            )
            return None
        return [str(x).strip()[:20] for x in labels]
    except Exception as e:
        log.warning("chapter label generation failed: %s", e)
        return None


def group_labels_to_chapters(
    labels: list[str], chunk_durations: list[float], start_offset: float,
) -> list[dict]:
    """Group consecutive identical labels into chapters with start times.

    Args:
        labels: one label per chunk
        chunk_durations: duration in seconds per chunk (must match labels length)
        start_offset: seconds from video start where chunk 0 begins
                      (i.e., OP + title + leading_silence)

    Returns:
        [{"label": str, "start": float}, ...]
    """
    if not labels or len(labels) != len(chunk_durations):
        return []

    chapters: list[dict] = []
    cursor = start_offset
    prev_label = None
    for label, dur in zip(labels, chunk_durations):
        if label != prev_label:
            chapters.append({"label": label, "start": cursor})
            prev_label = label
        cursor += dur
    return chapters
