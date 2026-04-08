"""Tests for shorts pipeline stage functions and registry."""

from app.pipeline.stages import STAGE_FUNCTIONS


class TestStageRegistry:
    def test_long_stages_registered(self):
        expected = ["scraped", "text_processed", "voice_generated", "images_generated", "video_complete"]
        for stage in expected:
            assert ("long", stage) in STAGE_FUNCTIONS, f"Missing long stage: {stage}"

    def test_short_stages_registered(self):
        expected = ["scraped", "text_processed", "voice_generated", "images_generated", "video_complete"]
        for stage in expected:
            assert ("short", stage) in STAGE_FUNCTIONS, f"Missing short stage: {stage}"

    def test_short_text_reuses_long(self):
        assert STAGE_FUNCTIONS[("short", "text_processed")] is STAGE_FUNCTIONS[("long", "text_processed")]

    def test_short_scrape_is_different_from_long(self):
        assert STAGE_FUNCTIONS[("short", "scraped")] is not STAGE_FUNCTIONS[("long", "scraped")]

    def test_short_voice_is_different_from_long(self):
        assert STAGE_FUNCTIONS[("short", "voice_generated")] is not STAGE_FUNCTIONS[("long", "voice_generated")]

    def test_short_images_is_different_from_long(self):
        assert STAGE_FUNCTIONS[("short", "images_generated")] is not STAGE_FUNCTIONS[("long", "images_generated")]

    def test_short_video_is_different_from_long(self):
        assert STAGE_FUNCTIONS[("short", "video_complete")] is not STAGE_FUNCTIONS[("long", "video_complete")]

    def test_report_submitted_not_in_short(self):
        assert ("short", "report_submitted") not in STAGE_FUNCTIONS

    def test_all_functions_are_callable(self):
        for key, func in STAGE_FUNCTIONS.items():
            assert callable(func), f"Function for {key} is not callable"
