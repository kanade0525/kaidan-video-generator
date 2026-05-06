from __future__ import annotations

import re

from app.config import get as cfg_get
from app.pipeline.retry import with_retry
from app.services.clients import get_gemini_text, get_openai
from app.utils.log import get_logger

log = get_logger("kaidan.text")


@with_retry(max_attempts=3, base_delay=5.0)
def process_text(
    text: str,
    prompt_template: str | None = None,
    model: str | None = None,
    use_ai_proofread: bool = False,
) -> str:
    """Convert kanji text to hiragana.

    Strategy: MeCab-first deterministic conversion (kanji→reading, particle
    は→わ, へ→え). If MeCab is unavailable, falls back to LLM-only conversion.

    When `use_ai_proofread=True`, the MeCab output is post-processed by
    Gemini to fix idiomatic readings, 連濁, and other patterns the
    dictionary missed. The AI sees the raw kanji text as reference but is
    instructed to preserve kept-as-kanji surfaces and structural newlines.
    """
    # 入力が NFD (分解形: か+゛など) で来るとMeCabが正しくトークン化できず、
    # 出力にも分解形 (例: か゛=2codepoint) が残ってしまう。スクレイピング元
    # サイトのHTMLが NFD だったり、保存パイプラインで分解されるケースに対応。
    # 標準/半角濁点も結合用に揃えてから NFC 正規化。
    import unicodedata
    text = (
        text.replace("゛", "゙").replace("゜", "゚")
        .replace("ﾞ", "゙").replace("ﾟ", "゚")
    )
    text = unicodedata.normalize("NFC", text)
    mecab_result = _mecab_to_hiragana(text)
    if mecab_result is not None:
        if use_ai_proofread:
            try:
                return _ai_proofread(mecab_result, text)
            except Exception as e:
                log.warning("AI校正に失敗、MeCab結果をそのまま返す: %s", e)
                return mecab_result
        return mecab_result

    log.warning("MeCab先行変換が失敗したためLLMフォールバックに切替")
    return _llm_convert(text, prompt_template, model)


_GEMINI_TRANSIENT_PATTERNS = ("500", "INTERNAL", "503", "UNAVAILABLE", "504", "DEADLINE_EXCEEDED", "RESOURCE_EXHAUSTED")


def _gemini_generate_with_retry(client, model_name: str, prompt: str, max_attempts: int = 4):
    """Call Gemini generate_content with retry on transient server errors.

    Gemini の SDK は 500 INTERNAL / 503 UNAVAILABLE / DEADLINE_EXCEEDED 等を
    一過性として返すことがある(Google 自身が retry を推奨)。指数バックオフで
    最大 max_attempts 回まで再試行。永続的な失敗はそのまま再送出して
    呼び出し元のフォールバックロジックに委ねる。
    """
    import time
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return client.models.generate_content(model=model_name, contents=prompt)
        except Exception as e:
            msg = str(e)
            transient = any(p in msg for p in _GEMINI_TRANSIENT_PATTERNS)
            if not transient or attempt == max_attempts:
                raise
            last_exc = e
            delay = min(2.0 * (2 ** (attempt - 1)), 30.0)
            log.warning(
                "Gemini generate_content 一過性エラー (試行 %d/%d): %s — %.1fs後再試行",
                attempt, max_attempts, msg[:120], delay,
            )
            time.sleep(delay)
    if last_exc:
        raise last_exc
    raise RuntimeError("unreachable")


def _ai_proofread(processed_text: str, raw_text: str) -> str:
    """Send MeCab-processed hiragana text + raw kanji to Gemini for proofreading.

    Returns the corrected text. On failure, the caller falls back to the
    uncorrected MeCab output. The prompt is taken from `cfg_get("ai_proofread_prompt")`
    so it can be tuned from the settings UI.
    """
    model_name = cfg_get("text_model") or "gemini-2.5-flash"
    if model_name.startswith("gpt"):
        # 使われる場面が限定的なので OpenAI 経路はサポート外
        log.warning("AI校正はGeminiのみ対応 (現在: %s)、スキップ", model_name)
        return processed_text

    prompt_template = cfg_get("ai_proofread_prompt") or ""
    if "{raw}" not in prompt_template or "{processed}" not in prompt_template:
        log.warning("ai_proofread_prompt に {raw} / {processed} がない、MeCab結果を返す")
        return processed_text

    client = get_gemini_text()
    prompt = prompt_template.format(raw=raw_text, processed=processed_text)
    response = _gemini_generate_with_retry(client, model_name, prompt)
    result = (response.text or "").strip()
    # コードブロック等の冗長要素を除去
    result = re.sub(r"```[\s\S]*?```", "", result).strip()
    if not result:
        log.warning("AI校正の応答が空、MeCab結果を返す")
        return processed_text

    # Gemini が NFD (分解形: は+゛) や 標準濁点 U+309B / 半角濁点 U+FF9E を
    # 出力するケースがあり、後段の規則適用で ば→わ+濁点 の破損が発生する。
    # 標準/半角濁点 を結合用濁点に揃えてから NFC で再合成 (わ゙→ば等に戻る)。
    import unicodedata
    # 標準濁点(U+309B) → 結合用(U+3099)、標準半濁点(U+309C) → 結合用(U+309A)
    # 半角濁点(U+FF9E) → 結合用、半角半濁点(U+FF9F) → 結合用
    result = (
        result
        .replace("゛", "゙")
        .replace("゜", "゚")
        .replace("ﾞ", "゙")
        .replace("ﾟ", "゚")
    )
    result = unicodedata.normalize("NFC", result)

    # ガード1: NFC化後も濁点/半濁点系コードポイントが孤立して残る場合は破損
    # とみなす (前文字が結合不能 or 元から不正配置)
    _DAKUTEN_CODEPOINTS = "゙゚゛゜ﾞﾟ"
    if any(ch in result for ch in _DAKUTEN_CODEPOINTS):
        log.warning("AI校正出力に孤立した濁点系文字が残存、MeCab結果を返す")
        return processed_text

    # ガード1.5: ば/び/ぶ/べ/ぼ が AI出力で大幅に減っていたら、ば→わ 誤変換が
    # 行われた可能性が高い (AI が は→わ 規則を ば内部の は にも誤適用)。
    _DAKUON_CHARS = "がぎぐげござじずぜぞだぢづでどばびぶべぼぱぴぷぺぽガギグゲゴザジズゼゾダヂヅデドバビブベボパピプペポ"
    in_dakuon = sum(processed_text.count(c) for c in _DAKUON_CHARS)
    out_dakuon = sum(result.count(c) for c in _DAKUON_CHARS)
    if in_dakuon > 0 and out_dakuon < in_dakuon * 0.9:
        log.warning(
            "AI校正で濁音文字が大幅減少 (%d → %d)、ば→わ誤変換の可能性、MeCab結果を返す",
            in_dakuon, out_dakuon,
        )
        return processed_text

    # ガード2: CJK互換・部首補助等の異体字ブロックを含む場合は破損とみなす
    if re.search(r"[⺀-⻿⼀-⿟豈-﫿]", result):
        log.warning("AI校正出力に異体字が混入、MeCab結果を返す")
        return processed_text

    # ガード3: 行数が大きく変わっている場合は信用しない (構造破壊の保険)
    raw_lines = processed_text.count("\n")
    new_lines = result.count("\n")
    if abs(raw_lines - new_lines) > max(2, raw_lines // 5):
        log.warning(
            "AI校正で改行数が大きく変動 (%d → %d)、MeCab結果を採用", raw_lines, new_lines
        )
        return processed_text

    # ガード4: 文字数が大幅に変動した場合(±30%超)も破損とみなす
    if len(result) < len(processed_text) * 0.7 or len(result) > len(processed_text) * 1.3:
        log.warning(
            "AI校正で文字数が大幅変動 (%d → %d)、MeCab結果を採用",
            len(processed_text), len(result),
        )
        return processed_text

    log.info("AI校正完了 (%d文字 → %d文字)", len(processed_text), len(result))
    return result


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
    # 「表」は単独だと「ひょう」と誤読される。物体の「表面/おもて側」の意味が
    # 物語文脈ではほぼ全てなので おもて 固定。代表/表彰/表面/表現 等の複合語は
    # MeCabが1トークンとして扱うため、surface単位の override は影響しない。
    "表": "おもて",
    # 「日本」は MeCab 既定で ニッポン だが、現代日本語の口語では にほん が標準。
    # 日本酒/日本海/日本国/日本一/日本人形 等の複合では 日本 が独立トークン化される
    # ので surface 一致でひらがな置換される。日本人/日本中/日本語 は 人/中/語 側の
    # 読みが文脈依存なので個別に compound_replacements で対応。
    "日本": "にほん",
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
    # ひらがな「床につ(く/き/いて/いた)」も同じ就寝慣用句。MeCab は 床→ユカ と
    # 誤読するので とこ に置換。「床につく」は現代日本語ではほぼ就寝の意。
    "床につ": "とこにつ",
    # 「非ず」(古語「あらず」) は MeCab が ヒ+ズ と誤読する。
    # 現代日本語で「非ず」と書かれる場合は 99% 古語の「あらず」読み。
    "非ず": "あらず",
    # 伏せ字 (匿名化プレースホルダー) のまる読み。3種類の Unicode 円文字に対応:
    #   ◯ U+25EF (LARGE CIRCLE)         — VOICEVOX が完全に無視 (致命的)
    #   〇 U+3007 (IDEOGRAPHIC ZERO)    — VOICEVOX が ゼロ と読む (誤り)
    #   ○ U+25CB (WHITE CIRCLE)          — VOICEVOX が マル と読む (これだけ自然)
    # いずれも怪談で人名隠しに使われるので、まる×N に統一して読み上げる。
    # 順序重要: 長い ◯◯ を先に置換してから単独 ◯ を処理 (奇数個ケース対応)。
    "◯◯◯": "まるまるまる",
    "◯◯": "まるまる",
    "◯": "まる",
    "〇〇〇": "まるまるまる",
    "〇〇": "まるまる",
    "〇": "まる",
    "○○○": "まるまるまる",
    "○○": "まるまる",
    "○": "まる",
    # 四字熟語「二束三文」(にそくさんもん)。MeCab は 二/束/三/文 と4分割して
    # ニ・タバ・サン・ブン と誤読する。
    "二束三文": "にそくさんもん",
    # 「見様見真似」(みようみまね)。MeCab は 見/様/見真似 と分割して
    # ミ・サマ・ミマネ と誤読する。
    "見様見真似": "みようみまね",
    # 「心做し(か)」(こころなし(か))。做 は 作 の異体字で MeCab に読みがなく、
    # 漢字のまま残ってしまう。
    "心做し": "こころなし",
    # 「何でも」(なんでも)。VOICEVOX は漢字「何」を 何か/何が/何と では正しく
    # ナニ と読むが、「何でも」だけは ナニデモ と誤読する(本来 ナンデモ)。
    # ひらがな「なんでも」に展開して回避。
    "何でも": "なんでも",
    # 「日本語」(にほんご)。MeCab は 日本 を国名としてニッポンで返すため、
    # 日本+語 → にっぽんご になる。日常用法は にほんご が標準。
    "日本語": "にほんご",
    # 「ぎゅうぎゅう詰め」は連濁で づめ（ぎゅうぎゅうづめ）。MeCab は 詰め 単独の
    # 読み ツメ を返すので連濁が起きない。
    "ぎゅうぎゅう詰め": "ぎゅうぎゅうづめ",
    # 四字熟語「二人三脚」は ににんさんきゃく が定着した読み。MeCab は 二人(フタリ)
    # + 三脚(サンキャク) と分割し、フタリ サンキャク になる。
    "二人三脚": "ににんさんきゃく",
    # 「二人組」は ふたりぐみ。VOICEVOX は漢字を渡すと ニニングミ と硬く読むが、
    # 物語文脈では ふたりぐみ が自然。MeCab は フタリグミ を1トークンで返すが
    # counter span 保護で漢字保持されてしまうため、先に ふたりぐみ に置換する。
    "二人組": "ふたりぐみ",
    # 「日本人形」は「日本+人形」(にんぎょう)で 1単語。「日本人」置換が先に
    # マッチして「にほんじん形」になるのを防ぐため、「日本人」より長い「日本人形」
    # をdict順で前に置く（Python3.7+ は挿入順保持）。
    "日本人形": "にほんにんぎょう",
    # 国名+人 は MeCab が 国名+「人(ニン)」と分割して にん 読みになるが、
    # 自然な現代日本語では じん が正しい (日本人=にほんじん, 外国人=がいこくじん 等)。
    "日本人": "にほんじん",
    "外国人": "がいこくじん",
    "アメリカ人": "アメリカじん",
    "中国人": "ちゅうごくじん",
    "韓国人": "かんこくじん",
    # 「日本中」は にほんじゅう (国全体の意)。MeCab は 日本(ニッポン)+中(チュウ)
    # と分解する。
    "日本中": "にほんじゅう",
    # 「一点張り」は いってんばり (促音化)。MeCab は 一(イチ)+点(テン)+張り(バリ)
    # と分解して いちてんばり にしてしまう。
    "一点張り": "いってんばり",
    # 「仏間」は ぶつま。単独/「に」前置時は MeCab が 仏間(ブツマ) と1トークン化
    # するが、「で」前置時など特定文脈で 仏(フツ)+間(カン) に分解されて ふつかん
    # と誤読する。常時 ぶつま に固定。
    "仏間": "ぶつま",
    # 「話大〜」(話大好き/話大事 等) は VOICEVOX が 話(漢字)+だい(ひらがな) を
    # 「話題(ワダイ)」と誤合成する。読点を挿入して境界を明示する。
    "話大": "話、大",
    # 「四十九日」(仏教の四十九日忌) は しじゅうくにち と読むのが慣用。
    # VOICEVOX は漢字「四十九日」を ヨンジュウクニチ と読んでしまう
    # (一般数詞読み)。ひらがなに展開して矯正。
    "四十九日": "しじゅうくにち",
    # 「部屋中」(部屋全体) の 中 は じゅう (中=throughout)。MeCab は チュウ を返す
    # ので「へやちゅう」になり、加えて VOICEVOX が「へ」を助詞え誤解析して
    # 「ながらへやちゅう」を「ナガラエ・ヤチュウ」(=夜中) と読んで意味が壊れる。
    "部屋中": "部屋じゅう",
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
    # 話 の活用・複合形。surface単位で KEEP。
    # 「で話す/を話し始める」等で「はなす/はなし」が particle 直後に来ると
    # VOICEVOX が は→ワ 誤解析 (デワナス/デワナシ)。
    "話す",   # 終止形 (で話す → デワナス 対策)
    "話し",   # 連用形 (話した/話し始め → デワナシ... 対策)
    "話し声",  # 単独トークン化される複合語名詞
    "話し合い",  # 同上
    "話しかける",  # 同上（動詞）
    "話しかけ",  # 同上（連用形）
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
    # 離れ: ひらがな「はなれ」が助詞「に/で」の直後にあると VOICEVOX が
    # 「は」を助詞「ワ」と誤解析し「ニワ・ナレテ」(ハ音欠落)になる。surface
    # 「離れ」(連用形: 離れて/離れた/離れない) と「離れる」(終止形/連体形) を
    # 漢字保持して回避。
    "離れ",
    "離れる",
    # 吐き気: ひらがな「はきけ」が助詞「と/に」の直後にあると VOICEVOX が
    # 「は」を助詞「ワ」と誤解析し「ト・ワキケ」(ハ音欠落)になる。
    "吐き気",
    # 入る: ひらがな「はいる」が「て+はいる」(...あけてはいる) の文末文脈で
    # VOICEVOX が「は」を助詞「ワ」と誤解析し「アケテ・ワ・イル」(意味壊れ)になる。
    # 連用形 入っ は既に保持済み。終止形 入る も追加。
    "入る",
    # 神奈川: ひらがな「かながわけん」が VOICEVOX で カナガ+ワケン に分割され
    # 「がわ」を が(particle)+わ(particle) と誤解析する。固有名詞は漢字保持。
    "神奈川",
    # 試験: 動物試験場 のような複合語で「しけんじょう」になると VOICEVOX が
    # ドオブツ+シ+ケンジョオ と妙に分解する。試験 を漢字保持して
    # ドオブツシケン+ジョオ に矯正。
    "試験",
    # 髪: ひらがな「かみ」が「と+かみ」(見ると髪) で VOICEVOX が と+か(particle)+み
    # に分割される(「とか」=列挙助詞と誤認)。漢字保持で境界明示。
    "髪",
    # 保管: ひらがな「ほかんこ」が VOICEVOX で ホ+カンコ と妙に分割される
    # (おそらく内部辞書の干渉)。保管 を漢字保持して境界明示。
    "保管",
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
    # 体: 霊体/人形/仏像/遺体 等の助数詞。MeCab は 「四体」を 四(シ)+体(タイ)
    # に分解して「したい」を出力するが、それが「死体」と同音になり怪談文脈で
    # 致命的に紛らわしい。漢字保持で VOICEVOX に「ヨンタイ」と読ませる。
    "体",
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

    Uses unidic-lite explicitly to keep tokenization stable across environments
    where unidic full may be installed (e.g., for accent estimation). Without
    this, verb 連用形 (踊って) get re-tokenized as 基本形 + te (踊る + て)
    causing output like 「おどるて」 instead of 「おどって」.

    Newlines in the input are preserved by processing each line separately —
    MeCab silently drops \n during tokenization, so single-pass conversion
    would collapse multi-paragraph stories into one line.
    """
    try:
        import MeCab  # noqa: F401
        import unidic_lite  # noqa: F401
    except ImportError:
        log.warning("MeCab未インストール")
        return None

    if "\n" in text:
        parts = text.split("\n")
        converted = [_mecab_to_hiragana_segment(p) for p in parts]
        if any(c is None for c in converted):
            return None
        return "\n".join(converted)  # type: ignore[arg-type]
    return _mecab_to_hiragana_segment(text)


def _mecab_to_hiragana_segment(text: str) -> str | None:
    """Convert a single line (no newline) to hiragana via MeCab."""
    try:
        import MeCab
        import unidic_lite
    except ImportError:
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
        tagger = MeCab.Tagger(f"-d {unidic_lite.DICDIR}")
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
        import unidic_lite

        tagger = MeCab.Tagger(f"-d {unidic_lite.DICDIR}")
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
