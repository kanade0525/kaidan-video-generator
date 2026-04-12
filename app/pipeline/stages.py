from __future__ import annotations

import json
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
    """Stage: Generate voice narration via VOICEVOX."""
    log.info("[voice] %s", story.title)
    ct = story.content_type
    chunks = json.loads(chunks_path(story.title, ct).read_text(encoding="utf-8"))
    voice_generator.generate_narration(
        chunks, audio_dir(story.title, ct),
        progress_callback=progress_callback,
    )


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
        voice_generator.generate_title_audio(story.title, title_audio)

    output = video_path(story.title, ct)
    video_generator.create_video(
        images, narration, output,
        durations=durations,
        title_card=title_card if title_card.exists() else None,
        title_audio=title_audio,
        progress_callback=progress_callback,
    )

    # Generate timestamps for YouTube description
    _save_timestamps(story, title_card, title_audio)


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
    """Calculate and save video part timestamps for YouTube description."""
    from app.config import get as cfg_get
    from app.utils.ffmpeg import get_audio_duration

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

    # Main content
    parts.append({"label": "本編", "start": cursor})
    narration = narration_path(story.title, ct)
    if narration.exists():
        lead = cfg_get("leading_silence") if cfg_get("leading_silence") else 2.0
        narr_dur = get_audio_duration(narration)
        trail = cfg_get("trailing_silence") if cfg_get("trailing_silence") else 2.0
        cursor += lead + narr_dur + trail

    # ED
    ed_path = cfg_get("ed_path")
    if ed_path and Path(ed_path).exists():
        parts.append({"label": "エンディング", "start": cursor})

    ts_file = timestamps_path(story.title, ct)
    ts_file.write_text(
        json.dumps(parts, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    log.info("[video] タイムスタンプ保存: %s", ts_file)


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
    description_template = cfg_get("youtube_description_template")
    speaker_name = get_speaker_name()
    playlist_url = cfg_get("youtube_playlist_url") or ""
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

    result = youtube_uploader.upload_video(
        video_path=vid,
        title=yt_title,
        description=description,
        tags=tags if isinstance(tags, list) else [t.strip() for t in tags.split(",")],
        category_id=category_id,
        privacy_status=privacy,
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

    # Duration validation
    narr = narration_path(story.title, ct)
    duration = get_audio_duration(narr)
    lead = cfg_get("shorts_leading_silence")
    trail = cfg_get("shorts_trailing_silence")
    total = duration + lead + trail
    max_duration = 180.0

    if total > max_duration:
        raise RuntimeError(
            f"ショート動画の尺制限超過: {total:.1f}s > {max_duration:.0f}s "
            f"(ナレーション: {duration:.1f}s + 無音: {lead + trail:.1f}s)"
        )
    log.info("[voice:short] 尺OK: %.1fs (制限: %.0fs)", total, max_duration)


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
        voice_generator.generate_title_audio(story.title, title_audio, speed=shorts_speed)
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

    # Fallback to template
    if not yt_title:
        title_template = cfg_get("shorts_youtube_title_template")
        yt_title = title_template.format(title=story.title)
    if not description:
        description_template = cfg_get("shorts_youtube_description_template")
        description = description_template.format(
            title=story.title, url=story.url, author=author, speaker=speaker_name,
        )

    # Always append source credit to description
    credit = (
        f"\n\n━━━━━━━━━━━━━━━━━━━━\n"
        f"引用元: 奇々怪々\n"
        f"「{story.title}」{story.url}\n"
        f"作者: {author}\n"
        f"音声: VOICEVOX:{speaker_name}"
    )
    description += credit

    tags = cfg_get("shorts_youtube_tags")
    category_id = cfg_get("youtube_category_id")
    privacy = cfg_get("youtube_privacy_status")

    # Use title card as thumbnail
    thumbnail = images_dir(story.title, ct) / TITLE_CARD_FILENAME

    result = youtube_uploader.upload_video(
        video_path=vid,
        title=yt_title,
        description=description,
        tags=tags if isinstance(tags, list) else [t.strip() for t in tags.split(",")],
        category_id=category_id,
        privacy_status=privacy,
        thumbnail_path=thumbnail if thumbnail.exists() else None,
        progress_callback=progress_callback,
    )

    db.set_youtube_video_id(story.id, result["video_id"])


# Stage function registry: maps (content_type, output_stage) -> processing function
# youtube_uploaded and report_submitted are excluded - triggered manually via UI
STAGE_FUNCTIONS: dict[tuple[str, str], Callable] = {
    ("long", "scraped"): do_scrape,
    ("long", "text_processed"): do_text,
    ("long", "voice_generated"): do_voice,
    ("long", "images_generated"): do_images,
    ("long", "video_complete"): do_video,
    ("short", "scraped"): do_scrape_short,
    ("short", "text_processed"): do_text,
    ("short", "voice_generated"): do_voice_short,
    ("short", "images_generated"): do_images_short,
    ("short", "video_complete"): do_video_short,
}
