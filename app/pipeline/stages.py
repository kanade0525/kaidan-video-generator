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
    voice_generator.generate_narration(
        chunks, audio_dir(story.title),
        progress_callback=progress_callback,
    )


def do_images(story: Story, progress_callback: ProgressCallback = None) -> None:
    """Stage: Generate images for the story."""
    log.info("[images] %s", story.title)
    raw = raw_content_path(story.title).read_text(encoding="utf-8")
    category = story.categories[0] if story.categories else "怪談"
    paths = image_generator.generate_images_for_story(
        raw, story.title, images_dir(story.title),
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
    if progress_callback:
        progress_callback(0, 3)
    img_dir = images_dir(story.title)
    sdir = story_dir(story.title)

    title_card = img_dir / TITLE_CARD_FILENAME
    images, durations = load_scene_images(img_dir, sdir / "slideshow.json")

    if not images:
        raise RuntimeError("No images found")

    narration = narration_path(story.title)
    if not narration.exists():
        raise RuntimeError("Narration file not found")

    # Generate title narration audio
    title_audio = None
    if title_card.exists():
        title_audio = sdir / "title_narration.wav"
        voice_generator.generate_title_audio(story.title, title_audio)

    output = video_path(story.title)
    video_generator.create_video(
        images, narration, output,
        durations=durations,
        title_card=title_card if title_card.exists() else None,
        title_audio=title_audio,
        progress_callback=progress_callback,
    )


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
    description = description_template.format(
        title=story.title, url=story.url, speaker=speaker_name,
    )
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


# Stage function registry: maps output stage -> processing function
# youtube_uploaded and report_submitted are excluded - triggered manually via UI
STAGE_FUNCTIONS = {
    "scraped": do_scrape,
    "text_processed": do_text,
    "voice_generated": do_voice,
    "images_generated": do_images,
    "video_complete": do_video,
}
