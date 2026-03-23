from __future__ import annotations

import os
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
    host = os.environ.get("VOICEVOX_HOST", VOICEVOX_HOST)
    r = requests.get(f"{host}/speakers", timeout=10)
    r.raise_for_status()
    return r.json()


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
    host = os.environ.get("VOICEVOX_HOST", VOICEVOX_HOST)

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

    # Synthesis
    r = requests.post(
        f"{host}/synthesis",
        params={"speaker": sid},
        json=query,
        timeout=120,
    )
    r.raise_for_status()
    return r.content


def generate_narration(chunks: list[str], output_dir: Path, progress_callback=None) -> Path:
    """Generate narration for all chunks and concatenate."""
    audio_files = []

    for i, chunk in enumerate(chunks):
        log.info("音声生成中 (%d/%d)", i + 1, len(chunks))
        if progress_callback:
            progress_callback(i, len(chunks))
        audio_data = text_to_speech(chunk)
        audio_path = output_dir / f"narration_{i:04d}.wav"
        audio_path.write_bytes(audio_data)
        audio_files.append(audio_path)
        log.info("保存: %s", audio_path.name)

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
