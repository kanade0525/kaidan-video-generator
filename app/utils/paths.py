from __future__ import annotations

import re
from pathlib import Path

OUTPUT_BASE = Path(__file__).resolve().parent.parent.parent / "output"


def safe_dirname(title: str, max_len: int = 50) -> str:
    """Generate filesystem-safe directory name from a story title."""
    safe = re.sub(r"[^\w\s\-]", "", title, flags=re.UNICODE)
    safe = safe.strip().replace(" ", "_")[:max_len]
    return safe or "untitled"


def story_dir(title: str, content_type: str = "long") -> Path:
    """Get (and create) the output directory for a story."""
    if content_type == "short":
        d = OUTPUT_BASE / "shorts" / safe_dirname(title)
    else:
        d = OUTPUT_BASE / safe_dirname(title)
    d.mkdir(parents=True, exist_ok=True)
    return d


def raw_content_path(title: str, content_type: str = "long") -> Path:
    return story_dir(title, content_type) / "raw_content.txt"


def meta_path(title: str, content_type: str = "long") -> Path:
    return story_dir(title, content_type) / "meta.json"


def processed_text_path(title: str, content_type: str = "long") -> Path:
    return story_dir(title, content_type) / "processed_text.txt"


def chunks_path(title: str, content_type: str = "long") -> Path:
    return story_dir(title, content_type) / "chunks.json"


def original_chunks_path(title: str, content_type: str = "long") -> Path:
    return story_dir(title, content_type) / "original_chunks.json"


def audio_dir(title: str, content_type: str = "long") -> Path:
    d = story_dir(title, content_type) / "audio"
    d.mkdir(parents=True, exist_ok=True)
    return d


def narration_path(title: str, content_type: str = "long") -> Path:
    return story_dir(title, content_type) / "narration_complete.wav"


def images_dir(title: str, content_type: str = "long") -> Path:
    d = story_dir(title, content_type) / "images"
    d.mkdir(parents=True, exist_ok=True)
    return d


def video_path(title: str, content_type: str = "long") -> Path:
    return story_dir(title, content_type) / f"{safe_dirname(title)}.mp4"


def timestamps_path(title: str, content_type: str = "long") -> Path:
    return story_dir(title, content_type) / "timestamps.json"
