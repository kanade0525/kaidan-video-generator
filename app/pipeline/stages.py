from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

from app import database as db
from app.models import Story
from app.services import image_generator, scraper, text_processor, video_generator, voice_generator
from app.utils.log import get_logger
from app.utils.paths import (
    audio_dir,
    chunks_path,
    images_dir,
    meta_path,
    narration_path,
    original_chunks_path,
    processed_text_path,
    raw_content_path,
    story_dir,
    timestamps_path,
    video_path,
)

log = get_logger("kaidan.stages")

ProgressCallback = Callable[[int, int], None] | None


def do_scrape(story: Story, progress_callback: ProgressCallback = None) -> None:
    """Stage: Fetch story content from URL."""
    log.info("[scrape] %s", story.title)
    if progress_callback:
        progress_callback(0, 1)
    content = scraper.fetch_story_content(story.url)

    ct = story.content_type
    out = raw_content_path(story.title, ct)
    out.write_text(content, encoding="utf-8")
    if progress_callback:
        progress_callback(1, 1)
    log.info("[scrape] 保存: %s (%d chars)", out.name, len(content))


def do_text(story: Story, progress_callback: ProgressCallback = None) -> None:
    """Stage: Process text with LLM API."""
    log.info("[text] %s", story.title)
    ct = story.content_type
    if progress_callback:
        progress_callback(0, 2)
    raw = raw_content_path(story.title, ct).read_text(encoding="utf-8")

    processed = text_processor.process_text(raw)
    processed_text_path(story.title, ct).write_text(processed, encoding="utf-8")
    if progress_callback:
        progress_callback(1, 2)

    chunks = text_processor.split_into_chunks(processed)
    chunks_path(story.title, ct).write_text(
        json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Save original text chunks (kanji) mapped 1:1 to hiragana chunks for subtitles
    orig_chunks = text_processor.split_into_n_chunks(raw, len(chunks))
    original_chunks_path(story.title, ct).write_text(
        json.dumps(orig_chunks, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if progress_callback:
        progress_callback(2, 2)
    log.info("[text] %d チャンク生成 (原文チャンクも保存)", len(chunks))


def do_voice(story: Story, progress_callback: ProgressCallback = None) -> None:
    """Stage: Generate voice narration via VOICEVOX.

    Also generates opening hook audio (if enabled) so do_video can prepend
    a dramatic intro clip.
    """
    from app.config import get as cfg_get

    log.info("[voice] %s", story.title)
    ct = story.content_type
    chunks = json.loads(chunks_path(story.title, ct).read_text(encoding="utf-8"))
    voice_generator.generate_narration(
        chunks, audio_dir(story.title, ct),
        progress_callback=progress_callback,
    )

    # Opening hook audio (optional, uses the FULL raw text, not chunks)
    if cfg_get("hook_auto_enabled"):
        from app.services import hook_generator
        raw_text = raw_content_path(story.title, ct).read_text(encoding="utf-8")
        hook_wav = story_dir(story.title, ct) / "hook.wav"
        result = hook_generator.generate_hook_audio(story.title, raw_text, hook_wav)
        if result:
            log.info("[voice] フック音声生成完了: %s", hook_wav.name)
        else:
            log.warning("[voice] フック音声生成失敗、動画は通常のOP/Titleから開始")


def do_images(story: Story, progress_callback: ProgressCallback = None) -> None:
    """Stage: Generate images for the story."""
    log.info("[images] %s", story.title)
    ct = story.content_type
    raw = raw_content_path(story.title, ct).read_text(encoding="utf-8")
    category = story.categories[0] if story.categories else "怪談"
    paths = image_generator.generate_images_for_story(
        raw, story.title, images_dir(story.title, ct),
        category=category, progress_callback=progress_callback,
    )
    log.info("[images] %d 画像生成", len(paths))


TITLE_CARD_FILENAME = "000_title_card.png"


def load_scene_images(
    img_dir: Path, slideshow_config_path: Path
) -> tuple[list[Path], list[float] | None]:
    """Load scene images excluding title card. Returns (images, durations)."""
    images: list[Path] = []
    durations: list[float] | None = None

    if slideshow_config_path.exists():
        slide_config = json.loads(slideshow_config_path.read_text())
        if slide_config:
            durations = []
            for slide in slide_config:
                if slide["file"] == TITLE_CARD_FILENAME:
                    continue
                img_path = img_dir / slide["file"]
                if img_path.exists():
                    images.append(img_path)
                    durations.append(slide.get("duration", 0))

    if not images:
        images = sorted(
            p for p in img_dir.glob("*.png") if p.name != TITLE_CARD_FILENAME
        )
        durations = None

    return images, durations


def do_video(story: Story, progress_callback: ProgressCallback = None) -> None:
    """Stage: Create final video."""
    log.info("[video] %s", story.title)
    ct = story.content_type
    if progress_callback:
        progress_callback(0, 3)
    img_dir = images_dir(story.title, ct)
    sdir = story_dir(story.title, ct)

    title_card = img_dir / TITLE_CARD_FILENAME
    images, durations = load_scene_images(img_dir, sdir / "slideshow.json")

    if not images:
        raise RuntimeError("No images found")

    narration = narration_path(story.title, ct)
    if not narration.exists():
        raise RuntimeError("Narration file not found")

    # Generate title narration audio
    title_audio = None
    if title_card.exists():
        title_audio = sdir / "title_narration.wav"
        voice_generator.generate_title_audio(story.title, title_audio, story.title_furigana)

    # Create video WITHOUT ED first; ED is concatenated after subtitles are
    # burned so the scrolling subtitle's read-tail does not overlap the ED.
    raw_output = sdir / "raw_long.mp4"
    video_generator.create_video(
        images, narration, raw_output,
        durations=durations,
        title_card=title_card if title_card.exists() else None,
        title_audio=title_audio,
        progress_callback=progress_callback,
        include_ed=False,
    )

    # Burn scrolling subtitle (original text) onto the ED-less video
    subtitled = sdir / "subtitled_long.mp4"
    _burn_long_scroll_subtitles(story, raw_output, subtitled, title_card, title_audio)
    raw_output.unlink(missing_ok=True)

    # Prepend opening hook clip if hook.wav exists (generated in voice stage)
    hook_wav = sdir / "hook.wav"
    if hook_wav.exists():
        hook_clip = sdir / "hook_clip.mp4"
        _create_hook_clip(hook_wav, images[0], hook_clip)
        from app.utils.ffmpeg import concat_videos
        hook_prefixed = sdir / "hook_prefixed.mp4"
        concat_videos([hook_clip, subtitled], hook_prefixed, width=1920, height=1080)
        subtitled.unlink(missing_ok=True)
        hook_clip.unlink(missing_ok=True)
        subtitled = hook_prefixed

    # Append ED after subtitles are burned
    output = video_path(story.title, ct)
    _append_ed(subtitled, output)

    # Generate timestamps for YouTube description
    _save_timestamps(story, title_card, title_audio)


def _create_hook_clip(
    hook_audio: Path, bg_image: Path, output: Path,
    width: int = 1920, height: int = 1080, fps: int = 30,
) -> Path:
    """Create a video clip for the opening hook: still image + hook audio.

    Used as a retention-friendly prefix before OP/title.
    """
    from app.utils.ffmpeg import get_audio_duration, run_ffmpeg

    duration = get_audio_duration(hook_audio)
    # Add 0.5s buffer for post-hook silence transition into OP
    total_dur = duration + 0.5

    run_ffmpeg([
        "-loop", "1", "-i", str(bg_image),
        "-i", str(hook_audio),
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
               f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
               f"fade=in:st=0:d=0.3,fade=out:st={total_dur - 0.3:.2f}:d=0.3",
        "-af", f"apad=pad_dur=0.5",
        "-r", str(fps),
        "-t", f"{total_dur:.2f}",
        "-shortest",
        str(output),
    ])
    return output


def _append_ed(src: Path, dst: Path) -> None:
    """Append ED video (if configured) to src, writing to dst. No-op if no ED."""
    from pathlib import Path as _P

    from app.config import get as cfg_get
    from app.utils.ffmpeg import concat_videos

    ed_path = cfg_get("ed_path")
    if ed_path and _P(ed_path).exists():
        concat_videos([src, _P(ed_path)], dst, width=1920, height=1080)
        src.unlink(missing_ok=True)
    else:
        src.rename(dst)


def _burn_long_scroll_subtitles(
    story: Story,
    raw_video: Path,
    final_output: Path,
    title_card: Path | None,
    title_audio: Path | None,
) -> None:
    """Burn a full-screen scrolling subtitle (original text) onto the long video.

    Uses the same `generate_scroll_image` + overlay approach as Shorts, but
    with 1920x1080 coordinates. Timing is length-proportional across the whole
    narration, avoiding chunk-to-chunk drift from hiragana/kanji length mismatch.

    Falls back to copying `raw_video` → `final_output` if the required inputs
    (original chunks, narration audio) are missing.
    """
    from app.config import get as cfg_get
    from app.utils.ffmpeg import _split_subtitle_text, burn_all_overlays, get_audio_duration

    ct = story.content_type
    sdir = story_dir(story.title, ct)
    orig_chunks_file = original_chunks_path(story.title, ct)
    narration = narration_path(story.title, ct)

    if not orig_chunks_file.exists() or not narration.exists():
        log.info("[video] 原文チャンクまたはナレーションなし、字幕焼き込みスキップ")
        raw_video.rename(final_output)
        return

    subtitle_chunks = json.loads(orig_chunks_file.read_text(encoding="utf-8"))
    subtitle_text = "".join(subtitle_chunks)
    if not subtitle_text.strip():
        log.info("[video] 字幕テキストが空、焼き込みスキップ")
        raw_video.rename(final_output)
        return

    segments = _split_subtitle_text(subtitle_text, max_chars=40)
    scroll_txt = sdir / "scroll_subtitle_long.txt"
    scroll_txt.write_text("\n".join(segments), encoding="utf-8")

    # Scroll timing spans the entire narration.
    # Offset = OP + title_clip + leading_silence
    offset = 0.0
    op_path = cfg_get("op_path")
    if op_path and Path(op_path).exists():
        offset += get_audio_duration(Path(op_path))
    if title_card and title_card.exists() and title_audio and title_audio.exists():
        offset += 1.0 + get_audio_duration(title_audio) + 1.0
    offset += cfg_get("leading_silence") if cfg_get("leading_silence") is not None else 2.0

    # Extend scroll animation past narration end so the last lines reach
    # the bottom a few seconds after the audio finishes. Scroll speed is
    # effectively unchanged (read_tail is ~1-2% of a typical narration).
    read_tail = 5.0
    scroll_dur = get_audio_duration(narration) + read_tail

    log.info(
        "[video] スクロール字幕焼き込み: %d 行, start=%.2fs, duration=%.1fs",
        len(segments), offset, scroll_dur,
    )
    burn_all_overlays(
        raw_video, final_output,
        scroll_textfile=scroll_txt,
        scroll_start_time=offset,
        scroll_duration=scroll_dur,
        scroll_top=120,
        scroll_bottom=960,
        scroll_font_size=44,
        scroll_line_spacing=32,
        scroll_margin_right=120,
        scroll_video_width=1920,
        scroll_overlay_x=60,
        mask_zones=False,
    )


def _format_timestamp(seconds: float) -> str:
    """Format seconds as M:SS or H:MM:SS."""
    total = int(seconds)
    h, remainder = divmod(total, 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _save_timestamps(
    story: Story,
    title_card: Path | None,
    title_audio: Path | None,
) -> None:
    """Calculate and save video part timestamps for YouTube description.

    If the chapter_auto_enabled config flag is on and per-chunk audio/text is
    available, uses LLM to subdivide 本編 into multiple chapters with
    section-specific labels.
    """
    from app.config import get as cfg_get
    from app.utils.ffmpeg import get_audio_duration
    from app.utils.paths import audio_dir, chunks_path

    ct = story.content_type
    cursor = 0.0
    parts = []

    # OP
    op_path = cfg_get("op_path")
    if op_path and Path(op_path).exists():
        parts.append({"label": "オープニング", "start": cursor})
        op_dur = get_audio_duration(Path(op_path))
        cursor += op_dur

    # Title card
    if title_card and title_card.exists() and title_audio and title_audio.exists():
        parts.append({"label": "タイトル", "start": cursor})
        title_dur = 1.0 + get_audio_duration(title_audio) + 1.0
        cursor += title_dur

    # Main content (auto-chapter subdivision if enabled)
    narration = narration_path(story.title, ct)
    lead = cfg_get("leading_silence") if cfg_get("leading_silence") else 2.0
    trail = cfg_get("trailing_silence") if cfg_get("trailing_silence") else 2.0
    main_start = cursor
    narration_offset = cursor + lead  # where chunk 0 begins

    chapters_added = False
    if cfg_get("chapter_auto_enabled"):
        chapters = _try_generate_chapters(story, narration_offset)
        if chapters:
            parts.extend(chapters)
            chapters_added = True
            log.info("[video] LLMチャプター %d 件を timestamps に追加", len(chapters))

    if not chapters_added:
        parts.append({"label": "本編", "start": main_start})

    if narration.exists():
        narr_dur = get_audio_duration(narration)
        cursor += lead + narr_dur + trail
    else:
        cursor = main_start

    # ED
    ed_path = cfg_get("ed_path")
    if ed_path and Path(ed_path).exists():
        parts.append({"label": "エンディング", "start": cursor})

    ts_file = timestamps_path(story.title, ct)
    ts_file.write_text(
        json.dumps(parts, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    log.info("[video] タイムスタンプ保存: %s", ts_file)


def _try_generate_chapters(story: Story, narration_offset: float) -> list[dict]:
    """Try to generate LLM-based chapters for the main narration body.

    Returns [] if chunks/audio unavailable or LLM fails.
    """
    from app.services.chapter_generator import (
        generate_chapter_labels,
        group_labels_to_chapters,
    )
    from app.utils.ffmpeg import get_audio_duration
    from app.utils.paths import audio_dir, chunks_path

    ct = story.content_type
    chunks_file = chunks_path(story.title, ct)
    adir = audio_dir(story.title, ct)
    if not chunks_file.exists() or not adir.exists():
        return []

    try:
        chunks = json.loads(chunks_file.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("chunks.json 読み込み失敗: %s", e)
        return []

    chunk_wavs = sorted(adir.glob("narration_*.wav"))
    if len(chunk_wavs) != len(chunks):
        log.warning(
            "chunk/audio count mismatch: %d vs %d — chapter生成スキップ",
            len(chunk_wavs), len(chunks),
        )
        return []

    from app.services.voice_generator import concatenate_wav_with_gaps  # noqa: F401

    # Compute per-chunk durations (including inter-chunk gaps, to match the
    # actual concatenated narration_complete.wav timeline).
    from app.config import get as cfg_get
    gap_s = cfg_get("inter_chunk_gap_sentence") or 0.6
    gap_d = cfg_get("inter_chunk_gap_default") or 0.25
    sentence_end_re = re.compile(r"[。！？!?]$")

    durations: list[float] = []
    for i, (wav, chunk) in enumerate(zip(chunk_wavs, chunks)):
        d = get_audio_duration(wav)
        # Add gap except after last chunk
        if i < len(chunks) - 1:
            chunk_text = str(chunk).rstrip()
            gap = gap_s if sentence_end_re.search(chunk_text) else gap_d
            d += gap
        durations.append(d)

    labels = generate_chapter_labels(chunks, story.title)
    if not labels:
        return []
    return group_labels_to_chapters(labels, durations, narration_offset)


def do_youtube_upload(story: Story, progress_callback: ProgressCallback = None) -> None:
    """Stage: Upload video to YouTube."""
    from app.config import get as cfg_get
    from app.services import youtube_uploader

    log.info("[youtube] %s", story.title)

    # Skip if already uploaded
    if story.youtube_video_id:
        log.info("既にアップロード済み: %s", story.youtube_video_id)
        return

    ct = story.content_type
    vid = video_path(story.title, ct)
    if not vid.exists():
        raise RuntimeError("動画ファイルが見つかりません")

    if not youtube_uploader.is_authenticated():
        raise RuntimeError("YouTube未認証。設定ページから認証を実行してください。")

    from app.services.voice_generator import get_speaker_name
    title_template = cfg_get("youtube_title_template")
    category = story.categories[0] if story.categories else "怪談"
    yt_title = title_template.format(title=story.title, category=category)
    speaker_name = get_speaker_name()
    playlist_url = cfg_get("youtube_playlist_url") or ""
    # Source-aware description template. kikikaikai-sourced stories migrated
    # to long (Short→Long) must credit 奇々怪々, not HHS図書館.
    if story.source == "kikikaikai":
        description_template = cfg_get("long_kikikaikai_youtube_description_template")
        # Load author from meta.json if available (kikikaikai scraper writes it)
        from app.utils.paths import meta_path
        meta_file = meta_path(story.title, ct)
        author = story.author
        if meta_file.exists():
            try:
                meta_data = json.loads(meta_file.read_text(encoding="utf-8"))
                author = meta_data.get("author", author)
            except Exception:
                pass
        description = description_template.format(
            title=story.title, url=story.url, speaker=speaker_name,
            playlist_url=playlist_url, author=author,
        )
    else:
        description_template = cfg_get("youtube_description_template")
        description = description_template.format(
            title=story.title, url=story.url, speaker=speaker_name,
            playlist_url=playlist_url,
        )

    # Insert timestamps if available
    ts_file = timestamps_path(story.title, ct)
    if ts_file.exists():
        parts = json.loads(ts_file.read_text(encoding="utf-8"))
        ts_lines = [f"{_format_timestamp(p['start'])} {p['label']}" for p in parts]
        timestamp_block = "\n".join(ts_lines)
        description = f"{timestamp_block}\n\n{description}"

    tags = cfg_get("youtube_tags")
    category_id = cfg_get("youtube_category_id")
    privacy = cfg_get("youtube_privacy_status")

    publish_at = None
    if cfg_get("youtube_schedule_enabled"):
        publish_at = youtube_uploader.get_next_publish_time(
            day=cfg_get("youtube_schedule_day") or "saturday",
            hour=cfg_get("youtube_schedule_hour") or 20,
            minute=cfg_get("youtube_schedule_minute") or 0,
        )

    # Generate custom thumbnail if enabled (LLM-generated clickbait phrase
    # overlaid on the most dramatic scene image)
    thumbnail_path = None
    if cfg_get("thumbnail_auto_enabled"):
        from app.services import thumbnail_generator
        thumb_out = story_dir(story.title, ct) / "thumbnail.png"
        raw = raw_content_path(story.title, ct).read_text(encoding="utf-8")
        result_path = thumbnail_generator.generate_story_thumbnail(
            story.title, raw, images_dir(story.title, ct), thumb_out,
        )
        if result_path and result_path.exists():
            thumbnail_path = result_path
            log.info("[youtube] カスタムサムネイル生成: %s", thumb_out.name)

    result = youtube_uploader.upload_video(
        video_path=vid,
        title=yt_title,
        description=description,
        tags=tags if isinstance(tags, list) else [t.strip() for t in tags.split(",")],
        category_id=category_id,
        privacy_status=privacy,
        publish_at=publish_at,
        thumbnail_path=thumbnail_path,
        progress_callback=progress_callback,
    )

    db.set_youtube_video_id(story.id, result["video_id"])


def do_submit_report(story: Story, progress_callback: ProgressCallback = None) -> None:
    """Stage: Submit usage report to HHS Library."""
    from app.config import get as cfg_get
    from app.services import youtube_uploader

    log.info("[report] %s", story.title)
    if progress_callback:
        progress_callback(0, 1)

    if not story.youtube_video_id:
        raise RuntimeError(
            "YouTube動画IDが見つかりません。"
            "先にYouTubeアップロードを実行してください。"
        )

    video_url = f"https://youtube.com/watch?v={story.youtube_video_id}"
    channel_name = cfg_get("youtube_channel_name")
    contact_email = cfg_get("youtube_contact_email")

    if not channel_name or not contact_email:
        raise RuntimeError(
            "使用報告に必要な設定が不足しています。"
            f"チャンネル名: {'設定済' if channel_name else '未設定'}, "
            f"メールアドレス: {'設定済' if contact_email else '未設定'}"
        )

    youtube_uploader.submit_usage_report(
        story_title=story.title,
        video_url=video_url,
        channel_name=channel_name,
        email=contact_email,
    )

    if progress_callback:
        progress_callback(1, 1)
    log.info("[report] 使用報告送信完了: %s", story.title)


# ── Shorts-specific stage functions ────────────────


def do_scrape_short(story: Story, progress_callback: ProgressCallback = None) -> None:
    """Stage: Fetch story content from kikikaikai."""
    from app.services import kikikaikai_scraper

    log.info("[scrape:short] %s", story.title)
    if progress_callback:
        progress_callback(0, 1)

    text, metadata = kikikaikai_scraper.fetch_story_content(story.url)

    ct = story.content_type
    raw_content_path(story.title, ct).write_text(text, encoding="utf-8")

    # Save metadata for credit overlay
    meta = {
        "author": metadata.get("author", story.author),
        "tags": metadata.get("tags", []),
        "char_count": metadata.get("char_count", len(text)),
        "source": "kikikaikai",
        "source_url": story.url,
    }
    meta_path(story.title, ct).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    db.update_char_count(story.id, len(text))

    if progress_callback:
        progress_callback(1, 1)
    log.info("[scrape:short] 保存: %s (%d chars)", story.title, len(text))


def do_voice_short(story: Story, progress_callback: ProgressCallback = None) -> None:
    """Stage: Generate voice + validate duration for shorts."""
    from app.config import get as cfg_get
    from app.utils.ffmpeg import get_audio_duration

    log.info("[voice:short] %s", story.title)
    ct = story.content_type
    chunks = json.loads(chunks_path(story.title, ct).read_text(encoding="utf-8"))
    shorts_speed = cfg_get("shorts_speed")
    voice_generator.generate_narration(
        chunks, audio_dir(story.title, ct),
        progress_callback=progress_callback,
        speed=shorts_speed,
    )

    # Duration notice (non-blocking).
    # YouTube Shorts制限 (180s) は upload stage で弾く。voice 段階では警告のみ。
    narr = narration_path(story.title, ct)
    duration = get_audio_duration(narr)
    lead = cfg_get("shorts_leading_silence")
    trail = cfg_get("shorts_trailing_silence")
    endscreen = cfg_get("shorts_endscreen_duration") or 0.0
    total = duration + lead + trail + endscreen
    shorts_limit = 180.0

    if total > shorts_limit:
        log.warning(
            "[voice:short] YouTube Shorts尺制限超過: %.1fs > %.0fs "
            "(TikTok等の長尺プラットフォーム向けに動画は生成継続。YouTube Shortsアップロードは後段でスキップされます)",
            total, shorts_limit,
        )
    else:
        log.info("[voice:short] 尺OK: %.1fs (制限: %.0fs)", total, shorts_limit)


def do_images_short(story: Story, progress_callback: ProgressCallback = None) -> None:
    """Stage: Generate images for shorts (vertical, fewer scenes)."""
    from app.config import get as cfg_get

    log.info("[images:short] %s", story.title)
    ct = story.content_type
    raw = raw_content_path(story.title, ct).read_text(encoding="utf-8")
    category = story.categories[0] if story.categories else "怪談"

    paths = image_generator.generate_images_for_story(
        raw, story.title, images_dir(story.title, ct),
        category=category,
        progress_callback=progress_callback,
        content_type=ct,
    )
    log.info("[images:short] %d 画像生成", len(paths))


def do_video_short(story: Story, progress_callback: ProgressCallback = None) -> None:
    """Stage: Create final short video (vertical, no OP/ED, with subtitles + credit)."""
    from app.config import get as cfg_get
    from app.utils.ffmpeg import (
        burn_all_overlays,
        _split_subtitle_text,
        concat_videos,
        generate_black_clip,
        get_audio_duration,
    )

    log.info("[video:short] %s", story.title)
    ct = story.content_type
    if progress_callback:
        progress_callback(0, 3)
    img_dir = images_dir(story.title, ct)
    sdir = story_dir(story.title, ct)
    a_dir = audio_dir(story.title, ct)

    title_card = img_dir / TITLE_CARD_FILENAME
    images, durations = load_scene_images(img_dir, sdir / "slideshow.json")
    if not images:
        raise RuntimeError("No images found")

    narration = narration_path(story.title, ct)
    if not narration.exists():
        raise RuntimeError("Narration file not found")

    lead = cfg_get("shorts_leading_silence")
    trail = cfg_get("shorts_trailing_silence")

    # Generate title narration audio for title card
    title_audio = None
    title_clip_duration = 0.0
    shorts_speed = cfg_get("shorts_speed")
    if title_card.exists():
        title_audio = sdir / "title_narration.wav"
        voice_generator.generate_title_audio(story.title, title_audio, story.title_furigana, speed=shorts_speed)
        # Title clip duration = silence_before(1.0) + audio + silence_after(1.0)
        title_clip_duration = 1.0 + get_audio_duration(title_audio) + 1.0

    # Step 1: Create base video (no OP/ED, vertical 1080x1920)
    if progress_callback:
        progress_callback(1, 3)
    raw_output = sdir / "raw_short.mp4"
    video_generator.create_video(
        images, narration, raw_output,
        durations=durations,
        title_card=title_card if title_card.exists() else None,
        title_audio=title_audio,
        progress_callback=None,
        leading_silence=lead,
        trailing_silence=trail,
        include_op=False,
        include_ed=False,
        include_title_card=True,
        target_width=1080,
        target_height=1920,
        fade_in=0,
        title_fade_in=0,
        title_fade_out=0,
    )

    # Step 2: Generate scroll subtitle image and burn all overlays
    if progress_callback:
        progress_callback(2, 3)

    orig_chunks_file = original_chunks_path(story.title, ct)
    hiragana_chunks_file = chunks_path(story.title, ct)

    subtitle_text = ""
    if orig_chunks_file.exists():
        subtitle_chunks = json.loads(orig_chunks_file.read_text(encoding="utf-8"))
        subtitle_text = "".join(subtitle_chunks)
        log.info("[video:short] 字幕: 原文（漢字）使用")
    elif hiragana_chunks_file.exists():
        subtitle_chunks = json.loads(hiragana_chunks_file.read_text(encoding="utf-8"))
        subtitle_text = "".join(subtitle_chunks)
        log.info("[video:short] 字幕: ひらがなテキスト使用（原文チャンクなし）")

    # Generate scroll subtitle text file
    scroll_txt = None
    narration_duration = get_audio_duration(narration)
    scroll_start = title_clip_duration + lead
    scroll_dur = narration_duration

    if subtitle_text:
        # Split into lines and write as plain text for drawtext textfile
        segments = _split_subtitle_text(subtitle_text, max_chars=16)
        scroll_txt = sdir / "scroll_subtitle.txt"
        scroll_txt.write_text("\n".join(segments), encoding="utf-8")
        log.info("[video:short] スクロール字幕テキスト生成: %d行", len(segments))

    # Load metadata for credit overlay
    meta_file = meta_path(story.title, ct)
    author = story.author
    if meta_file.exists():
        meta_data = json.loads(meta_file.read_text(encoding="utf-8"))
        author = meta_data.get("author", author)

    output = video_path(story.title, ct)
    credit_lines = [
        "奇々怪々",
        f"「{story.title}」",
        f"作者: {author}",
    ]

    log.info("[video:short] 字幕・バナー・クレジットを一括焼き込み中...")
    burn_all_overlays(
        raw_output, output,
        scroll_textfile=scroll_txt,
        scroll_start_time=scroll_start,
        scroll_duration=scroll_dur,
        scroll_top=260,
        scroll_bottom=1440,
        scroll_font_size=48,
        scroll_line_spacing=48,
        scroll_margin_right=200,
        banner_text="ショート怪談",
        banner_font_size=64,
        banner_font_color="red",
        banner_margin_top=160,
        banner_start_time=title_clip_duration,
        credit_lines=credit_lines,
        credit_font_size=52,
        credit_margin_bottom=320,
    )
    raw_output.unlink(missing_ok=True)

    # Append black end screen clip for YouTube end screen (min 5s required)
    endscreen_dur = cfg_get("shorts_endscreen_duration")
    if endscreen_dur and endscreen_dur > 0:
        log.info("[video:short] 終了画面用黒画面追加中 (%.1fs)...", endscreen_dur)
        endscreen_clip = sdir / "endscreen_black.mp4"
        generate_black_clip(endscreen_clip, endscreen_dur, width=1080, height=1920)
        final_with_endscreen = sdir / "final_with_endscreen.mp4"
        concat_videos([output, endscreen_clip], final_with_endscreen, width=1080, height=1920)
        output.unlink(missing_ok=True)
        final_with_endscreen.rename(output)
        endscreen_clip.unlink(missing_ok=True)

    if progress_callback:
        progress_callback(3, 3)
    log.info("[video:short] 動画生成完了: %s", output)


def do_youtube_upload_short(story: Story, progress_callback: ProgressCallback = None) -> None:
    """Stage: Upload short video to YouTube with kikikaikai credits."""
    from app.config import get as cfg_get
    from app.services import youtube_uploader

    log.info("[youtube:short] %s", story.title)

    if story.youtube_video_id:
        log.info("既にアップロード済み: %s", story.youtube_video_id)
        return

    ct = story.content_type
    vid = video_path(story.title, ct)
    if not vid.exists():
        raise RuntimeError("動画ファイルが見つかりません")

    if not youtube_uploader.is_authenticated():
        raise RuntimeError("YouTube未認証。設定ページから認証を実行してください。")

    # YouTube Shorts の180秒上限を超える動画はアップロード対象外（TikTok等専用）
    from app.utils.ffmpeg import get_audio_duration
    shorts_limit = 180.0
    try:
        vid_dur = get_audio_duration(vid)
    except Exception:
        vid_dur = 0.0
    if vid_dur > shorts_limit:
        raise RuntimeError(
            f"YouTube Shorts尺制限超過のためアップロードスキップ: "
            f"{vid_dur:.1f}s > {shorts_limit:.0f}s"
        )

    # Load metadata for author
    meta_file = meta_path(story.title, ct)
    author = story.author
    if meta_file.exists():
        meta_data = json.loads(meta_file.read_text(encoding="utf-8"))
        author = meta_data.get("author", author)

    from app.services.voice_generator import get_speaker_name

    speaker_name = get_speaker_name()

    # Try LLM-generated engaging title/description
    raw_text = ""
    raw_file = raw_content_path(story.title, ct)
    if raw_file.exists():
        raw_text = raw_file.read_text(encoding="utf-8")

    yt_title = ""
    description = ""
    if raw_text:
        try:
            meta_result = text_processor.generate_shorts_metadata(
                story.title, raw_text, author,
            )
            yt_title = meta_result["title"]
            description = meta_result["description"]
        except Exception as e:
            log.warning("[youtube:short] LLM metadata生成失敗: %s", e)

    # Fallback to template. HHS-sourced Shorts (migrated from long) use the
    # HHS-specific template so the 引用元 line is correct per ホラホリ規約.
    if not yt_title:
        title_template = cfg_get("shorts_youtube_title_template")
        yt_title = title_template.format(title=story.title)
    is_hhs = (story.source == "hhs")
    if not description:
        if is_hhs:
            description_template = cfg_get("shorts_hhs_youtube_description_template")
            description = description_template.format(
                title=story.title, url=story.url, speaker=speaker_name,
            )
        else:
            description_template = cfg_get("shorts_youtube_description_template")
            description = description_template.format(
                title=story.title, url=story.url, author=author, speaker=speaker_name,
            )

    # Always append source credit to description (source-specific).
    if is_hhs:
        credit = (
            f"\n\n━━━━━━━━━━━━━━━━━━━━\n"
            f"引用元: HHS図書館\n"
            f"「{story.title}」{story.url}\n"
            f"音声: VOICEVOX:{speaker_name}"
        )
    else:
        credit = (
            f"\n\n━━━━━━━━━━━━━━━━━━━━\n"
            f"引用元: 奇々怪々\n"
            f"「{story.title}」{story.url}\n"
            f"作者: {author}\n"
            f"音声: VOICEVOX:{speaker_name}"
        )
    description += credit

    tags = cfg_get("shorts_youtube_tags")
    # HHS由来 Short のみ #ホラホリ タグを付与（規約上の出典タグ慣習）
    if is_hhs:
        tag_list = [t.strip() for t in tags.split(",")] if isinstance(tags, str) else list(tags)
        if "ホラホリ" not in tag_list:
            tag_list.append("ホラホリ")
        tags = tag_list
    category_id = cfg_get("youtube_category_id")
    privacy = cfg_get("youtube_privacy_status")

    publish_at = None
    if cfg_get("youtube_schedule_enabled"):
        publish_at = youtube_uploader.get_next_publish_time(
            day=cfg_get("youtube_schedule_day") or "saturday",
            hour=cfg_get("youtube_schedule_hour") or 20,
            minute=cfg_get("youtube_schedule_minute") or 0,
        )

    # Custom thumbnail: LLM phrase on dramatic scene image (9:16 for Shorts)
    thumbnail: Path | None = None
    if cfg_get("thumbnail_auto_enabled") and raw_text:
        from app.services import thumbnail_generator
        thumb_out = story_dir(story.title, ct) / "thumbnail.png"
        # Shorts uses vertical 720x1280 thumbnail
        candidates = [
            images_dir(story.title, ct) / "scene_001.png",
            images_dir(story.title, ct) / "scene_000.png",
            images_dir(story.title, ct) / TITLE_CARD_FILENAME,
        ]
        bg = next((p for p in candidates if p.exists()), None)
        if bg:
            try:
                phrase = thumbnail_generator.generate_thumbnail_phrase(story.title, raw_text)
                thumbnail_generator.create_thumbnail(
                    story.title, bg, thumb_out, phrase=phrase,
                    width=720, height=1280,
                )
                thumbnail = thumb_out
                log.info("[youtube:short] カスタムサムネイル生成: %s", thumb_out.name)
            except Exception as e:
                log.warning("[youtube:short] サムネイル生成失敗、タイトルカードにfallback: %s", e)
    if thumbnail is None:
        default = images_dir(story.title, ct) / TITLE_CARD_FILENAME
        if default.exists():
            thumbnail = default

    result = youtube_uploader.upload_video(
        video_path=vid,
        title=yt_title,
        description=description,
        tags=tags if isinstance(tags, list) else [t.strip() for t in tags.split(",")],
        category_id=category_id,
        privacy_status=privacy,
        publish_at=publish_at,
        thumbnail_path=thumbnail,
        progress_callback=progress_callback,
    )

    db.set_youtube_video_id(story.id, result["video_id"])


# Stage function registry: maps (content_type, output_stage) -> processing function
# report_submitted is excluded - triggered manually via UI
STAGE_FUNCTIONS: dict[tuple[str, str], Callable] = {
    ("long", "scraped"): do_scrape,
    ("long", "text_processed"): do_text,
    ("long", "voice_generated"): do_voice,
    ("long", "images_generated"): do_images,
    ("long", "video_complete"): do_video,
    ("long", "youtube_uploaded"): do_youtube_upload,
    ("short", "scraped"): do_scrape_short,
    ("short", "text_processed"): do_text,
    ("short", "voice_generated"): do_voice_short,
    ("short", "images_generated"): do_images_short,
    ("short", "video_complete"): do_video_short,
    ("short", "youtube_uploaded"): do_youtube_upload_short,
}
