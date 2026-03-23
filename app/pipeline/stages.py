from __future__ import annotations

import json

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
    video_path,
)

log = get_logger("kaidan.stages")


def do_scrape(story: Story) -> None:
    """Stage: Fetch story content from URL."""
    log.info("[scrape] %s", story.title)
    content = scraper.fetch_story_content(story.url)

    out = raw_content_path(story.title)
    out.write_text(content, encoding="utf-8")
    log.info("[scrape] 保存: %s (%d chars)", out.name, len(content))


def do_text(story: Story) -> None:
    """Stage: Process text with Gemini API."""
    log.info("[text] %s", story.title)
    raw = raw_content_path(story.title).read_text(encoding="utf-8")

    processed = text_processor.process_text(raw)
    processed_text_path(story.title).write_text(processed, encoding="utf-8")

    chunks = text_processor.split_into_chunks(processed)
    chunks_path(story.title).write_text(
        json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info("[text] %d チャンク生成", len(chunks))


def do_voice(story: Story) -> None:
    """Stage: Generate voice narration via VOICEVOX."""
    log.info("[voice] %s", story.title)
    chunks = json.loads(chunks_path(story.title).read_text(encoding="utf-8"))
    voice_generator.generate_narration(chunks, audio_dir(story.title))


def do_images(story: Story) -> None:
    """Stage: Generate images for the story."""
    log.info("[images] %s", story.title)
    raw = raw_content_path(story.title).read_text(encoding="utf-8")
    paths = image_generator.generate_images_for_story(
        raw, story.title, images_dir(story.title)
    )
    log.info("[images] %d 画像生成", len(paths))


def do_video(story: Story) -> None:
    """Stage: Create final video."""
    log.info("[video] %s", story.title)
    img_dir = images_dir(story.title)
    images = sorted(img_dir.glob("*.png"))

    if not images:
        raise RuntimeError("No images found")

    narration = narration_path(story.title)
    if not narration.exists():
        raise RuntimeError("Narration file not found")

    output = video_path(story.title)
    video_generator.create_video(images, narration, output)


# Stage function registry: maps output stage -> processing function
STAGE_FUNCTIONS = {
    "scraped": do_scrape,
    "text_processed": do_text,
    "voice_generated": do_voice,
    "images_generated": do_images,
    "video_complete": do_video,
}
