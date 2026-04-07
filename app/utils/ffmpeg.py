from __future__ import annotations

import re
import subprocess
from pathlib import Path

from app.utils.log import get_logger

log = get_logger("kaidan.ffmpeg")


def run_ffmpeg(args: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    """Run ffmpeg with the given arguments. Always uses list form (no shell)."""
    cmd = ["ffmpeg", "-y", *args]
    log.debug("ffmpeg command: %s", " ".join(cmd))
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        log.error("ffmpeg failed: %s", result.stderr[-500:] if result.stderr else "")
        raise RuntimeError(f"ffmpeg failed (exit {result.returncode}): {result.stderr[-200:]}")
    return result


_duration_cache: dict[str, float] = {}


def get_audio_duration(audio_path: Path) -> float:
    """Get duration of an audio/video file in seconds using ffprobe (cached)."""
    key = str(audio_path)
    if key in _duration_cache:
        return _duration_cache[key]
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    duration = float(result.stdout.strip())
    _duration_cache[key] = duration
    return duration


def clear_duration_cache() -> None:
    """Clear cached durations (call after temp files are deleted)."""
    _duration_cache.clear()


def create_slideshow(
    images: list[Path],
    audio_path: Path,
    output_path: Path,
    fps: int = 30,
    durations: list[float] | None = None,
    target_width: int | None = None,
    target_height: int | None = None,
) -> Path:
    """Create a slideshow video from images synced to audio duration.

    Args:
        durations: Per-image durations in seconds. 0 or None = auto (equal split).
        target_width/target_height: If set, scale+crop images to fill this resolution.
    """
    total_duration = get_audio_duration(audio_path)

    # Video filter for scaling/cropping to target resolution (fill, no letterbox)
    vf_scale = ""
    if target_width and target_height:
        vf_scale = (
            f"scale={target_width}:{target_height}"
            f":force_original_aspect_ratio=increase,"
            f"crop={target_width}:{target_height}"
        )

    # Single image: use -loop 1 (concat demuxer produces broken keyframes for
    # a single long-duration image)
    if len(images) == 1:
        vf_args = ["-vf", vf_scale] if vf_scale else []
        run_ffmpeg([
            "-loop", "1",
            "-i", str(images[0]),
            "-i", str(audio_path),
            *vf_args,
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-r", str(fps),
            "-c:a", "aac",
            "-b:a", "192k",
            "-t", f"{total_duration:.3f}",
            "-movflags", "+faststart",
            str(output_path),
        ])
        return output_path

    # Calculate per-image durations
    if durations and len(durations) == len(images):
        # Fill in auto (0) durations
        fixed_total = sum(d for d in durations if d > 0)
        auto_count = sum(1 for d in durations if d <= 0)
        auto_dur = max(0.5, (total_duration - fixed_total) / auto_count) if auto_count > 0 else 0
        final_durations = [d if d > 0 else auto_dur for d in durations]
    else:
        per_image = total_duration / len(images)
        final_durations = [per_image] * len(images)

    # Write concat file
    concat_file = output_path.parent / "concat.txt"
    lines = []
    for img, dur in zip(images, final_durations, strict=False):
        safe_path = str(img.resolve()).replace("'", "'\\''")
        lines.append(f"file '{safe_path}'")
        lines.append(f"duration {dur:.3f}")
    # Last image needs duration too, then repeat for ffmpeg concat demuxer
    safe_last = str(images[-1].resolve()).replace("'", "'\\''")
    lines.append(f"file '{safe_last}'")
    lines.append("duration 0.001")
    concat_file.write_text("\n".join(lines))

    vf_args = ["-vf", vf_scale] if vf_scale else []
    run_ffmpeg([
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_file),
        "-i", str(audio_path),
        *vf_args,
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
        "-c:a", "aac",
        "-b:a", "192k",
        "-t", f"{total_duration:.3f}",
        "-movflags", "+faststart",
        str(output_path),
    ])

    concat_file.unlink(missing_ok=True)
    return output_path


def create_title_clip(
    image: Path,
    audio: Path,
    output_path: Path,
    silence_before: float = 1.0,
    silence_after: float = 1.0,
    fade_in: float = 0.5,
    fade_out: float = 0.5,
    fps: int = 30,
) -> Path:
    """Create a title clip: still image + title narration with silence padding and fades."""
    audio_dur = get_audio_duration(audio)
    total_dur = silence_before + audio_dur + silence_after
    fade_out_start = max(0, total_dur - fade_out)

    run_ffmpeg([
        "-loop", "1",
        "-i", str(image),
        "-i", str(audio),
        "-filter_complex",
        f"[0:v]fade=in:st=0:d={fade_in},fade=out:st={fade_out_start:.2f}:d={fade_out}[v];"
        f"[1:a]adelay={int(silence_before * 1000)}|{int(silence_before * 1000)},"
        f"apad=pad_dur={silence_after}[a]",
        "-map", "[v]",
        "-map", "[a]",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
        "-c:a", "aac",
        "-b:a", "192k",
        "-t", f"{total_dur:.3f}",
        "-movflags", "+faststart",
        str(output_path),
    ])
    return output_path


def add_fade(
    input_path: Path,
    output_path: Path,
    fade_in: float = 1.0,
    fade_out: float = 1.0,
) -> Path:
    """Add fade-in and fade-out effects to a video."""
    duration = get_audio_duration(input_path)
    fade_out_start = max(0, duration - fade_out)

    vfilter = (
        f"fade=in:st=0:d={fade_in},"
        f"fade=out:st={fade_out_start:.2f}:d={fade_out}"
    )
    afilter = (
        f"afade=in:st=0:d={fade_in},"
        f"afade=out:st={fade_out_start:.2f}:d={fade_out}"
    )

    run_ffmpeg([
        "-i", str(input_path),
        "-vf", vfilter,
        "-af", afilter,
        "-c:v", "libx264",
        "-c:a", "aac",
        "-movflags", "+faststart",
        str(output_path),
    ])
    return output_path


def mix_bgm(
    video_path: Path,
    bgm_path: Path,
    output_path: Path,
    bgm_volume: float = 0.1,
) -> Path:
    """Mix background music into a video."""
    duration = get_audio_duration(video_path)
    fade_start = max(0, duration - 2.0)

    run_ffmpeg([
        "-i", str(video_path),
        "-stream_loop", "-1",
        "-i", str(bgm_path),
        "-filter_complex",
        (
            f"[1:a]volume={bgm_volume},"
            f"afade=out:st={fade_start:.2f}:d=2.0[bgm];"
            f"[0:a][bgm]amix=inputs=2:duration=first[out]"
        ),
        "-map", "0:v",
        "-map", "[out]",
        "-c:v", "copy",
        "-c:a", "aac",
        "-shortest",
        str(output_path),
    ])
    return output_path


def add_fade_to_clip(
    input_path: Path,
    output_path: Path,
    fade_out: float = 1.0,
) -> Path:
    """Add fade-out to a video clip (for OP)."""
    duration = get_audio_duration(input_path)
    fade_start = max(0, duration - fade_out)

    run_ffmpeg([
        "-i", str(input_path),
        "-vf", f"fade=out:st={fade_start:.2f}:d={fade_out}",
        "-af", f"afade=out:st={fade_start:.2f}:d={fade_out}",
        "-c:v", "libx264",
        "-c:a", "aac",
        str(output_path),
    ])
    return output_path


def _normalize_video(
    input_path: Path, output_path: Path,
    width: int = 1920, height: int = 1080, fps: int = 30,
) -> Path:
    """Re-encode a video to exactly match target format for safe concat."""
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={fps}"
    )
    run_ffmpeg([
        "-i", str(input_path),
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "fast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-ar", "44100",
        "-ac", "2",
        "-b:a", "192k",
        str(output_path),
    ])
    return output_path


CJK_FONT_PATHS = [
    "/app/fonts/Zomzi.TTF",
    "fonts/Zomzi.TTF",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
]

# Readable fonts for subtitles (Zomzi is horror display font, bad for body text)
SUBTITLE_FONT_PATHS = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
    # Fallback to Zomzi only if nothing else available
    "/app/fonts/Zomzi.TTF",
    "fonts/Zomzi.TTF",
]


def _find_ffmpeg_font() -> str:
    """Find a CJK font usable by FFmpeg drawtext (prefers horror-style for titles/credits)."""
    for fp in CJK_FONT_PATHS:
        if Path(fp).exists():
            return fp
    return ""


def _find_subtitle_font() -> str:
    """Find a readable CJK font for subtitles (prefers clean sans-serif over display fonts)."""
    for fp in SUBTITLE_FONT_PATHS:
        if Path(fp).exists():
            return fp
    return ""


def add_credit_overlay(
    input_path: Path,
    output_path: Path,
    lines: list[str],
    font_size: int = 28,
) -> Path:
    """Burn credit text at the bottom of a video using drawtext filter."""
    font_path = _find_ffmpeg_font()

    # Build drawtext filter chain for each line (bottom-up positioning)
    filters = []
    for i, line in enumerate(reversed(lines)):
        # Escape special chars for FFmpeg drawtext
        escaped = line.replace("\\", "\\\\").replace("'", "'\\''").replace(":", "\\:")
        y_offset = 40 + i * (font_size + 10)
        font_opt = f":fontfile='{font_path}'" if font_path else ""
        filters.append(
            f"drawtext=text='{escaped}'"
            f":fontsize={font_size}"
            f":fontcolor=white"
            f":borderw=2:bordercolor=black"
            f":x=(w-text_w)/2"
            f":y=h-{y_offset}"
            f"{font_opt}"
        )

    vf = ",".join(filters)
    run_ffmpeg([
        "-i", str(input_path),
        "-vf", vf,
        "-c:v", "libx264",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output_path),
    ])
    return output_path


def _split_subtitle_text(text: str, max_chars: int = 40) -> list[str]:
    """Split long subtitle text into shorter display segments.

    Splits at sentence-ending punctuation (。！？」) first, then at commas (、),
    and finally force-splits if still too long. This ensures each subtitle entry
    shows at most 2 lines on a vertical 1080px screen.
    """
    if len(text) <= max_chars:
        return [text]

    segments: list[str] = []

    # First split at sentence boundaries
    parts = re.split(r"(?<=[。！？」])", text)
    parts = [p for p in parts if p]

    current = ""
    for part in parts:
        if len(current) + len(part) <= max_chars:
            current += part
        elif not current:
            # Single part exceeds max_chars, split further at commas
            sub_parts = re.split(r"(?<=[、，])", part)
            for sp in sub_parts:
                if len(current) + len(sp) <= max_chars:
                    current += sp
                elif not current:
                    # Force-split at max_chars
                    for j in range(0, len(sp), max_chars):
                        chunk = sp[j : j + max_chars]
                        if chunk:
                            segments.append(chunk)
                else:
                    segments.append(current)
                    current = sp
        else:
            segments.append(current)
            current = part

    if current:
        segments.append(current)

    # Final pass: force-split any remaining oversized segments
    final: list[str] = []
    for seg in segments:
        if len(seg) <= max_chars:
            final.append(seg)
        else:
            for j in range(0, len(seg), max_chars):
                chunk = seg[j : j + max_chars]
                if chunk:
                    final.append(chunk)

    return final if final else [text]


def generate_srt(
    chunks: list[str],
    audio_dir: Path,
    output_path: Path,
    leading_silence: float = 0.0,
    max_subtitle_chars: int = 40,
) -> Path:
    """Generate an SRT subtitle file from narration chunks.

    Each chunk is split into shorter display segments (max_subtitle_chars) so
    subtitles remain readable on vertical video. Timing is distributed
    proportionally by character count within each chunk.
    """
    srt_lines: list[str] = []
    current_time = leading_silence
    entry_index = 1

    def fmt(t: float) -> str:
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = int(t % 60)
        ms = int((t % 1) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    for i, chunk_text in enumerate(chunks):
        chunk_audio = audio_dir / f"narration_{i:04d}.wav"
        if not chunk_audio.exists():
            continue

        chunk_duration = get_audio_duration(chunk_audio)

        # Split into subtitle-friendly segments
        segments = _split_subtitle_text(chunk_text, max_chars=max_subtitle_chars)
        total_chars = sum(len(seg) for seg in segments)

        for seg in segments:
            # Proportional duration based on character count
            seg_duration = chunk_duration * (len(seg) / total_chars) if total_chars > 0 else chunk_duration
            start = current_time
            end = current_time + seg_duration

            srt_lines.append(str(entry_index))
            srt_lines.append(f"{fmt(start)} --> {fmt(end)}")
            srt_lines.append(seg)
            srt_lines.append("")

            entry_index += 1
            current_time = end

    output_path.write_text("\n".join(srt_lines), encoding="utf-8")
    log.info("SRT生成: %d エントリ (from %d chunks)", entry_index - 1, len(chunks))
    return output_path


def burn_subtitles(
    input_path: Path,
    subtitle_path: Path,
    output_path: Path,
    font_size: int = 52,
    margin_v: int = 220,
) -> Path:
    """Burn SRT subtitles into video with dramatic styling.

    Uses a readable CJK font (not horror display font) for subtitle legibility.
    Default font_size=52 and margin_v=220 are tuned for vertical 1080x1920 shorts.
    """
    font_path = _find_subtitle_font()
    # Escape path for FFmpeg filter (colons and backslashes)
    sub_escaped = str(subtitle_path).replace("\\", "/").replace(":", "\\:")

    # Force style for dramatic vertical video subtitles
    font_name = f"fontfile={font_path}," if font_path else ""
    style = (
        f"force_style='{font_name}"
        f"FontSize={font_size},"
        f"PrimaryColour=&H00FFFFFF,"
        f"OutlineColour=&H00000000,"
        f"BackColour=&H80000000,"
        f"Outline=3,"
        f"Shadow=2,"
        f"MarginV={margin_v},"
        f"Alignment=2'"
    )

    run_ffmpeg([
        "-i", str(input_path),
        "-vf", f"subtitles='{sub_escaped}':{style}",
        "-c:v", "libx264",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output_path),
    ])
    return output_path


def concat_videos(
    parts: list[Path],
    output_path: Path,
    width: int = 1920,
    height: int = 1080,
) -> Path:
    """Concatenate multiple video files, normalizing format first."""
    temp_dir = output_path.parent
    normalized_parts = []

    for i, part in enumerate(parts):
        norm_path = temp_dir / f"norm_{i}.ts"
        # Encode to MPEG-TS for safe concat
        _normalize_video(part, norm_path, width=width, height=height)
        normalized_parts.append(norm_path)

    # Use concat protocol with intermediate TS files
    concat_file = temp_dir / "concat_parts.txt"
    lines = []
    for part in normalized_parts:
        safe_path = str(part.resolve()).replace("'", "'\\''")
        lines.append(f"file '{safe_path}'")
    concat_file.write_text("\n".join(lines))

    run_ffmpeg([
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_file),
        "-c", "copy",
        "-movflags", "+faststart",
        str(output_path),
    ])

    concat_file.unlink(missing_ok=True)
    for p in normalized_parts:
        p.unlink(missing_ok=True)
    return output_path
