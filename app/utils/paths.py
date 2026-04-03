from __future__ import annotations

import re
from pathlib import Path

OUTPUT_BASE = Path("output")


def safe_dirname(title: str, max_len: int = 50) -> str:
    """Generate filesystem-safe directory name from a story title."""
    safe = re.sub(r"[^\w\s\-]", "", title, flags=re.UNICODE)
    safe = safe.strip().replace(" ", "_")[:max_len]
    return safe or "untitled"


def story_dir(title: str) -> Path:
    """Get (and create) the output directory for a story."""
    d = OUTPUT_BASE / safe_dirname(title)
    d.mkdir(parents=True, exist_ok=True)
    return d


def raw_content_path(title: str) -> Path:
    return story_dir(title) / "raw_content.txt"


def meta_path(title: str) -> Path:
    return story_dir(title) / "meta.json"


def processed_text_path(title: str) -> Path:
    return story_dir(title) / "processed_text.txt"


def chunks_path(title: str) -> Path:
    return story_dir(title) / "chunks.json"


def audio_dir(title: str) -> Path:
    d = story_dir(title) / "audio"
    d.mkdir(parents=True, exist_ok=True)
    return d


def narration_path(title: str) -> Path:
    return story_dir(title) / "narration_complete.wav"


def images_dir(title: str) -> Path:
    d = story_dir(title) / "images"
    d.mkdir(parents=True, exist_ok=True)
    return d


def video_path(title: str) -> Path:
    return story_dir(title) / f"{safe_dirname(title)}.mp4"


def short_video_path(title: str) -> Path:
    return story_dir(title) / "short.mp4"
