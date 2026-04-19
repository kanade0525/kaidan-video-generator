from __future__ import annotations

import re

from app.config import get as cfg_get
from app.pipeline.retry import with_retry
from app.services.clients import get_gemini_text, get_openai
from app.utils.log import get_logger

log = get_logger("kaidan.text")


@with_retry(max_attempts=3, base_delay=5.0)
def process_text(text: str, prompt_template: str | None = None, model: str | None = None) -> str:
    """Convert kanji text to hiragana.

    Strategy: MeCab-first deterministic conversion (kanji→reading, particle
    は→わ, へ→え). If MeCab is unavailable, falls back to LLM-only conversion.
    """
    mecab_result = _mecab_to_hiragana(text)
    if mecab_result is not None:
        return mecab_result

    log.warning("MeCab先行変換が失敗したためLLMフォールバックに切替")
    return _llm_convert(text, prompt_template, model)


def _llm_convert(text: str, prompt_template: str | None, model: str | None) -> str:
    """LLM-only conversion (used as fallback when MeCab unavailable)."""
    model_name = model or cfg_get("text_model") or "gemini-2.5-flash"
    template = prompt_template or cfg_get("text_prompt")

    if model_name.startswith("gpt"):
        client = get_openai()
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
        client = get_gemini_text()
        prompt = f"{template}\n\n{text}"
        response = client.models.generate_content(model=model_name, contents=prompt)
        result = response.text or ""

    result = re.sub(r"```[\s\S]*?```", "", result)
    result = result.strip()
    result = _remove_repetitions(result)

    if re.search(r"[一-龯]", result):
        result = _convert_kanji_to_hiragana(result)

    return result


# Surface → hiragana overrides for readings MeCab gets stylistically wrong
# for 怪談朗読. Add entries here when a word is consistently mis-read.
_READING_OVERRIDES: dict[str, str] = {
    "私": "わたし",  # MeCab default: わたくし
}


def _mecab_to_hiragana(text: str) -> str | None:
    """Convert text to hiragana using MeCab (kanji→reading + particle は/へ→わ/え).

    Returns None if MeCab is unavailable so the caller can fall back.
    """
    try:
        import MeCab
    except ImportError:
        log.warning("MeCab未インストール")
        return None

    try:
        tagger = MeCab.Tagger()
        tagger.parse("")
        output: list[str] = []
        node = tagger.parseToNode(text)
        while node:
            surface = node.surface
            if not surface:
                node = node.next
                continue

            feature = node.feature.split(",")
            pos = feature[0] if feature else ""
            pron = _extract_reading(feature)

            has_kanji = bool(re.search(r"[一-龯々〆]", surface))
            is_particle = pos == "助詞"

            if surface in _READING_OVERRIDES:
                output.append(_READING_OVERRIDES[surface])
            elif has_kanji and pron:
                output.append(_katakana_to_hiragana(pron))
            elif is_particle and surface == "は":
                output.append("わ")
            elif is_particle and surface == "へ":
                output.append("え")
            else:
                output.append(surface)
            node = node.next

        return "".join(output)
    except Exception as e:
        log.warning("MeCab変換エラー: %s", e)
        return None


def _extract_reading(feature: list[str]) -> str | None:
    """Extract katakana reading matching the surface form.

    Priority (unidic-lite / ipadic aware):
    1. feature[17] — surface-form kana in proper orthography (unidic-lite):
       handles inflected verbs correctly (覚まし→サマシ, not レンマの サマス).
    2. feature[6] — lForm reading (unidic-lite base-form fallback).
    3. feature[9] — pron (uses "ー" for long vowels, last resort).
    4. feature[7] / feature[8] — ipadic reading / pron.

    Only katakana-only values are accepted to guard against lemma fields
    that contain kanji.
    """
    for idx in (17, 6, 9, 7, 8):
        if idx < len(feature) and feature[idx] != "*":
            val = feature[idx]
            if re.fullmatch(r"[ァ-ヴー]+", val):
                return val
    return None


def _convert_kanji_to_hiragana(text: str) -> str:
    """Convert only remaining kanji in text to hiragana (fallback path)."""
    try:
        import MeCab

        tagger = MeCab.Tagger()
        tagger.parse("")
        output: list[str] = []
        node = tagger.parseToNode(text)
        while node:
            surface = node.surface
            if not surface:
                node = node.next
                continue

            feature = node.feature.split(",")
            reading = _extract_reading(feature)

            if re.search(r"[一-龯々〆]", surface) and reading:
                output.append(_katakana_to_hiragana(reading))
            else:
                output.append(surface)
            node = node.next

        return "".join(output)
    except ImportError:
        log.warning("MeCab not installed, skipping kanji fallback conversion")
        return text
    except Exception as e:
        log.warning("MeCab error during kanji conversion: %s", e)
        return text


def _katakana_to_hiragana(text: str) -> str:
    return "".join(
        chr(ord(ch) - 0x60) if "ァ" <= ch <= "ン" else ch
        for ch in text
    )


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


def split_into_n_chunks(text: str, n: int) -> list[str]:
    """Split text into exactly *n* chunks at sentence boundaries.

    Used to create original-text (kanji) chunks that map 1:1 to the
    hiragana chunks used for voice generation.
    """
    if n <= 0:
        return [text]

    # Split into sentences
    sentences = re.split(r"(?<=[。！？」\n])", text)
    sentences = [s.strip() for s in sentences if s.strip()]

    if not sentences:
        return [text] + [""] * (n - 1)

    if len(sentences) <= n:
        # Fewer sentences than requested chunks — group 1:1 and pad
        chunks = sentences[:n]
        while len(chunks) < n:
            chunks.append("")
        return chunks

    # Distribute sentences evenly into n groups
    chunks: list[str] = []
    per_chunk = len(sentences) / n
    current = ""
    threshold = per_chunk
    for i, sent in enumerate(sentences):
        current += sent
        if (i + 1) >= threshold and len(chunks) < n - 1:
            chunks.append(current)
            current = ""
            threshold += per_chunk
    if current:
        chunks.append(current)

    # Safety: ensure exactly n
    while len(chunks) < n:
        chunks.append("")
    return chunks[:n]


def generate_shorts_metadata(title: str, text: str, author: str) -> dict[str, str]:
    """Generate engaging YouTube Shorts title and description using Gemini.

    Returns {"title": "...", "description": "..."}.
    Falls back to simple template if LLM fails.
    """
    import json as _json

    prompt = (
        "あなたはYouTube Shortsの怪談チャンネルのコピーライターです。\n"
        "以下の怪談のタイトルと内容から、YouTube Shortsに最適なタイトルと説明文を生成してください。\n\n"
        "参考スタイル:\n"
        "- 👻【怖い話】彼氏に車から置き去りにされた夜、私を助けてくれたのは…\n"
        "- 😱【怖い話】深夜のマンションで見た「5階の住人」の正体…\n\n"
        "ルール:\n"
        "- タイトルは50文字以内\n"
        "- 冒頭に絵文字を1つ使う（👻💀😱🔥など怖い系）\n"
        "- 【怖い話】タグを含める\n"
        "- ストーリーの核心に触れつつ、結末は隠す（…で終わる）\n"
        "- 説明文は2-3行で簡潔にストーリーのあらすじを書く\n"
        "- 説明文の最後にハッシュタグを5個程度含める（例: #怪談 #怖い話 #心霊 #ホラー #Shorts）\n"
        "- #Shorts は必ず含める\n\n"
        "JSON形式のみで返してください（```不要）:\n"
        '{"title": "...", "description": "..."}\n\n'
        f"タイトル: {title}\n"
        f"作者: {author}\n"
        f"本文:\n{text[:1500]}"
    )

    try:
        client = get_gemini_text()
        model_name = cfg_get("gemini_model") or "gemini-2.5-flash-lite"
        response = client.models.generate_content(model=model_name, contents=prompt)
        result_text = (response.text or "").strip()

        # Strip markdown code blocks if present
        result_text = re.sub(r"```json\s*", "", result_text)
        result_text = re.sub(r"```\s*", "", result_text)

        data = _json.loads(result_text)
        yt_title = data.get("title", "").strip()
        yt_desc = data.get("description", "").strip()

        if not yt_title or not yt_desc:
            raise ValueError("Empty title or description from LLM")

        log.info("[metadata] LLM生成タイトル: %s", yt_title)
        return {"title": yt_title, "description": yt_desc}

    except Exception as e:
        log.warning("[metadata] LLM生成失敗、テンプレート使用: %s", e)
        return {
            "title": f"👻【怖い話】{title}",
            "description": f"【怖い話】{title}\n\n#怪談 #怖い話 #心霊 #ホラー #Shorts",
        }
