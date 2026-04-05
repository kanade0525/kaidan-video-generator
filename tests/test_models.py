"""Tests for models module."""

from app.models import STAGE_LABELS, STAGES, Story, next_stage, prev_stage


class TestStages:
    def test_stages_start_with_pending(self):
        assert STAGES[0] == "pending"

    def test_stages_end_with_report_submitted(self):
        assert STAGES[-1] == "report_submitted"

    def test_all_stages_have_labels(self):
        for stage in STAGES:
            assert stage in STAGE_LABELS, f"Missing label for {stage}"

    def test_no_duplicate_stages(self):
        assert len(STAGES) == len(set(STAGES))


class TestNextStage:
    def test_pending_to_scraped(self):
        assert next_stage("pending") == "scraped"

    def test_last_stage_returns_none(self):
        assert next_stage(STAGES[-1]) is None

    def test_invalid_stage_returns_none(self):
        assert next_stage("nonexistent") is None

    def test_all_stages_chain(self):
        for i, stage in enumerate(STAGES[:-1]):
            assert next_stage(stage) == STAGES[i + 1]


class TestPrevStage:
    def test_pending_returns_none(self):
        assert prev_stage("pending") is None

    def test_scraped_to_pending(self):
        assert prev_stage("scraped") == "pending"

    def test_invalid_stage_returns_none(self):
        assert prev_stage("nonexistent") is None

    def test_all_stages_chain(self):
        for i, stage in enumerate(STAGES[1:], 1):
            assert prev_stage(stage) == STAGES[i - 1]


class TestStory:
    def test_defaults(self):
        s = Story()
        assert s.id == 0
        assert s.stage == "pending"
        assert s.error is None
        assert s.categories == []
        assert s.youtube_video_id is None

    def test_categories_not_shared(self):
        s1 = Story()
        s2 = Story()
        s1.categories.append("test")
        assert s2.categories == []
