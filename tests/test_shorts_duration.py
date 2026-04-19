"""Tests for shorts duration estimation and classification."""

from unittest.mock import patch

import pytest

from app.models import Story
from app.utils.shorts_duration import (
    SHORTS_LIMIT,
    WARNING_MARGIN,
    classify_duration,
    estimate_shorts_total_duration,
)


class TestClassifyDuration:
    def test_none_returns_unknown(self):
        assert classify_duration(None) == "unknown"

    def test_short_is_ok(self):
        assert classify_duration(60.0) == "ok"

    def test_exactly_limit_is_warning(self):
        assert classify_duration(SHORTS_LIMIT) == "warning"

    def test_just_over_limit_is_over(self):
        assert classify_duration(SHORTS_LIMIT + 0.1) == "over"

    def test_within_margin_is_warning(self):
        assert classify_duration(SHORTS_LIMIT - WARNING_MARGIN + 0.1) == "warning"

    def test_just_under_warning_is_ok(self):
        assert classify_duration(SHORTS_LIMIT - WARNING_MARGIN - 1.0) == "ok"


def _make_story(content_type: str = "short") -> Story:
    return Story(
        id=1,
        url="http://example.com",
        title="テストショート",
        title_furigana="",
        pub_date="",
        stage="voice_generated",
        error=None,
        added_at="",
        updated_at="",
        categories=[],
        stages_completed={},
        youtube_video_id=None,
        content_type=content_type,
        author="",
        char_count=1000,
    )


class TestEstimateShortsTotalDuration:
    def test_no_audio_returns_unknown(self, tmp_path, monkeypatch):
        from app.utils import paths
        monkeypatch.setattr(paths, "OUTPUT_BASE", tmp_path)
        est = estimate_shorts_total_duration(_make_story())
        assert est.seconds is None
        assert est.classification == "unknown"
        assert est.actual is False

    def test_uses_video_when_present(self, tmp_path, monkeypatch):
        from app.utils import paths
        monkeypatch.setattr(paths, "OUTPUT_BASE", tmp_path)
        story = _make_story()
        vid = paths.video_path(story.title, story.content_type)
        vid.write_bytes(b"fake")

        with patch("app.utils.shorts_duration.get_audio_duration", return_value=150.0):
            est = estimate_shorts_total_duration(story)

        assert est.seconds == pytest.approx(150.0)
        assert est.actual is True
        assert est.classification == "ok"

    def test_estimates_from_narration_when_no_video(self, tmp_path, monkeypatch):
        from app.utils import paths
        monkeypatch.setattr(paths, "OUTPUT_BASE", tmp_path)
        story = _make_story()
        narr = paths.narration_path(story.title, story.content_type)
        narr.write_bytes(b"fake")

        with patch(
            "app.utils.shorts_duration.get_audio_duration", return_value=170.0
        ), patch(
            "app.config.get",
            side_effect=lambda k: {
                "shorts_leading_silence": 0.0,
                "shorts_trailing_silence": 0.5,
                "shorts_endscreen_duration": 5.0,
            }.get(k),
        ):
            est = estimate_shorts_total_duration(story)

        # 170 + 0 + 0.5 + 5 = 175.5 (within warning margin of 180)
        assert est.seconds == pytest.approx(175.5)
        assert est.actual is False
        assert est.classification == "warning"

    def test_over_limit_from_narration(self, tmp_path, monkeypatch):
        from app.utils import paths
        monkeypatch.setattr(paths, "OUTPUT_BASE", tmp_path)
        story = _make_story()
        narr = paths.narration_path(story.title, story.content_type)
        narr.write_bytes(b"fake")

        with patch(
            "app.utils.shorts_duration.get_audio_duration", return_value=200.0
        ), patch(
            "app.config.get",
            side_effect=lambda k: {
                "shorts_leading_silence": 0.0,
                "shorts_trailing_silence": 0.5,
                "shorts_endscreen_duration": 5.0,
            }.get(k),
        ):
            est = estimate_shorts_total_duration(story)

        assert est.seconds == pytest.approx(205.5)
        assert est.classification == "over"
        assert est.actual is False
