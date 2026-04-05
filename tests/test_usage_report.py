"""Tests for HHS usage report stage logic."""

from unittest.mock import MagicMock, patch

import pytest

from app.models import STAGE_LABELS, STAGES, Story
from app.services.youtube_uploader import UsageReportError


class TestUsageReportStage:
    """Tests for report_submitted stage in the pipeline."""

    def test_stage_exists_in_stages(self):
        assert "report_submitted" in STAGES

    def test_stage_after_youtube_uploaded(self):
        yt_idx = STAGES.index("youtube_uploaded")
        rpt_idx = STAGES.index("report_submitted")
        assert rpt_idx == yt_idx + 1

    def test_stage_has_label(self):
        assert "report_submitted" in STAGE_LABELS
        assert STAGE_LABELS["report_submitted"] == "使用報告済"


class TestDoSubmitReport:
    """Tests for the do_submit_report stage function."""

    def _make_story(self, youtube_video_id=None):
        return Story(
            id=1,
            url="https://example.com/story",
            title="テスト怪談",
            stage="youtube_uploaded",
            youtube_video_id=youtube_video_id,
        )

    @patch("app.services.youtube_uploader.submit_usage_report")
    @patch("app.config.get")
    def test_success(self, mock_cfg, mock_submit):
        from app.pipeline.stages import do_submit_report

        mock_cfg.side_effect = lambda k: {
            "youtube_channel_name": "test_channel",
            "youtube_contact_email": "test@example.com",
        }[k]

        story = self._make_story(youtube_video_id="abc123")
        do_submit_report(story)

        mock_submit.assert_called_once_with(
            story_title="テスト怪談",
            video_url="https://youtube.com/watch?v=abc123",
            channel_name="test_channel",
            email="test@example.com",
        )

    def test_no_youtube_video_id_raises(self):
        from app.pipeline.stages import do_submit_report

        story = self._make_story(youtube_video_id=None)
        with pytest.raises(RuntimeError, match="YouTube動画ID"):
            do_submit_report(story)

    @patch("app.config.get")
    def test_missing_channel_name_raises(self, mock_cfg):
        from app.pipeline.stages import do_submit_report

        mock_cfg.side_effect = lambda k: {
            "youtube_channel_name": "",
            "youtube_contact_email": "test@example.com",
        }[k]

        story = self._make_story(youtube_video_id="abc123")
        with pytest.raises(RuntimeError, match="未設定"):
            do_submit_report(story)

    @patch("app.config.get")
    def test_missing_email_raises(self, mock_cfg):
        from app.pipeline.stages import do_submit_report

        mock_cfg.side_effect = lambda k: {
            "youtube_channel_name": "channel",
            "youtube_contact_email": "",
        }[k]

        story = self._make_story(youtube_video_id="abc123")
        with pytest.raises(RuntimeError, match="未設定"):
            do_submit_report(story)

    @patch("app.services.youtube_uploader.submit_usage_report")
    @patch("app.config.get")
    def test_report_error_propagates(self, mock_cfg, mock_submit):
        from app.pipeline.stages import do_submit_report

        mock_cfg.side_effect = lambda k: {
            "youtube_channel_name": "channel",
            "youtube_contact_email": "email@test.com",
        }[k]
        mock_submit.side_effect = UsageReportError("フォーム送信失敗: status=validation_failed")

        story = self._make_story(youtube_video_id="abc123")
        with pytest.raises(UsageReportError, match="validation_failed"):
            do_submit_report(story)

    @patch("app.services.youtube_uploader.submit_usage_report")
    @patch("app.config.get")
    def test_progress_callback(self, mock_cfg, mock_submit):
        from app.pipeline.stages import do_submit_report

        mock_cfg.side_effect = lambda k: {
            "youtube_channel_name": "ch",
            "youtube_contact_email": "e@t.com",
        }[k]

        story = self._make_story(youtube_video_id="abc123")
        cb = MagicMock()
        do_submit_report(story, progress_callback=cb)

        cb.assert_any_call(0, 1)
        cb.assert_any_call(1, 1)


class TestUsageReportError:
    def test_is_runtime_error(self):
        assert issubclass(UsageReportError, RuntimeError)

    def test_message_preserved(self):
        err = UsageReportError("フォーム送信エラー: HTTP 500, body: error")
        assert "HTTP 500" in str(err)


class TestExtractErrorDetail:
    def test_extracts_title(self):
        from app.services.youtube_uploader import _extract_error_detail

        html = '<html><head><title>404 Error - Not Found</title></head><body>...</body></html>'
        assert _extract_error_detail(html) == "404 Error - Not Found"

    def test_falls_back_to_body_text(self):
        from app.services.youtube_uploader import _extract_error_detail

        html = "<html><body><h1>Server Error</h1><p>Something went wrong</p></body></html>"
        result = _extract_error_detail(html)
        assert "Server Error" in result

    def test_empty_response(self):
        from app.services.youtube_uploader import _extract_error_detail

        assert _extract_error_detail("") == "(空のレスポンス)"

    def test_truncates_long_text(self):
        from app.services.youtube_uploader import _extract_error_detail

        html = "<html><body>" + "あ" * 200 + "</body></html>"
        result = _extract_error_detail(html)
        assert len(result) <= 100
