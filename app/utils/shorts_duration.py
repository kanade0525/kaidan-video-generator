from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.models import Story
from app.utils.ffmpeg import get_audio_duration
from app.utils.paths import narration_path, video_path

SHORTS_LIMIT = 180.0
WARNING_MARGIN = 5.0

Classification = Literal["ok", "warning", "over", "unknown"]


@dataclass(frozen=True)
class DurationEstimate:
    seconds: float | None
    actual: bool
    classification: Classification


def classify_duration(
    seconds: float | None,
    limit: float = SHORTS_LIMIT,
    warning_margin: float = WARNING_MARGIN,
) -> Classification:
    """Classify a duration against the YouTube Shorts limit."""
    if seconds is None:
        return "unknown"
    if seconds > limit:
        return "over"
    if seconds >= limit - warning_margin:
        return "warning"
    return "ok"


def estimate_shorts_total_duration(story: Story) -> DurationEstimate:
    """Estimate total shorts video duration in seconds.

    Uses the final video file duration when available (authoritative).
    Otherwise estimates from narration + configured silences + end screen.
    Title narration clip is intentionally excluded from the estimate since
    it is generated at the video stage; the estimate is therefore slightly
    conservative (actual final video is a few seconds longer).
    """
    ct = story.content_type
    vid = video_path(story.title, ct)
    if vid.exists():
        try:
            seconds = get_audio_duration(vid)
        except Exception:
            seconds = None
        return DurationEstimate(
            seconds=seconds,
            actual=seconds is not None,
            classification=classify_duration(seconds),
        )

    narr = narration_path(story.title, ct)
    if not narr.exists():
        return DurationEstimate(seconds=None, actual=False, classification="unknown")

    try:
        narr_dur = get_audio_duration(narr)
    except Exception:
        return DurationEstimate(seconds=None, actual=False, classification="unknown")

    from app.config import get as cfg_get
    lead = cfg_get("shorts_leading_silence") or 0.0
    trail = cfg_get("shorts_trailing_silence") or 0.0
    endscreen = cfg_get("shorts_endscreen_duration") or 0.0
    total = narr_dur + lead + trail + endscreen
    return DurationEstimate(
        seconds=total,
        actual=False,
        classification=classify_duration(total),
    )
