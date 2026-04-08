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
    margin_bottom: int = 320,
) -> Path:
    """Burn credit text at the bottom of a video using drawtext filter.

    margin_bottom: safe area from bottom edge (default 320px = 2/12 of 1920px
    to avoid overlap with YouTube Shorts UI elements).
    """
    font_path = _find_ffmpeg_font()

    # Build drawtext filter chain for each line (bottom-up positioning)
    filters = []
    for i, line in enumerate(reversed(lines)):
        # Escape special chars for FFmpeg drawtext
        escaped = line.replace("\\", "\\\\").replace("'", "'\\''").replace(":", "\\:")
        y_offset = margin_bottom + i * (font_size + 16)
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


def add_title_banner(
    input_path: Path,
    output_path: Path,
    title: str,
    font_size: int = 32,
    font_color: str = "white",
    margin_top: int = 40,
    start_time: float = 0.0,
) -> Path:
    """Overlay a persistent title banner at the top of the video.

    The banner appears from start_time onwards (use to skip title card section).
    """
    font_path = _find_ffmpeg_font()

    escaped = title.replace("\\", "\\\\").replace("'", "'\\''").replace(":", "\\:")
    font_opt = f":fontfile='{font_path}'" if font_path else ""
    enable = f":enable='gte(t,{start_time:.2f})'" if start_time > 0 else ""

    filters = [
        f"drawtext=text='{escaped}'"
        f":fontsize={font_size}"
        f":fontcolor={font_color}"
        f":borderw=2:bordercolor=black"
        f":box=1:boxcolor=black@0.5:boxborderw=12"
        f":x=(w-text_w)/2"
        f":y={margin_top}"
        f"{font_opt}"
        f"{enable}"
    ]

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


def _tokenize_morphemes(text: str) -> list[str]:
    """Split text into morpheme tokens using MeCab.

    Each token is the smallest meaningful unit (word/particle/punctuation).
    Falls back to per-character splitting if MeCab is unavailable.
    """
    try:
        import MeCab
        tagger = MeCab.Tagger("-Owakati")
        # wakati gives space-separated tokens
        result = tagger.parse(text).strip()
        tokens = result.split()
        # Verify tokens reconstruct the original text
        if "".join(tokens) == text:
            return tokens
        # If not exact, fall back to character-level
    except Exception:
        pass
    # Fallback: split at punctuation boundaries
    tokens = []
    current = ""
    for ch in text:
        current += ch
        if ch in "。、！？」）】』\n":
            tokens.append(current)
            current = ""
    if current:
        tokens.append(current)
    return tokens


def _split_subtitle_text(text: str, max_chars: int = 19) -> list[str]:
    """Split text into subtitle segments using morpheme-aware boundaries.

    Uses MeCab to tokenize into meaningful units, then greedily groups
    tokens into lines of max_chars. This ensures splits never break
    mid-word and never start with punctuation like 、 or 」.
    """
    if len(text) <= max_chars:
        return [text]

    tokens = _tokenize_morphemes(text)

    # Pre-process: attach closing brackets/punctuation to previous token
    # so they never become the start of a new segment.
    merged_tokens: list[str] = []
    for token in tokens:
        if token and token[0] in "」）】』。、！？" and merged_tokens:
            merged_tokens[-1] += token
        else:
            merged_tokens.append(token)

    segments: list[str] = []
    current = ""

    for token in merged_tokens:
        if len(current) + len(token) <= max_chars:
            current += token
        else:
            if current:
                segments.append(current)
            # If a single token exceeds max_chars, force-split it
            if len(token) > max_chars:
                while token:
                    segments.append(token[:max_chars])
                    token = token[max_chars:]
            else:
                current = token
                continue
            current = ""

    if current:
        segments.append(current)

    # Remove empty segments
    segments = [s for s in segments if s.strip()]

    # Merge short fragments and segments starting with punctuation
    # into neighbors to avoid orphans and bad starts.
    merge_limit = max_chars + 4
    merged: list[str] = []
    for seg in segments:
        should_merge = False
        if merged:
            # Merge short segments (≤4 chars like "。", "なった。", "しかし、")
            if len(seg) <= 4:
                should_merge = True
            # Merge segments starting with closing brackets/punctuation
            elif seg[0] in "、。！？」）】』":
                should_merge = True

        if should_merge and len(merged[-1]) + len(seg) <= merge_limit:
            merged[-1] += seg
        else:
            merged.append(seg)

    # Merge short leading fragment forward
    if len(merged) > 1 and len(merged[0]) <= 4:
        if len(merged[0]) + len(merged[1]) <= merge_limit:
            merged[1] = merged[0] + merged[1]
            merged.pop(0)

    return merged if merged else [text]


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


def generate_ass(
    chunks: list[str],
    audio_dir: Path,
    output_path: Path,
    leading_silence: float = 0.0,
    max_subtitle_chars: int = 28,
    font_size: int = 42,
    alignment: int = 5,
    margin_v: int = 0,
    margin_lr: int = 40,
    video_width: int = 1080,
    video_height: int = 1920,
    timing_chunks: list[str] | None = None,
) -> Path:
    """Generate an ASS subtitle file from narration chunks.

    ASS format is used instead of SRT because libass ignores force_style
    Alignment for SRT input. ASS gives full control over positioning.

    If timing_chunks is provided, timing is calculated from those (hiragana)
    while display text comes from chunks (original kanji). This avoids
    timing skew when kanji text is much shorter than hiragana.
    """
    font_path = _find_ffmpeg_font()
    font_name = _get_font_family(font_path) if font_path else "sans-serif"

    def fmt(t: float) -> str:
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = int(t % 60)
        cs = int((t % 1) * 100)
        return f"{h:01d}:{m:02d}:{s:02d}.{cs:02d}"

    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {video_width}\n"
        f"PlayResY: {video_height}\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font_name},{font_size},"
        f"&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
        f"0,0,0,0,100,100,0,0,1,2,1,{alignment},{margin_lr},{margin_lr},{margin_v},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    # Compute total narration duration from all audio chunks
    total_duration = 0.0
    for i in range(len(chunks)):
        chunk_audio = audio_dir / f"narration_{i:04d}.wav"
        if chunk_audio.exists():
            total_duration += get_audio_duration(chunk_audio)

    # Concat all display text and split globally for even pacing.
    # Per-chunk splitting causes timing skew when original_chunks and
    # hiragana chunks have different text distributions.
    full_text = "".join(chunks)
    all_segments = _split_subtitle_text(full_text, max_chars=max_subtitle_chars)

    if not all_segments or total_duration <= 0:
        output_path.write_text(header, encoding="utf-8")
        return output_path

    # Group segments into multi-line subtitle entries first,
    # then distribute total duration evenly across groups.
    # This gives each screen (3 lines) equal display time.
    lines_per_entry = 3
    groups: list[str] = []
    for g in range(0, len(all_segments), lines_per_entry):
        group = all_segments[g:g + lines_per_entry]
        groups.append(r"\N".join(group))

    # Each group advances the timeline evenly, but the display duration
    # is extended by 30% so subtitles linger slightly after the narration
    # moves on. This prevents the "too fast" feeling with kanji text
    # (which is read faster visually than it's spoken in hiragana).
    group_interval = total_duration / len(groups) if groups else 0
    display_multiplier = 1.3
    group_display = min(group_interval * display_multiplier, total_duration / max(len(groups) - 1, 1))

    events: list[str] = []
    current_time = leading_silence

    for group_text in groups:
        start = current_time
        end = current_time + group_display
        events.append(
            f"Dialogue: 0,{fmt(start)},{fmt(end)},Default,,0,0,0,,{group_text}"
        )
        current_time += group_interval  # advance by interval, not display end

    content = header + "\n".join(events) + "\n"
    output_path.write_text(content, encoding="utf-8")
    log.info("ASS生成: %d エントリ (from %d chunks)", len(events), len(chunks))
    return output_path


def burn_subtitles(
    input_path: Path,
    subtitle_path: Path,
    output_path: Path,
    font_size: int = 46,
    margin_v: int = 200,
    alignment: int = 2,
    video_width: int = 1080,
    video_height: int = 1920,
) -> Path:
    """Burn subtitles into video.

    Supports both SRT and ASS files. ASS is preferred for alignment control
    since libass ignores force_style Alignment for SRT input.
    """
    font_path = _find_ffmpeg_font()
    sub_escaped = str(subtitle_path).replace("\\", "/").replace(":", "\\:")

    fonts_dir = ""
    if font_path:
        fonts_dir_path = Path(font_path).parent
        fonts_dir = str(fonts_dir_path).replace("\\", "/").replace(":", "\\:")

    is_ass = subtitle_path.suffix.lower() == ".ass"

    if is_ass:
        # ASS already contains all style info — use ass filter directly
        sub_filter = f"ass='{sub_escaped}'"
        if fonts_dir:
            sub_filter = f"ass='{sub_escaped}':fontsdir='{fonts_dir}'"
    else:
        # SRT fallback with force_style
        font_name_str = ""
        if font_path:
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
            f"MarginL=0,MarginR=0,"
            f"MarginV={margin_v},"
            f"Alignment={alignment}'"
        )
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


def burn_all_overlays(
    input_path: Path,
    output_path: Path,
    subtitle_path: Path | None = None,
    banner_text: str | None = None,
    banner_font_size: int = 64,
    banner_font_color: str = "red",
    banner_margin_top: int = 160,
    banner_start_time: float = 0.0,
    credit_lines: list[str] | None = None,
    credit_font_size: int = 52,
    credit_margin_bottom: int = 320,
) -> Path:
    """Burn subtitles, title banner, and credit overlay in a single FFmpeg pass.

    This replaces the previous 3-pass approach (burn_subtitles + add_title_banner
    + add_credit_overlay) with a single re-encode, cutting encoding time by ~2/3.
    """
    font_path = _find_ffmpeg_font()

    filters: list[str] = []

    # 1. Subtitle filter (ASS or SRT)
    if subtitle_path and subtitle_path.exists():
        sub_escaped = str(subtitle_path).replace("\\", "/").replace(":", "\\:")
        fonts_dir = ""
        if font_path:
            fonts_dir_path = Path(font_path).parent
            fonts_dir = str(fonts_dir_path).replace("\\", "/").replace(":", "\\:")

        if subtitle_path.suffix.lower() == ".ass":
            sub_filter = f"ass='{sub_escaped}'"
            if fonts_dir:
                sub_filter = f"ass='{sub_escaped}':fontsdir='{fonts_dir}'"
        else:
            sub_filter = f"subtitles='{sub_escaped}'"
            if fonts_dir:
                sub_filter = f"subtitles='{sub_escaped}':fontsdir='{fonts_dir}'"

        filters.append(sub_filter)

    # 2. Title banner (drawtext at top)
    if banner_text:
        escaped = banner_text.replace("\\", "\\\\").replace("'", "'\\''").replace(":", "\\:")
        font_opt = f":fontfile='{font_path}'" if font_path else ""
        enable = f":enable='gte(t,{banner_start_time:.2f})'" if banner_start_time > 0 else ""
        filters.append(
            f"drawtext=text='{escaped}'"
            f":fontsize={banner_font_size}"
            f":fontcolor={banner_font_color}"
            f":borderw=2:bordercolor=black"
            f":box=1:boxcolor=black@0.5:boxborderw=12"
            f":x=(w-text_w)/2"
            f":y={banner_margin_top}"
            f"{font_opt}"
            f"{enable}"
        )

    # 3. Credit lines (drawtext at bottom, shown after title card)
    if credit_lines:
        credit_enable = f":enable='gte(t,{banner_start_time:.2f})'" if banner_start_time > 0 else ""
        for i, line in enumerate(reversed(credit_lines)):
            escaped = line.replace("\\", "\\\\").replace("'", "'\\''").replace(":", "\\:")
            y_offset = credit_margin_bottom + i * (credit_font_size + 16)
            font_opt = f":fontfile='{font_path}'" if font_path else ""
            filters.append(
                f"drawtext=text='{escaped}'"
                f":fontsize={credit_font_size}"
                f":fontcolor=white"
                f":borderw=3:bordercolor=black"
                f":x=(w-text_w)/2"
                f":y=h-{y_offset}"
                f"{font_opt}"
                f"{credit_enable}"
            )

    if not filters:
        # Nothing to do — just copy
        import shutil
        shutil.copy2(input_path, output_path)
        return output_path

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
