"""完全自動運転 (オートパイロット) とそのブレーキのテスト。

未処理ストーリーが1000件以上あるので、ワーカーを全部動かすと YouTube の
1日クォータを使い切り、予約枠も何ヶ月も先まで埋まってしまう。
ゲートで「1日の本数」と「予約の先読み日数」を制限する。
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

import app.config as config_module
from app.models import Story
from app.pipeline.stages import STAGE_GATES, STAGE_POST_DELAY, youtube_upload_gate


@pytest.fixture
def user_config(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr(config_module, "CONFIG_PATH", config_path)

    def _set(**values):
        config_module.save_config(values)

    return _set


def _future(days: int) -> str:
    return (datetime.now(timezone(timedelta(hours=9))) + timedelta(days=days)).isoformat()


class TestYoutubeUploadGate:
    def test_allows_when_under_limits(self, user_config, monkeypatch):
        user_config(youtube_daily_upload_limit=5, youtube_schedule_max_ahead_days=14)
        monkeypatch.setattr("app.database.count_uploads_since", lambda *a: 0)
        monkeypatch.setattr("app.database.get_latest_scheduled_publish_at", lambda: None)
        allowed, reason = youtube_upload_gate("long")
        assert allowed is True
        assert reason == ""

    def test_blocks_at_daily_limit(self, user_config, monkeypatch):
        user_config(youtube_daily_upload_limit=5)
        monkeypatch.setattr("app.database.count_uploads_since", lambda *a: 5)
        monkeypatch.setattr("app.database.get_latest_scheduled_publish_at", lambda: None)
        allowed, reason = youtube_upload_gate("long")
        assert allowed is False
        assert "上限" in reason

    def test_blocks_when_schedule_full(self, user_config, monkeypatch):
        """予約が先読み日数より先まで埋まっていたら止める。"""
        user_config(
            youtube_daily_upload_limit=5,
            youtube_schedule_max_ahead_days=14,
            youtube_schedule_enabled=True,
        )
        monkeypatch.setattr("app.database.count_uploads_since", lambda *a: 0)
        monkeypatch.setattr(
            "app.database.get_latest_scheduled_publish_at", lambda: _future(30),
        )
        allowed, reason = youtube_upload_gate("long")
        assert allowed is False
        assert "予約投稿" in reason

    def test_allows_when_schedule_within_horizon(self, user_config, monkeypatch):
        user_config(
            youtube_daily_upload_limit=5,
            youtube_schedule_max_ahead_days=14,
            youtube_schedule_enabled=True,
        )
        monkeypatch.setattr("app.database.count_uploads_since", lambda *a: 0)
        monkeypatch.setattr(
            "app.database.get_latest_scheduled_publish_at", lambda: _future(3),
        )
        assert youtube_upload_gate("long")[0] is True

    def test_horizon_ignored_when_scheduling_disabled(self, user_config, monkeypatch):
        user_config(
            youtube_daily_upload_limit=5,
            youtube_schedule_max_ahead_days=14,
            youtube_schedule_enabled=False,
        )
        monkeypatch.setattr("app.database.count_uploads_since", lambda *a: 0)
        monkeypatch.setattr(
            "app.database.get_latest_scheduled_publish_at", lambda: _future(90),
        )
        assert youtube_upload_gate("long")[0] is True

    def test_zero_limit_disables_daily_check(self, user_config, monkeypatch):
        user_config(youtube_daily_upload_limit=0, youtube_schedule_max_ahead_days=0)
        monkeypatch.setattr("app.database.count_uploads_since", lambda *a: 999)
        monkeypatch.setattr("app.database.get_latest_scheduled_publish_at", lambda: None)
        assert youtube_upload_gate("long")[0] is True

    def test_tolerates_unparsable_reservation(self, user_config, monkeypatch):
        user_config(youtube_daily_upload_limit=5, youtube_schedule_max_ahead_days=14)
        monkeypatch.setattr("app.database.count_uploads_since", lambda *a: 0)
        monkeypatch.setattr(
            "app.database.get_latest_scheduled_publish_at", lambda: "壊れた日時",
        )
        assert youtube_upload_gate("long")[0] is True


class TestVideoInventoryGate:
    """未投稿の完成動画が溜まったら生成を止める。"""

    def _gate(self, ready_count, user_config, monkeypatch, **cfg):
        from app.pipeline.stages import video_inventory_gate

        user_config(**cfg)
        monkeypatch.setattr(
            "app.database.get_stage_counts",
            lambda content_type=None: {"video_complete": ready_count},
        )
        return video_inventory_gate("long")

    def test_allows_when_inventory_low(self, user_config, monkeypatch):
        allowed, reason = self._gate(
            3, user_config, monkeypatch, autopilot_max_ready_videos=10,
        )
        assert allowed is True
        assert reason == ""

    def test_blocks_at_limit(self, user_config, monkeypatch):
        allowed, reason = self._gate(
            10, user_config, monkeypatch, autopilot_max_ready_videos=10,
        )
        assert allowed is False
        assert "未投稿の完成動画" in reason

    def test_blocks_over_limit(self, user_config, monkeypatch):
        assert self._gate(
            99, user_config, monkeypatch, autopilot_max_ready_videos=10,
        )[0] is False

    def test_zero_disables_the_brake(self, user_config, monkeypatch):
        """0 にすると従来どおり際限なく生成する (手動一括生成用)。"""
        assert self._gate(
            999, user_config, monkeypatch, autopilot_max_ready_videos=0,
        )[0] is True

    def test_counts_are_per_content_type(self, user_config, monkeypatch):
        """長編の在庫で Shorts が止まらない。"""
        from app.pipeline.stages import video_inventory_gate

        user_config(autopilot_max_ready_videos=5)
        counts = {"long": {"video_complete": 99}, "short": {"video_complete": 0}}
        monkeypatch.setattr(
            "app.database.get_stage_counts",
            lambda content_type=None: counts[content_type],
        )
        assert video_inventory_gate("long")[0] is False
        assert video_inventory_gate("short")[0] is True


class TestStageGateRegistry:
    def test_upload_stage_is_gated(self):
        assert "youtube_uploaded" in STAGE_GATES

    def test_expensive_stages_are_gated(self):
        """金と容量を食う工程は在庫ブレーキの対象。"""
        from app.pipeline.stages import video_inventory_gate

        for stage in ("voice_generated", "images_generated", "video_complete"):
            assert STAGE_GATES[stage] is video_inventory_gate

    def test_cheap_stages_are_not_gated(self):
        """スクレイピングとテキスト処理は素通し。"""
        for stage in ("scraped", "text_processed"):
            assert stage not in STAGE_GATES

    def test_report_stage_has_post_delay(self):
        """使用報告は外部フォーム送信なので連投しない。"""
        assert STAGE_POST_DELAY["report_submitted"] > 0

    def test_scrape_delay_preserved(self):
        assert STAGE_POST_DELAY["scraped"] == 2.0


class TestExecutorHonoursGate:
    def _executor(self, gate_result):
        from app.pipeline.executor import StageExecutor

        ex = StageExecutor("youtube_uploaded", content_type="long")
        ex._stop_event.set()  # ループを1周で抜ける
        return ex

    def test_blocked_reason_is_exposed(self):
        from app.pipeline.executor import StageExecutor

        ex = StageExecutor("youtube_uploaded", content_type="long")
        assert ex.blocked_reason == ""
        ex._set_blocked("上限に到達")
        assert ex.blocked_reason == "上限に到達"
        ex._set_blocked("")
        assert ex.blocked_reason == ""

    def test_gate_blocks_story_from_being_claimed(self, monkeypatch):
        """ゲートで止まったストーリーは running にならない (失敗扱いにしない)。"""
        from app.pipeline import executor as executor_module
        from app.pipeline.executor import StageExecutor

        story = Story(id=1, title="t", stage="video_complete", content_type="long")
        monkeypatch.setattr(
            executor_module.db, "get_stories_at_stage", lambda *a, **k: [story],
        )
        marked = []
        monkeypatch.setattr(
            executor_module.db, "mark_running", lambda *a: marked.append(a),
        )
        monkeypatch.setitem(
            executor_module.STAGE_GATES, "youtube_uploaded",
            lambda ct: (False, "上限に到達"),
        )

        ex = StageExecutor("youtube_uploaded", content_type="long")
        # 1周だけ回して抜けるよう、wait が呼ばれたら停止させる
        original_wait = ex._stop_event.wait

        def _wait(timeout=None):
            ex._stop_event.set()
            return original_wait(0)

        monkeypatch.setattr(ex._stop_event, "wait", _wait)
        ex._poll_loop()

        assert marked == []
        assert ex.blocked_reason == "上限に到達"


class TestAutopilot:
    def test_disabled_by_default(self, user_config):
        from app.pipeline import autopilot

        user_config()
        assert autopilot.is_enabled("long") is False
        assert autopilot.is_enabled("short") is False

    def test_set_enabled_persists_and_starts(self, user_config):
        from app.pipeline import autopilot

        user_config()
        fake = MagicMock()
        with patch.object(autopilot, "pipeline", fake):
            autopilot.set_enabled("long", True)
        assert autopilot.is_enabled("long") is True
        fake.start_all.assert_called_once()

    def test_set_enabled_false_stops(self, user_config):
        from app.pipeline import autopilot

        user_config(autopilot_long_enabled=True)
        fake = MagicMock()
        with patch.object(autopilot, "pipeline", fake):
            autopilot.set_enabled("long", False)
        assert autopilot.is_enabled("long") is False
        fake.stop_all.assert_called_once()

    def test_unknown_content_type_rejected(self, user_config):
        from app.pipeline import autopilot

        user_config()
        with pytest.raises(ValueError):
            autopilot.set_enabled("medium", True)

    def test_start_if_enabled_only_starts_enabled_pipelines(self, user_config):
        from app.pipeline import autopilot

        user_config(autopilot_long_enabled=True, autopilot_short_enabled=False)
        long_pl, short_pl = MagicMock(), MagicMock()
        long_pl.recover_stale.return_value = 0
        with patch.object(autopilot, "pipeline", long_pl), \
                patch.object(autopilot, "shorts_pipeline", short_pl):
            started = autopilot.start_if_enabled()
        assert started == ["long"]
        long_pl.start_all.assert_called_once()
        short_pl.start_all.assert_not_called()

    def test_start_if_enabled_recovers_stale_first(self, user_config):
        """前回の異常終了で :running のまま残ったストーリーを先に戻す。"""
        from app.pipeline import autopilot

        user_config(autopilot_long_enabled=True)
        pl = MagicMock()
        pl.recover_stale.return_value = 3
        with patch.object(autopilot, "pipeline", pl):
            autopilot.start_if_enabled()
        pl.recover_stale.assert_called_once()

    def test_start_if_enabled_noop_when_all_disabled(self, user_config):
        from app.pipeline import autopilot

        user_config()
        long_pl, short_pl = MagicMock(), MagicMock()
        with patch.object(autopilot, "pipeline", long_pl), \
                patch.object(autopilot, "shorts_pipeline", short_pl):
            assert autopilot.start_if_enabled() == []
        long_pl.start_all.assert_not_called()


class TestUsageReportAutomation:
    def test_non_hhs_story_is_skipped(self):
        """奇々怪々由来は報告先が無いので、失敗させずスキップする。"""
        from app.pipeline.stages import do_submit_report

        story = Story(id=1, title="t", source="kikikaikai", youtube_video_id="")
        with patch("app.services.youtube_uploader.submit_usage_report") as submit:
            do_submit_report(story)
        submit.assert_not_called()

    def test_hhs_story_without_video_id_raises(self):
        from app.pipeline.stages import do_submit_report

        story = Story(id=1, title="t", source="hhs", youtube_video_id=None)
        with pytest.raises(RuntimeError):
            do_submit_report(story)

    def test_progress_reported_for_skipped_story(self):
        from app.pipeline.stages import do_submit_report

        seen = []
        story = Story(id=1, title="t", source="kikikaikai")
        do_submit_report(story, progress_callback=lambda a, b: seen.append((a, b)))
        assert seen[-1] == (1, 1)
