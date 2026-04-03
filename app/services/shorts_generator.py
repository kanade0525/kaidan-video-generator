"""YouTube Shorts auto-generator.

Creates a vertical short video (9:16, 1080x1920) with:
- Ken Burns effect (slow zoom) via ffmpeg zoompan
- Synced subtitles via ASS format
- BGM
- Hook text at the start
- "続きは本編で" end card
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.config import get as cfg_get
from app.utils.ffmpeg import get_audio_duration, run_ffmpeg
from app.utils.log import get_logger
from app.utils.paths import audio_dir, chunks_path, images_dir, short_video_path, story_dir

log = get_logger("kaidan.shorts")

MAX_DURATION = 58
WIDTH = 1080
HEIGHT = 1920


def _get_gemini():
    import os
    from google import genai
    api_key = os.environ.get("GEMINI_API_KEY_TEXT_TO_TEXT") or os.environ.get("GEMINI_API_KEY", "")
    return genai.Client(api_key=api_key)


def select_best_chunks(chunks: list[str], title: str) -> tuple[int, int]:
    """Use Gemini to select the most impactful chunk range for a Short."""
    numbered = "\n".join(f"[{i}] {c[:80]}..." for i, c in enumerate(chunks))
    prompt = (
        f"以下の怪談「{title}」のチャンク一覧から、YouTube Shortsの予告動画として"
        f"最もインパクトのある連続する1〜2チャンクを選んでください。\n\n"
        f"選定基準:\n"
        f"・恐怖のピーク、または最も不気味な部分\n"
        f"・視聴者が「続きが気になる」と思うような展開\n"
        f"・冒頭（チャンク0）は避ける\n"
        f"・合計で30〜50秒程度の朗読時間になるもの\n\n"
        f'JSON形式で回答: {{"start": 開始インデックス, "end": 終了インデックス}}\n'
        f"インデックスのみ、説明不要。\n\n"
        f"チャンク一覧:\n{numbered}"
    )
    try:
        client = _get_gemini()
        model_name = cfg_get("gemini_model")
        response = client.models.generate_content(model=model_name, contents=prompt)
        text = (response.text or "").strip()
        text = re.sub(r"```json\s*", "", text)
        text = re.sub(r"```\s*", "", text)
        data = json.loads(text)
        start = max(0, min(int(data["start"]), len(chunks) - 1))
        end = max(start, min(int(data["end"]), len(chunks) - 1))
        return start, end
    except Exception as e:
        log.warning("Geminiチャンク選択失敗、中盤を使用: %s", e)
        mid = len(chunks) // 2
        return mid, min(mid + 1, len(chunks) - 1)


def _extract_audio(title: str, start_idx: int, end_idx: int, output: Path) -> float:
    """Concatenate narration WAV files for selected chunks and return duration."""
    from app.services.voice_generator import concatenate_wav

    adir = audio_dir(title)
    files = [adir / f"narration_{i:04d}.wav" for i in range(start_idx, end_idx + 1)
             if (adir / f"narration_{i:04d}.wav").exists()]
    if not files:
        raise RuntimeError("対象チャンクの音声ファイルが見つかりません")

    concatenate_wav(files, output)
    duration = get_audio_duration(output)

    if duration > MAX_DURATION:
        truncated = output.with_suffix(".trunc.wav")
        run_ffmpeg(["-i", str(output), "-t", str(MAX_DURATION), str(truncated)])
        truncated.rename(output)
        duration = MAX_DURATION
    return duration


def _pick_background_image(title: str) -> Path:
    """Pick a scene image (not title card) from the story."""
    idir = images_dir(title)
    scenes = sorted(
        [p for p in idir.glob("*.png") if "title" not in p.name],
        key=lambda p: p.name,
    )
    if scenes:
        return scenes[0]
    title_card = idir / "000_title_card.png"
    if title_card.exists():
        return title_card
    raise RuntimeError("背景画像が見つかりません")


def _split_text_to_lines(chunks: list[str]) -> list[str]:
    """Split chunks into display lines for subtitle sync."""
    lines = []
    for chunk in chunks:
        parts = re.split(r"(?<=[。、！？」）])", chunk)
        for part in parts:
            part = part.strip()
            if part:
                lines.append(part)
    return lines


def _fmt_ass_time(seconds: float) -> str:
    """Format seconds to ASS time: H:MM:SS.cc"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int((seconds % 1) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _generate_ass_subtitles(
    text_lines: list[str],
    duration: float,
    output: Path,
    hook_text: str = "▼ この後、衝撃の展開が…",
    cta_text: str = "続きは本編で",
) -> Path:
    """Generate an ASS subtitle file with hook, synced subtitles, and CTA."""

    # Calculate line timings proportional to char count
    total_chars = sum(len(ln) for ln in text_lines) or 1
    timings = []
    t = 0.0
    for ln in text_lines:
        line_dur = max(0.8, (len(ln) / total_chars) * duration)
        timings.append((t, t + line_dur))
        t += line_dur
    # Normalize
    if t > 0:
        scale = duration / t
        timings = [(s * scale, e * scale) for s, e in timings]

    total_duration = duration + 3  # +3 for CTA

    ass_content = f"""[Script Info]
Title: Short Subtitles
ScriptType: v4.00+
PlayResX: {WIDTH}
PlayResY: {HEIGHT}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,Noto Sans CJK JP,46,&H00FFFFFF,&H000000FF,&H00000000,&HB4000000,1,0,0,0,100,100,0,0,3,3,0,2,20,20,300,1
Style: Hook,Noto Sans CJK JP,42,&H0050C8FF,&H000000FF,&H00000000,&HC8000000,1,0,0,0,100,100,0,0,3,3,0,8,20,20,80,1
Style: CTA,Noto Sans CJK JP,72,&H003232FF,&H000000FF,&H00000000,&HDC000000,1,0,0,0,100,100,0,0,3,5,0,5,20,20,20,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    # Hook line (first 3 seconds)
    ass_content += f"Dialogue: 1,{_fmt_ass_time(0)},{_fmt_ass_time(3.0)},Hook,,0,0,0,,{hook_text}\n"

    # Subtitle lines
    for i, (start, end) in enumerate(timings):
        line = text_lines[i].replace("\n", "\\N")
        # Wrap long lines with \N
        if len(line) > 16:
            wrapped = "\\N".join(line[j:j + 16] for j in range(0, len(line), 16))
        else:
            wrapped = line
        ass_content += f"Dialogue: 0,{_fmt_ass_time(start)},{_fmt_ass_time(end)},Sub,,0,0,0,,{wrapped}\n"

    # CTA end card
    ass_content += f"Dialogue: 2,{_fmt_ass_time(duration)},{_fmt_ass_time(total_duration)},CTA,,0,0,0,,{cta_text}\n"

    output.write_text(ass_content, encoding="utf-8")
    return output


def _render_cta_overlay(output: Path) -> Path:
    """Render dark overlay for CTA end card as PNG."""
    from PIL import Image
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 200))
    img.save(str(output), format="PNG")
    return output


def generate_short(title: str, progress_callback=None) -> Path:
    """Generate a YouTube Short for a story."""
    log.info("ショート生成開始: %s", title)

    cpath = chunks_path(title)
    if not cpath.exists():
        raise RuntimeError("チャンクファイルが見つかりません")
    chunks = json.loads(cpath.read_text(encoding="utf-8"))

    if progress_callback:
        progress_callback(1, 6)

    # Select best chunks
    start_idx, end_idx = select_best_chunks(chunks, title)
    selected_chunks = chunks[start_idx:end_idx + 1]
    log.info("選択チャンク: %d-%d", start_idx, end_idx)

    if progress_callback:
        progress_callback(2, 6)

    # Extract audio
    sdir = story_dir(title)
    audio_out = sdir / "short_audio.wav"
    duration = _extract_audio(title, start_idx, end_idx, audio_out)
    log.info("音声抽出完了: %.1f秒", duration)

    if progress_callback:
        progress_callback(3, 6)

    # Generate subtitle lines and ASS file
    text_lines = _split_text_to_lines(selected_chunks)
    if not text_lines:
        text_lines = selected_chunks

    ass_path = sdir / "short_subs.ass"
    _generate_ass_subtitles(text_lines, duration, ass_path)
    log.info("ASS字幕生成完了: %d行", len(text_lines))

    # CTA overlay image
    cta_overlay = sdir / "cta_overlay.png"
    _render_cta_overlay(cta_overlay)

    if progress_callback:
        progress_callback(4, 6)

    # Build ffmpeg command
    bg_path = _pick_background_image(title)
    bgm_path = cfg_get("bgm_path")
    total_duration = duration + 3

    # Escape path for ASS filter (ffmpeg requires : and \ escaping)
    ass_escaped = str(ass_path).replace("\\", "\\\\").replace(":", "\\:")

    filter_complex = (
        # Ken Burns zoom on background
        f"[0:v]scale=1296:2304,fps=30,"
        f"zoompan=z='1+0.0008*in':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d={int(total_duration * 30)}:s={WIDTH}x{HEIGHT}:fps=30[bg];"
        # CTA dark overlay (last 3 seconds)
        f"[bg][3:v]overlay=0:0:enable='gte(t,{duration})'[v1];"
        # Burn in ASS subtitles
        f"[v1]ass='{ass_escaped}'[vout];"
        # Audio: narration + BGM
        f"[1:a]aformat=sample_rates=44100:channel_layouts=stereo[narr];"
        f"[2:a]aloop=loop=-1:size=2e+09,volume=0.08,"
        f"afade=out:st={duration}:d=3[bgm];"
        f"[narr][bgm]amix=inputs=2:duration=first[aout]"
    )

    output = short_video_path(title)

    if progress_callback:
        progress_callback(5, 6)

    log.info("ffmpegでショート動画合成中...")

    run_ffmpeg([
        "-loop", "1", "-i", str(bg_path),           # 0: background
        "-i", str(audio_out),                         # 1: narration
        "-stream_loop", "-1", "-i", str(bgm_path),   # 2: BGM
        "-loop", "1", "-i", str(cta_overlay),         # 3: CTA overlay
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", "[aout]",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-preset", "fast",
        "-c:a", "aac",
        "-b:a", "192k",
        "-t", str(total_duration),
        "-movflags", "+faststart",
        str(output),
    ], timeout=300)

    # Cleanup
    audio_out.unlink(missing_ok=True)
    ass_path.unlink(missing_ok=True)
    cta_overlay.unlink(missing_ok=True)

    log.info("ショート生成完了: %s (%.1f秒)", output, total_duration)

    if progress_callback:
        progress_callback(6, 6)

    return output
