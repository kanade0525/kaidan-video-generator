from __future__ import annotations

import os
import re
import wave
from pathlib import Path

import requests

from app.config import get as cfg_get
from app.pipeline.retry import with_retry
from app.services.text_processor import _sentences
from app.utils.log import get_logger

log = get_logger("kaidan.voice")

VOICEVOX_HOST = os.environ.get("VOICEVOX_HOST", "http://localhost:50021")


@with_retry(max_attempts=5, base_delay=3.0)
def get_speakers() -> list[dict]:
    """Get available speakers from VOICEVOX."""
    host = VOICEVOX_HOST
    r = requests.get(f"{host}/speakers", timeout=10)
    r.raise_for_status()
    return r.json()


def get_speaker_name(speaker_id: int | None = None) -> str:
    """Get the character name and style for a speaker ID."""
    sid = speaker_id if speaker_id is not None else cfg_get("speaker_id")
    try:
        speakers = get_speakers()
        for speaker in speakers:
            for style in speaker.get("styles", []):
                if style["id"] == sid:
                    return f'{speaker["name"]}（{style["name"]}）'
    except Exception:
        pass
    return f"speaker_id={sid}"


# AquesTalk 風記法の装飾記号。モーラ列だけを比べたいので取り除く。
#   ' = アクセント核 / _ = 無声化 / / = アクセント句区切り / 、 = ポーズ
_KANA_DECORATION_RE = re.compile(r"[\'_/、。 \u3000]")


def normalize_speech_text(text: str, newline_mode: str | None = None) -> str:
    """VOICEVOX に渡す前に読み上げテキストを整える。

    従来は改行を無条件に「。」へ変換していたため、行末が既に「。」の場合に
    「。。」が生まれて不自然に長い間が空いていた。連続した句読点を畳み、
    改行の扱いを設定で選べるようにする。

    newline_mode: "period" = 。(長い間) / "comma" = 、(短い間) / "none" = 直結
    """
    mode = newline_mode if newline_mode is not None else (cfg_get("voice_newline_mode") or "period")
    separator = {"period": "。", "comma": "、", "none": ""}.get(mode, "。")

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.strip() for ln in text.split("\n")]
    lines = [ln for ln in lines if ln]

    parts: list[str] = []
    for i, line in enumerate(lines):
        parts.append(line)
        if i == len(lines) - 1 or not separator:
            continue
        # 行末が既に文末記号なら区切りを足さない (「。。」を作らない)
        if line[-1] not in "。、！？!?":
            parts.append(separator)
    joined = "".join(parts)

    # 残った重複句読点を畳む (原文由来の「、。」「。。」も対象)
    joined = re.sub(r"[。、]*。[。、]*", "。", joined)
    joined = re.sub(r"、{2,}", "、", joined)
    return joined.strip()


# モーラ → 母音。長音の表記ゆれ (ヘイテン / ヘエテン) を吸収するために使う。
_VOWEL_ROWS = {
    "a": "アカサタナハマヤラワガザダバパャヮファヴ",
    "i": "イキシチニヒミリギジヂビピフィ",
    "u": "ウクスツヌフムユルグズヅブプュヴ",
    "e": "エケセテネヘメレゲゼデベペェフェ",
    "o": "オコソトノホモヨロヲゴゾドボポョフォ",
}
_MORA_VOWEL = {ch: v for v, chars in _VOWEL_ROWS.items() for ch in chars}
_SMALL_KANA = "ァィゥェォャュョヮ"


def _split_moras(kana: str) -> list[str]:
    """カタカナ列をモーラ単位に分ける (拗音の小書き文字は直前にくっつける)。"""
    moras: list[str] = []
    for ch in kana:
        if ch in _SMALL_KANA and moras:
            moras[-1] += ch
        else:
            moras.append(ch)
    return moras


def _mora_signature(kana: str) -> str:
    """AquesTalk 風記法から「読みの中身」だけを取り出す。

    2つの原稿 (ひらがな版 / 漢字混じり版) が同じ読みかの判定に使う。無視するのは:
      - アクセント核 / アクセント句の切れ目 / ポーズ / 無声化記号
      - 長音の表記ゆれ。VOICEVOX は「へいてん」を ヘイテン、「閉店」を ヘエテン と
        返すが、どちらも同じ語の読み (むしろ漢字側が自然な発音)。エ段+イ、
        オ段+ウ、長音符を正規化して同一視する。
    読みが1モーラでも違えば別物と判定される点は変わらない。
    """
    stripped = _KANA_DECORATION_RE.sub("", kana or "")
    moras = _split_moras(stripped)

    out: list[str] = []
    prev_vowel = ""
    for mora in moras:
        base = mora[0]
        if mora == "ー":
            # 長音符は直前の母音の繰り返しとして展開する
            mora = {"a": "ア", "i": "イ", "u": "ウ", "e": "エ", "o": "オ"}.get(
                prev_vowel, "ー",
            )
            base = mora[0]
        elif mora == "イ" and prev_vowel == "e":
            mora, base = "エ", "エ"
        elif mora == "ウ" and prev_vowel == "o":
            mora, base = "オ", "オ"
        out.append(mora)
        prev_vowel = _MORA_VOWEL.get(mora[-1], _MORA_VOWEL.get(base, ""))
    return "".join(out)


def _audio_query(text: str, speaker_id: int) -> dict:
    """VOICEVOX の audio_query を取得する。"""
    r = requests.post(
        f"{VOICEVOX_HOST}/audio_query",
        params={"text": text, "speaker": speaker_id},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def _apply_query_params(
    query: dict,
    speed: float | None = None,
    pitch: float | None = None,
    intonation: float | None = None,
    volume: float | None = None,
) -> dict:
    """設定値を AudioQuery に反映する。"""
    if speed is not None:
        query["speedScale"] = speed
    elif cfg_get("speed"):
        query["speedScale"] = cfg_get("speed")

    if pitch is not None:
        query["pitchScale"] = pitch
    elif cfg_get("pitch"):
        query["pitchScale"] = cfg_get("pitch")

    if intonation is not None:
        query["intonationScale"] = intonation
    elif cfg_get("intonation"):
        query["intonationScale"] = cfg_get("intonation")

    if volume is not None:
        query["volumeScale"] = volume
    elif cfg_get("volume"):
        query["volumeScale"] = cfg_get("volume")

    # 「間」の調整。engine のバージョンによっては存在しないフィールドがあるので、
    # audio_query が返したキーだけを上書きする。
    pre = cfg_get("pre_phoneme_length")
    if pre is not None and pre >= 0 and "prePhonemeLength" in query:
        query["prePhonemeLength"] = pre
    post = cfg_get("post_phoneme_length")
    if post is not None and post >= 0 and "postPhonemeLength" in query:
        query["postPhonemeLength"] = post
    pause_scale = cfg_get("pause_length_scale")
    if pause_scale is not None and pause_scale > 0 and "pauseLengthScale" in query:
        query["pauseLengthScale"] = pause_scale

    return query


def _synthesize(query: dict, speaker_id: int) -> bytes:
    r = requests.post(
        f"{VOICEVOX_HOST}/synthesis",
        params={"speaker": speaker_id},
        json=query,
        timeout=120,
    )
    r.raise_for_status()
    return r.content


@with_retry(max_attempts=3, base_delay=2.0)
def text_to_speech(
    text: str,
    speaker_id: int | None = None,
    speed: float | None = None,
    pitch: float | None = None,
    intonation: float | None = None,
    volume: float | None = None,
    accent_source: str | None = None,
) -> bytes:
    """Convert text to speech via VOICEVOX API.

    `accent_source` には同じ内容の「漢字混じり原稿」を渡せる。読みが `text`
    と完全に一致する場合だけそちらで合成し、アクセントを自然にする。1モーラ
    でも違えば `text` (ひらがな原稿) で合成するので、読みが変わることはない。
    """
    sid = speaker_id if speaker_id is not None else cfg_get("speaker_id")

    clean_text = normalize_speech_text(text)
    query = _audio_query(clean_text, sid)

    if accent_source and (cfg_get("voice_accent_mode") or "hiragana") == "verified_kanji":
        query = _maybe_use_accent_source(query, text, accent_source, sid)

    _apply_query_params(query, speed, pitch, intonation, volume)
    return _synthesize(query, sid)


def merge_verified_sentences(
    hiragana_text: str, mixed_text: str, speaker_id: int,
) -> str | None:
    """文ごとに読みを照合し、一致した文だけ漢字混じりに差し替えた原稿を返す。

    チャンク丸ごとで比べると、1 文でも読みが食い違えばチャンク全体が
    ひらがなに戻ってしまう (実測で採用率 49%)。文単位なら 85% まで上がる。
    文数が合わなければ None を返し、呼び出し側がチャンク単位の判定に落とす。
    """
    hira_sents = _sentences(hiragana_text)
    mixed_sents = _sentences(mixed_text)
    if not hira_sents or len(hira_sents) != len(mixed_sents):
        return None

    picked: list[str] = []
    for hira, mixed in zip(hira_sents, mixed_sents, strict=True):
        if hira == mixed:
            picked.append(hira)
            continue
        hira_sig = _mora_signature(_audio_query(hira, speaker_id).get("kana", ""))
        mixed_sig = _mora_signature(_audio_query(mixed, speaker_id).get("kana", ""))
        if hira_sig == mixed_sig:
            picked.append(mixed)
            _ACCENT_STATS["sentences_matched"] += 1
        else:
            picked.append(hira)
            _ACCENT_STATS["sentences_mismatched"] += 1
    return "".join(picked)


def _maybe_use_accent_source(
    query: dict, text: str, accent_source: str, speaker_id: int,
) -> dict:
    """漢字混じり原稿の読みが一致する範囲だけ採用した AudioQuery を返す。

    失敗しても必ず元の `query` を返す — アクセント改善は「うまくいけば得」の
    最適化であって、ここで例外を投げてナレーション生成を止めてはいけない。
    最後に必ずチャンク全体の読みを突き合わせるので、文をつなぎ直したことで
    読みが変わった場合もひらがな原稿に戻る。
    """
    try:
        alt_text = normalize_speech_text(accent_source)
        if not alt_text:
            return query
        base_sig = _mora_signature(query.get("kana", ""))

        base_text = normalize_speech_text(text)
        candidate = merge_verified_sentences(base_text, alt_text, speaker_id)
        if candidate is None:
            # 文数が合わない (原稿の対応が崩れている) のでチャンク単位で判定
            candidate = alt_text
        if not candidate:
            return query
        if candidate == base_text:
            # どの文も採用できなかった。問い合わせ直しても同じ結果なので省く。
            _ACCENT_STATS["mismatched"] += 1
            return query

        candidate_query = _audio_query(candidate, speaker_id)
        if _mora_signature(candidate_query.get("kana", "")) == base_sig:
            _ACCENT_STATS["matched"] += 1
            return candidate_query
        _ACCENT_STATS["mismatched"] += 1
        log.debug("アクセント原稿の読みが不一致、ひらがな原稿を使用: %s", candidate[:40])
    except Exception as e:  # noqa: BLE001 — 最適化なので失敗は握りつぶす
        _ACCENT_STATS["error"] += 1
        log.debug("アクセント原稿の照合に失敗、ひらがな原稿を使用: %s", e)
    return query


# 直近のナレーション生成でアクセント原稿がどれだけ採用されたかの集計。
# generate_narration がログに出す (完全自動運転時の効き目の可視化)。
_ACCENT_STATS: dict[str, int] = {
    "matched": 0,
    "mismatched": 0,
    "error": 0,
    "sentences_matched": 0,
    "sentences_mismatched": 0,
}


def reset_accent_stats() -> None:
    for k in _ACCENT_STATS:
        _ACCENT_STATS[k] = 0


def get_accent_stats() -> dict[str, int]:
    return dict(_ACCENT_STATS)


def repair_dropped_title_reading(title: str, speaker_id: int | None = None) -> str:
    """タイトルに読めない漢字があれば、ひらがな版に差し替える。

    VOICEVOX は辞書に無い漢字を**無音で読み飛ばす**。「彼に憑く部屋」は
    「憑」が落ちて「カレニ、ク、ヘヤ」になっていた。

    ただしタイトルを一律ひらがな化するのは駄目で、そちらはそちらで
    「箱根」→ワコネ (助詞は→わ の誤爆)、「彼岸花」→ヒガンハナ のように
    悪化する。そこで **モーラが落ちているときだけ** 差し替える。
    読みが違うだけで長さが同じケースは触らない (個別に title_furigana で直す)。
    """
    sid = speaker_id if speaker_id is not None else cfg_get("speaker_id")
    try:
        from app.services.text_processor import _mecab_to_hiragana

        raw_sig = _mora_signature(_audio_query(normalize_speech_text(title), sid).get("kana", ""))
        hiragana = _mecab_to_hiragana(title)
        if not hiragana or hiragana == title:
            return title
        hira_sig = _mora_signature(
            _audio_query(normalize_speech_text(hiragana), sid).get("kana", ""),
        )
    except Exception as e:  # noqa: BLE001 — 判定できなければ原題のまま
        log.warning("タイトル読みの点検に失敗、原題のまま読み上げます: %s", e)
        return title

    if len(raw_sig) < len(hira_sig):
        log.info(
            "タイトルに読めない文字があるためひらがなで読み上げます: %s → %s "
            "(%s → %s)", title, hiragana, raw_sig, hira_sig,
        )
        return hiragana
    return title


def generate_title_audio(title: str, output_path: Path, title_furigana: str | None = None, speed: float | None = None) -> Path:
    """Generate title narration audio via VOICEVOX.

    `title_furigana` が設定されていればそれを最優先で読む (手動指定が最強)。
    未設定のときだけ、読み落としが起きていないか点検する。
    """
    text = title_furigana if title_furigana else repair_dropped_title_reading(title)
    log.info("タイトル読み上げ音声生成中: %s", text)
    audio_data = text_to_speech(text, speed=speed)
    output_path.write_bytes(audio_data)
    log.info("タイトル音声保存: %s", output_path.name)
    return output_path


def generate_narration(
    chunks: list[str], output_dir: Path, progress_callback=None, speed: float | None = None,
    source_chunks: list[str] | None = None,
) -> Path:
    """Generate narration for all chunks and concatenate.

    `source_chunks` にひらがなチャンクと 1:1 対応する「漢字混じり原稿」を渡すと、
    読みが一致するチャンクだけそちらで合成してアクセントを自然にする
    (`voice_accent_mode = "verified_kanji"` のとき)。件数が合わない場合は
    アクセント原稿を使わない。
    """
    # Clean up stale narration files from previous runs to prevent data corruption
    existing = sorted(output_dir.glob("narration_*.wav"))
    if existing:
        log.info("既存ナレーションファイル %d 個を削除", len(existing))
        for old_file in existing:
            old_file.unlink(missing_ok=True)

    audio_files = []

    use_accent_source = bool(source_chunks) and len(source_chunks) == len(chunks)
    if source_chunks and not use_accent_source:
        log.warning(
            "アクセント原稿のチャンク数が不一致 (%d != %d)、ひらがな原稿のみで生成",
            len(source_chunks), len(chunks),
        )
    reset_accent_stats()

    for i, chunk in enumerate(chunks):
        log.info("音声生成中 (%d/%d)", i + 1, len(chunks))
        if progress_callback:
            progress_callback(i, len(chunks))
        accent_source = source_chunks[i] if use_accent_source else None
        audio_data = text_to_speech(chunk, speed=speed, accent_source=accent_source)
        audio_path = output_dir / f"narration_{i:04d}.wav"
        audio_path.write_bytes(audio_data)
        audio_files.append(audio_path)
        log.info("保存: %s", audio_path.name)

    if use_accent_source:
        stats = get_accent_stats()
        chunk_total = stats["matched"] + stats["mismatched"] + stats["error"]
        sent_total = stats["sentences_matched"] + stats["sentences_mismatched"]
        if chunk_total:
            log.info(
                "アクセント原稿の採用: %d/%d チャンク, 漢字を残した文 %d/%d "
                "(照合エラー %d)",
                stats["matched"], chunk_total,
                stats["sentences_matched"], sent_total, stats["error"],
            )

    # Concatenate
    if progress_callback:
        progress_callback(len(chunks), len(chunks))
    output_path = output_dir.parent / "narration_complete.wav"
    concatenate_wav(audio_files, output_path)
    log.info("結合完了: %s", output_path)
    return output_path


def concatenate_wav(files: list[Path], output: Path) -> None:
    """Concatenate WAV files into a single file."""
    if not files:
        return

    with wave.open(str(files[0]), "rb") as first:
        params = first.getparams()

    with wave.open(str(output), "wb") as out:
        out.setparams(params)
        for f in files:
            with wave.open(str(f), "rb") as inp:
                out.writeframes(inp.readframes(inp.getnframes()))
