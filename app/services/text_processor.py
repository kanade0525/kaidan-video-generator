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


# Hardcoded defaults for the narration dictionaries. These are merged with
# user additions from config.toml (`reading_overrides` / `compound_replacements`
# / `keep_as_kanji`) at runtime, so the UI can add entries without redeploying.

# Surface → hiragana overrides for readings MeCab gets stylistically wrong.
_DEFAULT_READING_OVERRIDES: dict[str, str] = {
    "私": "わたし",  # MeCab default: わたくし
}

# Compound words that MeCab splits into multiple tokens, producing wrong
# per-token readings (e.g. お父さん → お+父+さん → おちちさん).
# Applied as a string replacement BEFORE tokenization.
_DEFAULT_COMPOUND_REPLACEMENTS: dict[str, str] = {
    "お父さん": "おとうさん",
    "お母さん": "おかあさん",
    "お姉さん": "おねえさん",
    "お兄さん": "おにいさん",
    "お爺さん": "おじいさん",
    "お婆さん": "おばあさん",
    # "の臭い" は名詞(におい)ほぼ確定だが、後続の と/だ などで MeCab が
    # 形容詞(くさい)に倒れることがある。先に置換して曖昧性を除去する。
    # 形容詞用法(例: 「魚が臭い」「靴が臭くて」)はMeCabが正しく判別するので
    # 置換対象外。
    "の臭い": "のにおい",
    # 「間」の慣用読み ま (MeCabは多くの場合「あいだ」を返すが、下記は ま が
    # 固定): いつの間(に)(か), 知らない/ぬ間に, あっという間。
    # 少しの間/その間/この間 等の「あいだ」読みは MeCab に任せる。
    "いつの間にか": "いつのまにか",
    "いつの間に": "いつのまに",
    "知らない間に": "しらないまに",
    "知らぬ間に": "しらぬまに",
    "あっという間": "あっというま",
    # カウンター+位 は「〜くらい(約)」の意。MeCabは 代位/年位 等を1トークン化して
    # ダイイ/ネンイ と誤読するので、先に 位→くらい に展開して数字+カウンターと
    # くらいに分離する。(「10位」「1位」等のランキング用 位 は数詞+位のみで
    # カウンターを挟まないため影響なし)
    "代位": "代くらい",
    "年位": "年くらい",
    "時位": "時くらい",
    "分位": "分くらい",
    "秒位": "秒くらい",
    "人位": "人くらい",
    "日位": "日くらい",
    "回位": "回くらい",
    "度位": "度くらい",
    "月位": "月くらい",
    "歳位": "歳くらい",
    "才位": "才くらい",
    "週間位": "週間くらい",
    "日間位": "日間くらい",
    "年間位": "年間くらい",
    "時間位": "時間くらい",
    "分間位": "分間くらい",
    "本位": "本くらい",
    "個位": "個くらい",
    "枚位": "枚くらい",
    "匹位": "匹くらい",
    "階位": "階くらい",
    "台位": "台くらい",
    "円位": "円くらい",
    # 「にはいって」「ではいって」等 particle+はいって は VOICEVOX が は→ワ と
    # 助詞誤解析して「ワイッテ」と読むのを防ぐ。漢字「入って」に戻すと _KEEP_AS_KANJI
    # の「入っ」ルールで漢字保持され、VOICEVOX が正しく ハイッテ と読む。
    # (「家に入って」のように原文が既に漢字の場合は影響なし)
    "にはいって": "に入って",
    "ではいって": "で入って",
    "にはいった": "に入った",
    "ではいった": "で入った",
    # 「床に就く」(就寝の意) の 床 は とこ、就 は つ。MeCab は 床→ユカ、
    # 就く→ズク と誤解析する。床に就(く/き/いた) すべてに効くよう接頭3文字で置換。
    "床に就": "とこにつ",
    # 「非ず」(古語「あらず」) は MeCab が ヒ+ズ と誤読する。
    # 現代日本語で「非ず」と書かれる場合は 99% 古語の「あらず」読み。
    "非ず": "あらず",
}

# Kanji to keep as-is (skip hiragana conversion) because VOICEVOX mis-reads
# the hiragana form as a particle は. Verified via VOICEVOX audio_query API:
# many は-start 2-mora nouns get their first は interpreted as a particle
# when preceded by adjective/genitive patterns (e.g., 「こわいはなし」 →
# コワイ**ワ**ナシ, 「きれいなはだ」 → キレイナ**ワ**ダ). Keeping the kanji
# disambiguates for VOICEVOX.
_DEFAULT_KEEP_AS_KANJI: set[str] = {
    "母",   # はは → ワワ
    "話",   # はなし → ワナシ
    "花",   # はな → ワナ
    "鼻",   # はな → ワナ
    "羽",   # はね → ワネ
    "肌",   # はだ → ワダ
    "箱",   # はこ → ワコ
    "葉",   # は → ワ
    "歯",   # は → ワ
    "腹",   # はら → ワラ (な-連体形文脈で誤読)
    "墓",   # はか → ワカ (句読点後の「、はかわ…」で誤読)
    # 入っ: 入って/入った の surface。ひらがな「はいって」が「は+いって」と
    # 誤解析され「ワイッテ」になるのを防ぐ。入る/入った/入ろう 等 他の活用と
    # 入学/入れる/気に入る 等の複合語・別語は surface が異なるため影響なし。
    "入っ",
    # 何: MeCab は常に ナン を返すが、VOICEVOX は漢字なら文脈で ナニを/ナンラ/
    # ナンネン 等に使い分ける。ひらがな「なん」を渡すと VOICEVOX も常に ナン。
    "何",
    # 後: 文境界(?/。/？)直後の「後から」を MeCab が 接尾辞/ゴ と誤解析する
    # ことがある。VOICEVOX は漢字なら全文脈で正読 (後ろ→ウシロ, 後から→
    # アトカラ, 最後→サイゴ, 午後→ゴゴ, 後半→コオハン)。
    "後",
}

# Counter kanji whose correct reading depends on 促音/連濁/lexicalized rules
# (e.g., 一泊→いっぱく, 三本→さんぼん, 二日→ふつか). MeCab tokenizes 数詞+カウンター
# separately and concatenates bare readings, producing 「いちはく/さんほん/ふたか」.
# VOICEVOX handles these correctly when given the kanji form, so we keep the
# counter kanji as-is only when preceded by a number (数詞).
_COUNTER_KANJI: set[str] = {
    "日", "人", "月", "年", "本", "泊", "時", "個", "匹", "回",
    "分", "週", "階", "度", "枚", "台", "冊", "歳", "才", "歩",
    "杯", "軒", "着", "組", "口", "戸", "袋", "缶", "つ", "番",
    "秒", "円", "通",
}


def _reading_overrides() -> dict[str, str]:
    user = cfg_get("reading_overrides") or {}
    return {**_DEFAULT_READING_OVERRIDES, **user}


def _compound_replacements() -> dict[str, str]:
    user = cfg_get("compound_replacements") or {}
    return {**_DEFAULT_COMPOUND_REPLACEMENTS, **user}


def _keep_as_kanji() -> set[str]:
    user = cfg_get("keep_as_kanji") or []
    return _DEFAULT_KEEP_AS_KANJI | set(user)


def _mecab_to_hiragana(text: str) -> str | None:
    """Convert text to hiragana using MeCab (kanji→reading + particle は/へ→わ/え).

    Returns None if MeCab is unavailable so the caller can fall back.
    """
    try:
        import MeCab
    except ImportError:
        log.warning("MeCab未インストール")
        return None

    # Apply furigana annotations 「漢字（ふりがな）」 first: author-provided
    # readings override everything else (e.g., 優曇華（うどんげ） → うどんげ).
    text = _apply_furigana(text)

    for src, dst in _compound_replacements().items():
        text = text.replace(src, dst)

    reading_overrides = _reading_overrides()
    keep_as_kanji = _keep_as_kanji()

    # Protect "数字+カウンター漢字" spans so MeCab does not split them and
    # emit wrong per-token readings (e.g. 一泊二日 → いち泊ふた日 with bad
    # 促音/連濁). VOICEVOX reads the original kanji form correctly.
    text, counter_placeholders = _protect_counter_spans(text)

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

            if surface in keep_as_kanji:
                output.append(surface)
            elif surface in reading_overrides:
                output.append(reading_overrides[surface])
            elif has_kanji and pron:
                output.append(_katakana_to_hiragana(pron))
            elif is_particle and surface == "は":
                output.append("わ")
            elif is_particle and surface == "へ":
                output.append("え")
            else:
                output.append(surface)
            node = node.next

        result = "".join(output)
        for placeholder, original in counter_placeholders.items():
            result = result.replace(placeholder, original)
        return result
    except Exception as e:
        log.warning("MeCab変換エラー: %s", e)
        return None


# Counter spans may have a trailing suffix kanji that belongs to the counter
# phrase lexically (e.g., 3日"間", 6年"生", 5分"間"). Include these so MeCab
# does not re-tokenize them standalone and pick the wrong reading.
# 「何」 is included in the number-like prefix class so 何人/何年/何回 等も
# 1トークンの カウンタースパンとして保護される (VOICEVOX は漢字組合せを見て
# ナン+カウンター の正しい読みに倒す)。
_COUNTER_SPAN_RE = re.compile(
    r"[0-9０-９一二三四五六七八九十百千万何]+"
    r"(?:[" + "".join(_COUNTER_KANJI) + r"][間生]?)+"
)


_FURIGANA_RE = re.compile(r"([一-龯々〆]+)[（(]([ぁ-んァ-ヴー]+)[）)]")


def _apply_furigana(text: str) -> str:
    """Replace 「漢字（ふりがな）」 with the furigana reading, and propagate each
    author-provided reading to all other occurrences of the same kanji in the
    text (authors typically annotate only the first mention).

    Also handles the common case where okurigana follows the closing paren
    and duplicates the last mora of the furigana (e.g., 「掴（つかみ）み取って」
    → 「つかみ取って」, not 「つかみみ取って」).
    """
    # First pass: collect kanji → furigana map from annotations, and build
    # text with parens stripped (keep the kanji for second-pass replacement).
    mapping: dict[str, str] = {}
    out: list[str] = []
    i = 0
    for m in _FURIGANA_RE.finditer(text):
        out.append(text[i:m.start()])
        kanji = m.group(1)
        kana = m.group(2)
        mapping[kanji] = kana
        out.append(kanji)
        i = m.end()
        # If the char right after the paren matches the last mora of the
        # furigana, remember to skip it during second-pass replacement of
        # this kanji. Handled by storing the kana → consumption hint.
        if i < len(text) and text[i] == kana[-1]:
            i += 1  # drop the duplicate mora here too
    out.append(text[i:])
    stripped = "".join(out)

    # Second pass: replace every occurrence of each annotated kanji with its
    # furigana reading. Longest kanji first to avoid partial overshadowing.
    for kanji in sorted(mapping, key=len, reverse=True):
        stripped = stripped.replace(kanji, mapping[kanji])
    return stripped


def _protect_counter_spans(text: str) -> tuple[str, dict[str, str]]:
    """Replace 数字+カウンター漢字 spans with placeholders to shield them from MeCab.

    Uses Private Use Area codepoints as placeholders since they are guaranteed
    not to appear in real text and are treated as opaque symbols by MeCab.
    """
    placeholders: dict[str, str] = {}

    def _repl(m: re.Match) -> str:
        key = f"\uE000{len(placeholders):04d}\uE001"
        placeholders[key] = m.group(0)
        return key

    protected = _COUNTER_SPAN_RE.sub(_repl, text)
    return protected, placeholders


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
        keep_as_kanji = _keep_as_kanji()
        output: list[str] = []
        node = tagger.parseToNode(text)
        while node:
            surface = node.surface
            if not surface:
                node = node.next
                continue

            feature = node.feature.split(",")
            reading = _extract_reading(feature)

            if surface in keep_as_kanji:
                output.append(surface)
            elif re.search(r"[一-龯々〆]", surface) and reading:
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
