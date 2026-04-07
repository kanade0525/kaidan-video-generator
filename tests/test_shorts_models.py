"""Tests for shorts-related model extensions."""

from app.models import (
    STAGES,
    STAGES_SHORT,
    Story,
    next_stage,
    prev_stage,
    stages_for,
)


class TestStagesShort:
    def test_stages_short_starts_with_pending(self):
        assert STAGES_SHORT[0] == "pending"

    def test_stages_short_ends_with_youtube_uploaded(self):
        assert STAGES_SHORT[-1] == "youtube_uploaded"

    def test_stages_short_excludes_report_submitted(self):
        assert "report_submitted" not in STAGES_SHORT

    def test_stages_short_is_subset_of_stages(self):
        for stage in STAGES_SHORT:
            assert stage in STAGES

    def test_stages_short_has_no_duplicates(self):
        assert len(STAGES_SHORT) == len(set(STAGES_SHORT))

    def test_stages_short_one_less_than_long(self):
        assert len(STAGES_SHORT) == len(STAGES) - 1


class TestStagesFor:
    def test_long_returns_stages(self):
        assert stages_for("long") is STAGES

    def test_short_returns_stages_short(self):
        assert stages_for("short") is STAGES_SHORT

    def test_unknown_defaults_to_long(self):
        assert stages_for("unknown") is STAGES


class TestPrevStageContentType:
    def test_long_scraped_to_pending(self):
        assert prev_stage("scraped", "long") == "pending"

    def test_short_scraped_to_pending(self):
        assert prev_stage("scraped", "short") == "pending"

    def test_short_youtube_uploaded_to_video_complete(self):
        assert prev_stage("youtube_uploaded", "short") == "video_complete"

    def test_short_report_submitted_returns_none(self):
        assert prev_stage("report_submitted", "short") is None

    def test_long_report_submitted_to_youtube_uploaded(self):
        assert prev_stage("report_submitted", "long") == "youtube_uploaded"

    def test_short_chain(self):
        for i, stage in enumerate(STAGES_SHORT[1:], 1):
            assert prev_stage(stage, "short") == STAGES_SHORT[i - 1]


class TestNextStageContentType:
    def test_short_video_complete_to_youtube_uploaded(self):
        assert next_stage("video_complete", "short") == "youtube_uploaded"

    def test_short_youtube_uploaded_is_last(self):
        assert next_stage("youtube_uploaded", "short") is None

    def test_long_youtube_uploaded_to_report_submitted(self):
        assert next_stage("youtube_uploaded", "long") == "report_submitted"

    def test_short_chain(self):
        for i, stage in enumerate(STAGES_SHORT[:-1]):
            assert next_stage(stage, "short") == STAGES_SHORT[i + 1]


class TestStoryNewFields:
    def test_defaults(self):
        s = Story()
        assert s.content_type == "long"
        assert s.author == ""
        assert s.char_count is None

    def test_short_content_type(self):
        s = Story(content_type="short")
        assert s.content_type == "short"

    def test_author_and_char_count(self):
        s = Story(author="テスト作者", char_count=300)
        assert s.author == "テスト作者"
        assert s.char_count == 300

    def test_categories_still_independent(self):
        s1 = Story(content_type="short")
        s2 = Story(content_type="short")
        s1.categories.append("test")
        assert s2.categories == []
