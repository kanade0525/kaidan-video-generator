from __future__ import annotations

import json
from typing import Callable

from app import database as db
from app.models import Story
from app.services import image_generator, scraper, text_processor, video_generator, voice_generator
from app.utils.log import get_logger
from app.utils.paths import (
    audio_dir,
    chunks_path,
    images_dir,
    narration_path,
    processed_text_path,
    raw_content_path,
    story_dir,
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

    out = raw_content_path(story.title)
    out.write_text(content, encoding="utf-8")
    if progress_callback:
        progress_callback(1, 1)
    log.info("[scrape] 保存: %s (%d chars)", out.name, len(content))


def do_text(story: Story, progress_callback: ProgressCallback = None) -> None:
    """Stage: Process text with LLM API."""
    log.info("[text] %s", story.title)
    if progress_callback:
        progress_callback(0, 2)
    raw = raw_content_path(story.title).read_text(encoding="utf-8")

    processed = text_processor.process_text(raw)
    processed_text_path(story.title).write_text(processed, encoding="utf-8")
    if progress_callback:
        progress_callback(1, 2)

    chunks = text_processor.split_into_chunks(processed)
    chunks_path(story.title).write_text(
        json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if progress_callback:
        progress_callback(2, 2)
    log.info("[text] %d チャンク生成", len(chunks))


def do_voice(story: Story, progress_callback: ProgressCallback = None) -> None:
    """Stage: Generate voice narration via VOICEVOX."""
    log.info("[voice] %s", story.title)
    chunks = json.loads(chunks_path(story.title).read_text(encoding="utf-8"))
    voice_generator.generate_narration(chunks, audio_dir(story.title), progress_callback=progress_callback)


def do_images(story: Story, progress_callback: ProgressCallback = None) -> None:
    """Stage: Generate images for the story."""
    log.info("[images] %s", story.title)
    raw = raw_content_path(story.title).read_text(encoding="utf-8")
    category = story.categories[0] if story.categories else "怪談"
    paths = image_generator.generate_images_for_story(
        raw, story.title, images_dir(story.title), category=category, progress_callback=progress_callback
    )
    log.info("[images] %d 画像生成", len(paths))


def do_video(story: Story, progress_callback: ProgressCallback = None) -> None:
    """Stage: Create final video."""
    log.info("[video] %s", story.title)
    if progress_callback:
        progress_callback(0, 3)
    img_dir = images_dir(story.title)
    sdir = story_dir(story.title)

    # Load slideshow config if exists
    slideshow_config_path = sdir / "slideshow.json"
    if slideshow_config_path.exists():
        import json as _json
        slide_config = _json.loads(slideshow_config_path.read_text())
        images = []
        durations = []
        for slide in slide_config:
            img_path = img_dir / slide["file"]
            if img_path.exists():
                images.append(img_path)
                durations.append(slide.get("duration", 0))
    else:
        images = sorted(img_dir.glob("*.png"))
        durations = None

    if not images:
        raise RuntimeError("No images found")

    narration = narration_path(story.title)
    if not narration.exists():
        raise RuntimeError("Narration file not found")

    output = video_path(story.title)
    video_generator.create_video(images, narration, output, durations=durations, progress_callback=progress_callback)


def do_youtube_upload(story: Story, progress_callback: ProgressCallback = None) -> None:
    """Stage: Upload video to YouTube."""
    from app.config import get as cfg_get
    from app.services import youtube_uploader

    log.info("[youtube] %s", story.title)

    # Skip if already uploaded
    if story.youtube_video_id:
        log.info("既にアップロード済み: %s", story.youtube_video_id)
        return

    vid = video_path(story.title)
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
    description = description_template.format(title=story.title, url=story.url, speaker=speaker_name)
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

    # Submit usage report to HHS Library
    channel_name = cfg_get("youtube_channel_name")
    contact_email = cfg_get("youtube_contact_email")
    if channel_name and contact_email:
        try:
            youtube_uploader.submit_usage_report(
                story_title=story.title,
                video_url=result["url"],
                channel_name=channel_name,
                email=contact_email,
            )
        except Exception as e:
            log.warning("使用報告送信失敗（アップロードは成功）: %s", e)


# Stage function registry: maps output stage -> processing function
# youtube_uploaded is excluded - only triggered manually via UI approval
STAGE_FUNCTIONS = {
    "scraped": do_scrape,
    "text_processed": do_text,
    "voice_generated": do_voice,
    "images_generated": do_images,
    "video_complete": do_video,
}
