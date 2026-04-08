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

    Each image is rendered as an individual video clip using -loop 1, then all
    clips are concatenated with the audio track. This avoids the known FFmpeg
    concat demuxer issue where static images produce truncated output.

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

    # Calculate per-image durations
    if durations and len(durations) == len(images):
        fixed_total = sum(d for d in durations if d > 0)
        auto_count = sum(1 for d in durations if d <= 0)
        auto_dur = max(0.5, (total_duration - fixed_total) / auto_count) if auto_count > 0 else 0
        final_durations = [d if d > 0 else auto_dur for d in durations]
    else:
        per_image = total_duration / len(images)
        final_durations = [per_image] * len(images)

    log.info("スライドショー: %d枚, 各%.1fs, 合計%.1fs",
             len(images), final_durations[0], sum(final_durations))

    # Single image: simple -loop 1 with audio
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

    # Multiple images: generate each as a silent video clip, then concat + add audio.
    # This avoids the concat demuxer bug with long-duration static images.
    temp_dir = output_path.parent
    clip_paths: list[Path] = []

    for i, (img, dur) in enumerate(zip(images, final_durations, strict=False)):
        clip_path = temp_dir / f"slide_clip_{i:03d}.ts"
        vf_args = ["-vf", vf_scale] if vf_scale else []
        run_ffmpeg([
            "-loop", "1",
            "-i", str(img),
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            *vf_args,
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-r", str(fps),
            "-c:a", "aac",
            "-b:a", "192k",
            "-t", f"{dur:.3f}",
            str(clip_path),
        ])
        clip_paths.append(clip_path)
        log.info("スライドクリップ %d/%d 生成完了 (%.1fs)", i + 1, len(images), dur)

    # Concat all clips
    concat_file = temp_dir / "slide_concat.txt"
    lines = []
    for cp in clip_paths:
        safe_path = str(cp.resolve()).replace("'", "'\\''")
        lines.append(f"file '{safe_path}'")
    concat_file.write_text("\n".join(lines))

    # Concat video clips + replace audio with the real narration
    video_only = temp_dir / "slideshow_video_only.mp4"
    run_ffmpeg([
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_file),
        "-c", "copy",
        str(video_only),
    ])

    # Mux concatenated video with real audio
    run_ffmpeg([
        "-i", str(video_only),
        "-i", str(audio_path),
        "-map", "0:v",
        "-map", "1:a",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-t", f"{total_duration:.3f}",
        "-movflags", "+faststart",
        str(output_path),
    ])

    # Verify output duration
    out_dur = get_audio_duration(output_path)
    if abs(out_dur - total_duration) > 1.0:
        log.warning("⚠ スライドショー尺ズレ: 期待=%.1fs, 実際=%.1fs", total_duration, out_dur)
    else:
        log.info("スライドショー尺OK: %.1fs", out_dur)

    # Cleanup
    concat_file.unlink(missing_ok=True)
    video_only.unlink(missing_ok=True)
    for cp in clip_paths:
        cp.unlink(missing_ok=True)

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

def _find_ffmpeg_font() -> str:
    """Find a CJK font usable by FFmpeg (prefers Zomzi for horror style)."""
    for fp in CJK_FONT_PATHS:
        if Path(fp).exists():
            return fp
    return ""


def _get_font_family(font_path: str) -> str:
    """Extract the font family name from a TTF/TTC file for libass FontName."""
    try:
        from PIL import ImageFont
        font = ImageFont.truetype(font_path, 20)
        return font.getname()[0]
    except Exception:
        return Path(font_path).stem


def add_credit_overlay(
    input_path: Path,
    output_path: Path,
    lines: list[str],
    font_size: int = 52,
) -> Path:
    """Burn credit text at the bottom of a video using drawtext filter."""
    font_path = _find_ffmpeg_font()

    # Build drawtext filter chain for each line (bottom-up positioning)
    filters = []
    for i, line in enumerate(reversed(lines)):
        # Escape special chars for FFmpeg drawtext
        escaped = line.replace("\\", "\\\\").replace("'", "'\\''").replace(":", "\\:")
        y_offset = 60 + i * (font_size + 16)
        font_opt = f":fontfile='{font_path}'" if font_path else ""
        filters.append(
            f"drawtext=text='{escaped}'"
            f":fontsize={font_size}"
            f":fontcolor=white"
            f":borderw=3:bordercolor=black"
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


def _split_subtitle_text(text: str, max_chars: int = 28) -> list[str]:
    """Split long subtitle text into shorter display segments for SRT.

    Strategy:
    1. Split at sentence-ending punctuation (。！？) — punctuation stays
       attached to the preceding segment.
    2. If a segment is still over max_chars, split at commas (、).
    3. Last resort: force-split, searching backwards for a natural break.

    Bracket handling: 「」pairs are kept together when possible.
    Short fragments (≤3 chars like lone 」) are merged into adjacent segments.
    """
    if len(text) <= max_chars:
        return [text]

    # Step 1: split at sentence endings, keeping the delimiter attached.
    # Do NOT split after 」 here — handle brackets separately to avoid orphans.
    parts = re.split(r"(?<=[。！？])", text)
    parts = [p for p in parts if p]

    # Step 2: group parts into segments respecting max_chars
    segments: list[str] = []
    current = ""
    for part in parts:
        if len(current) + len(part) <= max_chars:
            current += part
        else:
            if current:
                segments.append(current)
            current = part

    if current:
        segments.append(current)

    # Step 3: split any oversized segments at commas
    refined: list[str] = []
    for seg in segments:
        if len(seg) <= max_chars:
            refined.append(seg)
            continue
        comma_parts = re.split(r"(?<=[、，])", seg)
        comma_parts = [p for p in comma_parts if p]
        cur = ""
        for cp in comma_parts:
            if len(cur) + len(cp) <= max_chars:
                cur += cp
            else:
                if cur:
                    refined.append(cur)
                cur = cp
        if cur:
            refined.append(cur)

    # Step 4: force-split anything still too long, seeking a natural break
    forced: list[str] = []
    for seg in refined:
        if len(seg) <= max_chars:
            forced.append(seg)
            continue
        pos = 0
        while pos < len(seg):
            if pos + max_chars >= len(seg):
                forced.append(seg[pos:])
                break
            end = pos + max_chars
            break_at = end
            for offset in range(min(10, end - pos)):
                ch = seg[end - 1 - offset]
                if ch in "。、！？）】』\n":
                    break_at = end - offset
                    break
            forced.append(seg[pos:break_at])
            pos = break_at

    # Step 5: merge short fragments (≤5 chars) into neighbors to avoid
    # orphaned entries like lone "」", "た。", "ていった。".
    # Allow up to 6 extra chars over max_chars when merging — a slightly
    # long subtitle is far better than a 2-char flash.
    merge_tolerance = 6
    final: list[str] = []
    for seg in forced:
        seg = seg.strip()
        if not seg:
            continue
        if len(seg) <= 5 and final:
            if len(final[-1]) + len(seg) <= max_chars + merge_tolerance:
                final[-1] += seg
                continue
        final.append(seg)

    # Also merge a short leading fragment forward
    if len(final) > 1 and len(final[0]) <= 5:
        if len(final[0]) + len(final[1]) <= max_chars + merge_tolerance:
            final[1] = final[0] + final[1]
            final.pop(0)

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
    font_size: int = 46,
    margin_v: int = 200,
    video_width: int = 1080,
    video_height: int = 1920,
) -> Path:
    """Burn SRT subtitles into video with dramatic styling.

    Default font_size=46 and margin_v=200 are tuned for vertical 1080x1920 shorts.
    Uses Zomzi font via fontsdir for proper libass font resolution.

    IMPORTANT: PlayResX/PlayResY must match the actual video dimensions so that
    font_size corresponds to real pixel sizes. Without this, libass scales from
    its default 384x288 canvas, making text enormously oversized on HD video.
    """
    font_path = _find_ffmpeg_font()
    # Escape path for FFmpeg filter (colons and backslashes)
    sub_escaped = str(subtitle_path).replace("\\", "/").replace(":", "\\:")

    # Resolve font directory and font name for libass
    fonts_dir = ""
    font_name_str = ""
    if font_path:
        fonts_dir_path = Path(font_path).parent
        fonts_dir = str(fonts_dir_path).replace("\\", "/").replace(":", "\\:")
        # Resolve the actual font family name embedded in the TTF file.
        # libass matches by family name, not filename.
        font_family = _get_font_family(font_path)
        font_name_str = f"FontName={font_family}," if font_family else ""

    style = (
        f"force_style='{font_name_str}"
        f"PlayResX={video_width},"
        f"PlayResY={video_height},"
        f"FontSize={font_size},"
        f"PrimaryColour=&H00FFFFFF,"
        f"OutlineColour=&H00000000,"
        f"BackColour=&H80000000,"
        f"Outline=2,"
        f"Shadow=1,"
        f"MarginV={margin_v},"
        f"Alignment=2'"
    )

    # Build subtitles filter with fontsdir for font discovery
    sub_filter = f"subtitles='{sub_escaped}':{style}"
    if fonts_dir:
        sub_filter = f"subtitles='{sub_escaped}':fontsdir='{fonts_dir}':{style}"

    run_ffmpeg([
        "-i", str(input_path),
        "-vf", sub_filter,
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
