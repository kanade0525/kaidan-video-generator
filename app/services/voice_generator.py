from __future__ import annotations

import os
import re
import wave
from pathlib import Path

import requests

from app.config import get as cfg_get
from app.pipeline.retry import with_retry
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


@with_retry(max_attempts=3, base_delay=2.0)
def text_to_speech(
    text: str,
    speaker_id: int | None = None,
    speed: float | None = None,
    pitch: float | None = None,
    intonation: float | None = None,
    volume: float | None = None,
) -> bytes:
    """Convert text to speech via VOICEVOX API."""
    sid = speaker_id if speaker_id is not None else cfg_get("speaker_id")
    host = VOICEVOX_HOST

    # Clean text for VOICEVOX (remove newlines)
    clean_text = text.replace("\n", "。").replace("\r", "").strip()

    # Create audio query
    r = requests.post(
        f"{host}/audio_query",
        params={"text": clean_text, "speaker": sid},
        timeout=30,
    )
    r.raise_for_status()
    query = r.json()

    # Accent correction via pyopenjtalk + marine (Issue #31)
    if cfg_get("accent_correction_enabled"):
        from app.services import accent_estimator
        query = accent_estimator.apply_accent_override(query, clean_text)

    # Apply parameters
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

    # Pause length: VOICEVOX native scaling of all `、` / `。` pauses.
    # Longer pauses make kaidan narration more dramatic.
    pause_scale = cfg_get("pause_length_scale")
    if pause_scale is not None:
        query["pauseLengthScale"] = pause_scale

    # Synthesis
    r = requests.post(
        f"{host}/synthesis",
        params={"speaker": sid},
        json=query,
        timeout=120,
    )
    r.raise_for_status()
    return r.content


def generate_title_audio(title: str, output_path: Path, title_furigana: str | None = None, speed: float | None = None) -> Path:
    """Generate title narration audio via VOICEVOX."""
    text = title_furigana if title_furigana else title
    log.info("タイトル読み上げ音声生成中: %s", text)
    audio_data = text_to_speech(text, speed=speed)
    output_path.write_bytes(audio_data)
    log.info("タイトル音声保存: %s", output_path.name)
    return output_path


def generate_narration(
    chunks: list[str], output_dir: Path, progress_callback=None, speed: float | None = None,
) -> Path:
    """Generate narration for all chunks and concatenate."""
    # Clean up stale narration files from previous runs to prevent data corruption
    existing = sorted(output_dir.glob("narration_*.wav"))
    if existing:
        log.info("既存ナレーションファイル %d 個を削除", len(existing))
        for old_file in existing:
            old_file.unlink(missing_ok=True)

    audio_files = []

    for i, chunk in enumerate(chunks):
        log.info("音声生成中 (%d/%d)", i + 1, len(chunks))
        if progress_callback:
            progress_callback(i, len(chunks))
        audio_data = text_to_speech(chunk, speed=speed)
        audio_path = output_dir / f"narration_{i:04d}.wav"
        audio_path.write_bytes(audio_data)
        audio_files.append(audio_path)
        log.info("保存: %s", audio_path.name)

    # Concatenate with inter-chunk silence based on chunk ending punctuation.
    # Chunks ending with 。/！/？ get a longer dramatic pause; others get a
    # moderate pause. This adds "breathing room" between kaidan segments.
    if progress_callback:
        progress_callback(len(chunks), len(chunks))
    output_path = output_dir.parent / "narration_complete.wav"
    concatenate_wav_with_gaps(audio_files, chunks, output_path)
    log.info("結合完了: %s", output_path)
    return output_path


def concatenate_wav(files: list[Path], output: Path) -> None:
    """Concatenate WAV files into a single file (no inter-chunk silence)."""
    if not files:
        return

    with wave.open(str(files[0]), "rb") as first:
        params = first.getparams()

    with wave.open(str(output), "wb") as out:
        out.setparams(params)
        for f in files:
            with wave.open(str(f), "rb") as inp:
                out.writeframes(inp.readframes(inp.getnframes()))


def concatenate_wav_with_gaps(
    files: list[Path], chunks: list[str], output: Path,
) -> None:
    """Concatenate WAV files with silence between chunks based on the last
    punctuation of the preceding chunk.

    Silence durations (configurable):
      - After 。/！/？ (sentence end): cfg `inter_chunk_gap_sentence` (default 0.6s)
      - After other endings: cfg `inter_chunk_gap_default` (default 0.25s)
      - Final chunk: no trailing silence
    """
    if not files:
        return

    gap_sentence = cfg_get("inter_chunk_gap_sentence")
    gap_default = cfg_get("inter_chunk_gap_default")
    if gap_sentence is None:
        gap_sentence = 0.6
    if gap_default is None:
        gap_default = 0.25

    with wave.open(str(files[0]), "rb") as first:
        params = first.getparams()
    framerate = params.framerate
    sampwidth = params.sampwidth
    nchannels = params.nchannels

    sentence_end_re = re.compile(r"[。！？!?]$")

    with wave.open(str(output), "wb") as out:
        out.setparams(params)
        for i, f in enumerate(files):
            with wave.open(str(f), "rb") as inp:
                out.writeframes(inp.readframes(inp.getnframes()))
            # Inter-chunk silence (not after last chunk)
            if i < len(files) - 1:
                chunk = chunks[i].rstrip() if i < len(chunks) else ""
                gap = gap_sentence if sentence_end_re.search(chunk) else gap_default
                if gap > 0:
                    n_frames = int(framerate * gap)
                    silence = b"\x00" * (n_frames * sampwidth * nchannels)
                    out.writeframes(silence)
