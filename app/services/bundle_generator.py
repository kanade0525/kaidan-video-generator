"""Bundle (詰め合わせ動画) generator.

Concatenates multiple completed Long stories into a single 1-2 hour video
with a single OP at the start, jingles between stories, and a single ED at
the end. The existing per-story final mp4 cannot be used directly because
each one has OP/ED packaged in — instead this module rebuilds each segment
from the intermediate artifacts (title card image, title narration, scene
images, narration audio, original chunks for scroll subtitles).

```
[OP] → [seg1: title_card + title_narration + slideshow + scroll_sub]
     → [jingle]
     → [seg2: ...]
     → [jingle]
     → ... → [segN] → [ED]
```
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path

from app.models import Story
from app.pipeline.stages import (
    _burn_long_scroll_subtitles,
    load_scene_images,
    title_card_filename,
)
from app.services.video_generator import create_video
from app.services import voice_generator
from app.utils.ffmpeg import (
    concat_videos,
    generate_black_clip,
    get_audio_duration,
)
from app.utils.log import get_logger
from app.utils.paths import (
    bundle_dir,
    bundle_manifest_path,
    bundle_segments_dir,
    bundle_video_path,
    images_dir,
    narration_path,
    story_dir,
)

log = get_logger("kaidan.bundle")

ProgressCallback = Callable[[int, int], None] | None

# Bundle target resolution (long-form横動画)
BUNDLE_WIDTH = 1920
BUNDLE_HEIGHT = 1080
SILENT_JINGLE_DURATION = 0.5


def build_bundle(
    stories: list[Story],
    bundle_name: str,
    op_path: Path | None = None,
    ed_path: Path | None = None,
    jingle_path: Path | None = None,
    progress_callback: ProgressCallback = None,
    keep_segments: bool = False,
) -> Path:
    """Build a bundled long-form compilation video.

    Each story produces an OP/ED-less segment from its intermediate artifacts;
    segments are concatenated with `jingle_path` between them, OP prepended,
    ED appended.

    `keep_segments=False` (default) deletes the `segments/` directory on
    success — segments can be 1GB+ each and pile up. Set True if you want
    to inspect or re-bundle quickly.

    Returns the path to the produced bundle mp4.
    """
    if not stories:
        raise ValueError("少なくとも1つのストーリーが必要です")

    bdir = bundle_dir(bundle_name)
    seg_dir = bundle_segments_dir(bundle_name)
    log.info(
        "[bundle] 開始: %s (%d ストーリー、jingle=%s)",
        bundle_name, len(stories),
        jingle_path.name if jingle_path else "(silent fallback)",
    )

    total_steps = len(stories) + 1  # build segments + final concat
    if progress_callback:
        progress_callback(0, total_steps)

    # 1. Build per-story segments
    segment_paths: list[Path] = []
    for idx, story in enumerate(stories):
        log.info("[bundle] セグメント %d/%d: %s", idx + 1, len(stories), story.title)
        seg_path = _build_story_segment(story, seg_dir, idx)
        segment_paths.append(seg_path)
        if progress_callback:
            progress_callback(idx + 1, total_steps)

    # 2. Resolve / fabricate jingle clip
    jingle_clip = _resolve_jingle(jingle_path, seg_dir)

    # 3. Build concat list: [OP, seg1, jingle, seg2, jingle, ..., segN, ED]
    parts: list[Path] = []
    if op_path is not None and op_path.exists():
        parts.append(op_path)
    for i, seg in enumerate(segment_paths):
        parts.append(seg)
        # Jingle between stories only (not after the last segment)
        if jingle_clip is not None and i < len(segment_paths) - 1:
            parts.append(jingle_clip)
    if ed_path is not None and ed_path.exists():
        parts.append(ed_path)

    # 4. Compute YouTube chapter offsets (cumulative duration from start)
    chapters = _compute_chapters(
        stories, segment_paths,
        op_path if op_path is not None and op_path.exists() else None,
        jingle_clip if len(segment_paths) > 1 else None,
        ed_path if ed_path is not None and ed_path.exists() else None,
    )

    # 5. Concatenate everything
    output = bundle_video_path(bundle_name)
    log.info("[bundle] 連結: %d パーツ → %s", len(parts), output.name)
    concat_videos(parts, output, width=BUNDLE_WIDTH, height=BUNDLE_HEIGHT)

    # 6. Write manifest (with chapters for YouTube auto-detection)
    duration = 0.0
    try:
        duration = get_audio_duration(output)
    except Exception:
        pass
    _write_manifest(bdir, bundle_name, stories, duration, jingle_path, chapters)

    # Clean up intermediate segments by default (they can be 1GB+ each)
    if not keep_segments:
        import shutil
        try:
            shutil.rmtree(seg_dir)
            log.info("[bundle] 中間セグメントを削除: %s", seg_dir.name)
        except Exception as e:
            log.warning("[bundle] segments/ 削除失敗 (続行): %s", e)

    if progress_callback:
        progress_callback(total_steps, total_steps)
    log.info("[bundle] 完了: %s (%.1f分)", output.name, duration / 60.0)
    return output


def _build_story_segment(story: Story, seg_dir: Path, idx: int) -> Path:
    """Build a per-story segment: title card + title narration + slideshow + scroll sub.

    Returns the path to the subtitled segment.
    """
    ct = story.content_type
    sdir = story_dir(story.title, ct)
    img_dir = images_dir(story.title, ct)

    # Locate inputs
    title_card = img_dir / title_card_filename(ct)
    narration = narration_path(story.title, ct)
    if not narration.exists():
        raise FileNotFoundError(
            f"narration_complete.wav が見つかりません: {story.title}",
        )

    images, durations = load_scene_images(img_dir, sdir / "slideshow.json", ct)
    if not images:
        raise FileNotFoundError(f"scene 画像が見つかりません: {story.title}")

    # Generate title narration if missing
    title_audio = sdir / "title_narration.wav"
    if title_card.exists() and not title_audio.exists():
        log.info("[bundle] title_narration 生成: %s", story.title)
        voice_generator.generate_title_audio(
            story.title, title_audio, story.title_furigana,
        )

    # Build raw segment without OP/ED
    raw_seg = seg_dir / f"{idx:03d}_raw.mp4"
    create_video(
        images, narration, raw_seg,
        durations=durations,
        title_card=title_card if title_card.exists() else None,
        title_audio=title_audio if title_audio.exists() else None,
        progress_callback=None,
        include_op=False,
        include_ed=False,
        include_title_card=title_card.exists(),
        target_width=BUNDLE_WIDTH,
        target_height=BUNDLE_HEIGHT,
    )

    # Burn scroll subtitles. Bundle segments don't have OP, so suppress
    # the OP-duration offset that the helper would otherwise add.
    final_seg = seg_dir / f"{idx:03d}.mp4"
    _burn_long_scroll_subtitles(
        story, raw_seg, final_seg,
        title_card if title_card.exists() else None,
        title_audio if title_audio.exists() else None,
        include_op_offset=False,
    )
    raw_seg.unlink(missing_ok=True)
    return final_seg


def _resolve_jingle(jingle_path: Path | None, seg_dir: Path) -> Path | None:
    """Return a usable jingle clip path, or None if no jingle desired.

    If `jingle_path` is provided and exists, return it. Otherwise generate a
    short silent black clip as a soft separator.
    """
    if jingle_path is not None and jingle_path.exists():
        return jingle_path
    if jingle_path is not None:
        log.warning("[bundle] 指定されたジングルが見つかりません: %s", jingle_path)

    silent = seg_dir / "_silent_jingle.mp4"
    if not silent.exists():
        _make_silent_jingle(silent, target_width=BUNDLE_WIDTH, target_height=BUNDLE_HEIGHT)
    return silent


def _make_silent_jingle(path: Path, *, target_width: int, target_height: int) -> Path:
    """Create a 0.5s silent black clip as a default story separator."""
    log.info("[bundle] 無音ジングル生成 (%.1fs): %s", SILENT_JINGLE_DURATION, path.name)
    generate_black_clip(
        path, SILENT_JINGLE_DURATION,
        width=target_width, height=target_height,
    )
    return path


def _compute_chapters(
    stories: list[Story],
    segment_paths: list[Path],
    op_path: Path | None,
    jingle_clip: Path | None,
    ed_path: Path | None,
) -> list[dict]:
    """Compute YouTube chapter markers (cumulative offsets from bundle start).

    YouTube auto-detects chapters when the description contains lines like
    ``MM:SS Title`` (or ``H:MM:SS Title``) starting at ``00:00`` and at least
    3 entries with monotonically increasing timestamps.

    Returns: list of {title, start_seconds} dicts including OP/ED if present.
    """
    chapters: list[dict] = []
    offset = 0.0

    if op_path is not None:
        chapters.append({"title": "オープニング", "start_seconds": 0.0})
        try:
            offset += get_audio_duration(op_path)
        except Exception:
            pass

    jingle_dur = 0.0
    if jingle_clip is not None:
        try:
            jingle_dur = get_audio_duration(jingle_clip)
        except Exception:
            pass

    for idx, story in enumerate(stories):
        chapters.append({
            "title": story.title,
            "start_seconds": offset,
            "story_id": story.id,
        })
        try:
            offset += get_audio_duration(segment_paths[idx])
        except Exception:
            pass
        # Jingle between stories
        if idx < len(stories) - 1:
            offset += jingle_dur

    if ed_path is not None:
        chapters.append({"title": "エンディング", "start_seconds": offset})

    return chapters


def _write_manifest(
    bdir: Path, bundle_name: str, stories: list[Story],
    duration: float, jingle_path: Path | None,
    chapters: list[dict] | None = None,
) -> None:
    manifest = {
        "name": bundle_name,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "stories": [{"id": s.id, "title": s.title} for s in stories],
        "duration_seconds": duration,
        "jingle_path": str(jingle_path) if jingle_path else "",
        "chapters": chapters or [],
    }
    bundle_manifest_path(bundle_name).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
    )


def format_chapter_timestamp(seconds: float) -> str:
    """Format seconds for a YouTube chapter line.

    Always uses H:MM:SS for compositions over an hour, MM:SS otherwise.
    YouTube requires the first chapter to be exactly 0:00 or 00:00.
    """
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def render_chapters_block(chapters: list[dict]) -> str:
    """Render a chapter block usable in a YouTube video description."""
    if not chapters:
        return ""
    return "\n".join(
        f"{format_chapter_timestamp(c['start_seconds'])} {c['title']}"
        for c in chapters
    )
