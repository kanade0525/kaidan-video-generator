"""Tests for shorts pipeline executor."""

from unittest.mock import patch

from app.models import STAGES, STAGES_SHORT
from app.pipeline.executor import Pipeline, StageExecutor, pipeline, shorts_pipeline


class TestStageExecutorContentType:
    def test_default_is_long(self):
        ex = StageExecutor("scraped")
        assert ex.content_type == "long"

    def test_short_content_type(self):
        ex = StageExecutor("scraped", content_type="short")
        assert ex.content_type == "short"
        assert ex.input_stage == "pending"

    def test_short_voice_input_stage(self):
        ex = StageExecutor("voice_generated", content_type="short")
        assert ex.input_stage == "text_processed"


class TestPipelineContentType:
    def test_long_pipeline_has_all_stages(self):
        p = Pipeline(content_type="long")
        for stage in STAGES[1:]:
            assert stage in p.executors

    def test_short_pipeline_includes_report_submitted(self):
        # HHS-sourced shorts (migrated from long) require usage reporting,
        # so report_submitted is now part of STAGES_SHORT and Pipeline executors.
        p = Pipeline(content_type="short")
        assert "report_submitted" in p.executors

    def test_short_pipeline_has_correct_stages(self):
        p = Pipeline(content_type="short")
        for stage in STAGES_SHORT[1:]:
            assert stage in p.executors

    def test_short_pipeline_content_type(self):
        p = Pipeline(content_type="short")
        assert p.content_type == "short"
        for ex in p.executors.values():
            assert ex.content_type == "short"


class TestSingletons:
    def test_pipeline_is_long(self):
        assert pipeline.content_type == "long"

    def test_shorts_pipeline_is_short(self):
        assert shorts_pipeline.content_type == "short"

    def test_singletons_are_different(self):
        assert pipeline is not shorts_pipeline
