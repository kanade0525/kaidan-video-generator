from __future__ import annotations

from dataclasses import dataclass, field

STAGES = [
    "pending",
    "scraped",
    "text_processed",
    "voice_generated",
    "images_generated",
    "video_complete",
    "youtube_uploaded",
    "report_submitted",
]

STAGE_LABELS = {
    "pending": "未処理",
    "scraped": "スクレイピング済",
    "text_processed": "テキスト処理済",
    "voice_generated": "音声生成済",
    "images_generated": "画像生成済",
    "video_complete": "動画生成済",
    "youtube_uploaded": "YouTube投稿済",
    "report_submitted": "使用報告済",
}


def prev_stage(stage: str) -> str | None:
    """Return the previous stage, or None if already at the beginning."""
    try:
        idx = STAGES.index(stage)
        return STAGES[idx - 1] if idx > 0 else None
    except ValueError:
        return None


def next_stage(stage: str) -> str | None:
    """Return the next stage, or None if already at the end."""
    try:
        idx = STAGES.index(stage)
        return STAGES[idx + 1] if idx < len(STAGES) - 1 else None
    except ValueError:
        return None


@dataclass
class Story:
    id: int = 0
    url: str = ""
    title: str = ""
    pub_date: str = ""
    stage: str = "pending"
    error: str | None = None
    added_at: str = ""
    updated_at: str = ""
    categories: list[str] = field(default_factory=list)
    stages_completed: dict[str, str] = field(default_factory=dict)
    youtube_video_id: str | None = None
