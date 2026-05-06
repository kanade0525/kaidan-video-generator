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


def bundle_dir(name: str) -> Path:
    """Get (and create) the output directory for a bundle (詰め合わせ動画).

    Bundles concatenate multiple long stories with OP/jingles/ED into a
    1-2 hour compilation video.
    """
    d = OUTPUT_BASE / "bundles" / safe_dirname(name)
    d.mkdir(parents=True, exist_ok=True)
    return d


def bundle_video_path(name: str) -> Path:
    return bundle_dir(name) / f"{safe_dirname(name)}.mp4"


def bundle_segments_dir(name: str) -> Path:
    """Working directory for per-story intermediate segments."""
    d = bundle_dir(name) / "segments"
    d.mkdir(parents=True, exist_ok=True)
    return d


def bundle_manifest_path(name: str) -> Path:
    return bundle_dir(name) / "manifest.json"
