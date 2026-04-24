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

    v_parts = []
    if fade_in > 0:
        v_parts.append(f"fade=in:st=0:d={fade_in}")
    if fade_out > 0:
        v_parts.append(f"fade=out:st={fade_out_start:.2f}:d={fade_out}")
    v_filter = ",".join(v_parts) if v_parts else "null"

    run_ffmpeg([
        "-loop", "1",
        "-i", str(image),
        "-i", str(audio),
        "-filter_complex",
        f"[0:v]{v_filter}[v];"
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


def generate_scroll_image(
    text: str,
    output_path: Path,
    max_chars: int = 19,
    font_size: int = 48,
    line_spacing: int = 48,
    margin_x: int = 60,
    image_width: int = 1080,
    leading_pad: int = 0,
    pre_split_segments: list[str] | None = None,
) -> tuple[Path, int]:
    """Render subtitle text as a tall transparent PNG for scroll overlay.

    The image is image_width wide, and as tall as needed to fit all lines.
    Text is white with black outline on transparent background.

    leading_pad: transparent padding at the top of the image (pixels).
    Set to the visible scroll area height so that the first line enters
    from the bottom and scrolls up naturally, rather than appearing all at once.

    Returns (output_path, total_image_height).
    """
    from PIL import Image, ImageDraw, ImageFont

    font_path = _find_ffmpeg_font()
    try:
        font = ImageFont.truetype(font_path, font_size) if font_path else ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()

    # Use pre-split segments if provided, otherwise split text
    segments = pre_split_segments if pre_split_segments else _split_subtitle_text(text, max_chars=max_chars)

    # Measure total height
    line_height = font_size + line_spacing
    text_height = line_height * len(segments) + line_spacing * 2
    total_height = leading_pad + text_height

    # Create transparent image
    img = Image.new("RGBA", (image_width, total_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Draw each line centered with outline, offset by leading_pad
    outline_width = 3
    y = leading_pad + line_spacing
    for seg in segments:
        bbox = draw.textbbox((0, 0), seg, font=font)
        text_w = bbox[2] - bbox[0]
        x = (image_width - text_w) // 2

        # Black outline
        for dx in range(-outline_width, outline_width + 1):
            for dy in range(-outline_width, outline_width + 1):
                if dx == 0 and dy == 0:
                    continue
                draw.text((x + dx, y + dy), seg, font=font, fill=(0, 0, 0, 255))
        # White text
        draw.text((x, y), seg, font=font, fill=(255, 255, 255, 255))
        y += line_height

    img.save(str(output_path), "PNG")
    log.info("スクロール字幕画像生成: %dpx x %dpx (%d行)", image_width, total_height, len(segments))
    return output_path, total_height


def burn_all_overlays(
    input_path: Path,
    output_path: Path,
    subtitle_path: Path | None = None,
    scroll_textfile: Path | None = None,
    scroll_start_time: float = 0.0,
    scroll_duration: float = 0.0,
    scroll_top: int = 260,
    scroll_bottom: int = 1440,
    scroll_font_size: int = 48,
    scroll_line_spacing: int = 48,
    scroll_margin_right: int = 200,
    scroll_video_width: int = 1080,
    scroll_overlay_x: int = 0,
    mask_zones: bool = True,
    banner_text: str | None = None,
    banner_font_size: int = 64,
    banner_font_color: str = "red",
    banner_margin_top: int = 160,
    banner_start_time: float = 0.0,
    credit_lines: list[str] | None = None,
    credit_font_size: int = 52,
    credit_margin_bottom: int = 320,
) -> Path:
    """Burn overlays in a single FFmpeg pass.

    Supports two subtitle modes:
    - subtitle_path: ASS/SRT file burned with ass/subtitles filter
    - scroll_textfile: plain text file scrolled via drawtext y animation

    scroll_textfile takes precedence if both are provided.
    """
    font_path = _find_ffmpeg_font()

    # Track filter components
    filter_parts: list[str] = []  # for filter_complex (ASS/overlay)
    drawtext_filters: list[str] = []  # for -vf (drawtext chain)
    current_stream = "[0:v]"
    extra_inputs: list[str] = []

    # 1. Scrolling subtitle via Pillow image + FFmpeg overlay.
    #    drawtext cannot render newlines correctly (shows · instead),
    #    so we pre-render the entire subtitle as a transparent PNG and
    #    overlay it with animated y position.
    if scroll_textfile and scroll_textfile.exists() and scroll_duration > 0:
        scroll_end = scroll_start_time + scroll_duration
        visible_h = scroll_bottom - scroll_top

        text_content = scroll_textfile.read_text(encoding="utf-8")
        segments = [s for s in text_content.split("\n") if s.strip()]
        line_h = scroll_font_size + scroll_line_spacing

        # Generate scroll image with Pillow (narrower to avoid right-side buttons)
        scroll_img_width = scroll_video_width - scroll_margin_right
        scroll_img_path = scroll_textfile.parent / "scroll_subtitle.png"
        _, img_h = generate_scroll_image(
            text="",
            output_path=scroll_img_path,
            font_size=scroll_font_size,
            line_spacing=scroll_line_spacing,
            image_width=scroll_img_width,
            leading_pad=0,
            pre_split_segments=segments,
        )

        # Add scroll image as extra FFmpeg input
        extra_inputs.extend(["-i", str(scroll_img_path)])

        # Start position: 2 lines already visible at scroll_top
        start_y = scroll_top + visible_h - 2 * line_h
        # Stop when the last line reaches the bottom of the visible area
        end_y = scroll_bottom - img_h

        # Overlay with animated y position (linear from start_y to end_y
        # over scroll_duration seconds).
        filter_parts.append(
            f"{current_stream}[1:v]"
            f"overlay=x={scroll_overlay_x}"
            f":y='{start_y}-({start_y}-({end_y}))*(t-{scroll_start_time:.2f})/{scroll_duration:.2f}'"
            f":enable='between(t,{scroll_start_time:.2f},{scroll_end:.2f})'"
            f"[scrolled]"
        )
        current_stream = "[scrolled]"

    elif subtitle_path and subtitle_path.exists():
        # ASS/SRT subtitle filter (non-scroll fallback)
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

        filter_parts.append(f"{current_stream}{sub_filter}[subbed]")
        current_stream = "[subbed]"

    # 1b. Masking boxes to clip scroll text to safe area.
    # Draw opaque rectangles over banner and credit zones so scroll text
    # that bleeds into those areas is hidden. Banner/credits render on top.
    if mask_zones:
        mask_enable = f":enable='gte(t,{banner_start_time:.2f})'" if banner_start_time > 0 else ""
        # Top mask: y=0 to scroll_top
        drawtext_filters.append(
            f"drawbox=x=0:y=0:w=iw:h={scroll_top}"
            f":color=black@0.85:t=fill"
            f"{mask_enable}"
        )
        # Bottom mask: y=scroll_bottom to screen bottom
        drawtext_filters.append(
            f"drawbox=x=0:y={scroll_bottom}:w=iw:h=ih-{scroll_bottom}"
            f":color=black@0.85:t=fill"
            f"{mask_enable}"
        )

    # 2. Title banner (drawtext at top)
    if banner_text:
        escaped = banner_text.replace("\\", "\\\\").replace("'", "'\\''").replace(":", "\\:")
        font_opt = f":fontfile='{font_path}'" if font_path else ""
        enable = f":enable='gte(t,{banner_start_time:.2f})'" if banner_start_time > 0 else ""
        drawtext_filters.append(
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
    # Background box covers any scroll text that bleeds into credit area.
    if credit_lines:
        credit_enable = f":enable='gte(t,{banner_start_time:.2f})'" if banner_start_time > 0 else ""
        for i, line in enumerate(reversed(credit_lines)):
            escaped = line.replace("\\", "\\\\").replace("'", "'\\''").replace(":", "\\:")
            y_offset = credit_margin_bottom + i * (credit_font_size + 16)
            font_opt = f":fontfile='{font_path}'" if font_path else ""
            drawtext_filters.append(
                f"drawtext=text='{escaped}'"
                f":fontsize={credit_font_size}"
                f":fontcolor=white"
                f":borderw=3:bordercolor=black"
                f":x=(w-text_w)/2"
                f":y=h-{y_offset}"
                f"{font_opt}"
                f"{credit_enable}"
            )

    if not filter_parts and not drawtext_filters:
        import shutil
        shutil.copy2(input_path, output_path)
        return output_path

    # Strategy: if we have both overlay (filter_complex) and drawtext filters,
    # run them in two passes. Mixing drawtext with Japanese text inside
    # filter_complex causes escaping issues with 「」 and other characters.
    # Burn can take longer than default 600s for 5+ minute videos; use fast
    # preset and scale timeout with input length.
    try:
        burn_timeout = max(1800, int(get_audio_duration(input_path) * 6))
    except Exception:
        burn_timeout = 1800

    if filter_parts and drawtext_filters:
        # Pass 1: overlay (filter_complex)
        temp_overlay = output_path.parent / f"_overlay_temp_{output_path.name}"
        last_tag = filter_parts[-1].rsplit("[", 1)[-1].rstrip("]")
        vf = ";".join(filter_parts)
        cmd1 = ["-i", str(input_path)]
        cmd1.extend(extra_inputs)
        cmd1.extend([
            "-filter_complex", vf,
            "-map", f"[{last_tag}]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "veryfast", "-c:a", "copy",
            "-movflags", "+faststart",
            str(temp_overlay),
        ])
        run_ffmpeg(cmd1, timeout=burn_timeout)

        # Pass 2: drawtext (simple -vf, no escaping issues)
        dt_chain = ",".join(drawtext_filters)
        run_ffmpeg([
            "-i", str(temp_overlay),
            "-vf", dt_chain,
            "-c:v", "libx264", "-preset", "veryfast", "-c:a", "copy",
            "-movflags", "+faststart",
            str(output_path),
        ], timeout=burn_timeout)
        temp_overlay.unlink(missing_ok=True)

    elif filter_parts:
        # Only overlay
        last_tag = filter_parts[-1].rsplit("[", 1)[-1].rstrip("]")
        vf = ";".join(filter_parts)
        cmd = ["-i", str(input_path)]
        cmd.extend(extra_inputs)
        cmd.extend([
            "-filter_complex", vf,
            "-map", f"[{last_tag}]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "veryfast", "-c:a", "copy",
            "-movflags", "+faststart",
            str(output_path),
        ])
        run_ffmpeg(cmd, timeout=burn_timeout)

    else:
        # Only drawtext
        vf = ",".join(drawtext_filters)
        run_ffmpeg([
            "-i", str(input_path),
            "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-c:a", "copy",
            "-movflags", "+faststart",
            str(output_path),
        ], timeout=burn_timeout)

    return output_path


def generate_black_clip(
    output: Path,
    duration: float,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
) -> Path:
    """Generate a silent black video clip for end screen."""
    run_ffmpeg([
        "-f", "lavfi", "-i", f"color=black:s={width}x{height}:r={fps}:d={duration:.3f}",
        "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo",
        "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-movflags", "+faststart",
        str(output),
    ])
    return output


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
