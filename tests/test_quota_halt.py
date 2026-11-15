"""APIのクレジット切れを検知してパイプラインを止めるテスト。

クレジットが尽きると画像生成が失敗し続ける。1件ずつ「失敗」にして進むと、
在庫448件を数分で全部焦がしてしまうので、検知したら全ワーカーを止める。
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from app.models import Story
from app.pipeline.stages import is_quota_error


class TestIsQuotaError:
    @pytest.mark.parametrize("message", [
        "429 RESOURCE_EXHAUSTED. {'error': {'code': 429}}",
        "Your prepayment credits are depleted.",
        "You exceeded your current quota",
        "billing account not configured",
        "insufficient_quota",
    ])
    def test_detects_quota_errors(self, message):
        assert is_quota_error(RuntimeError(message)) is True

    @pytest.mark.parametrize("message", [
        "Narration file not found",
        "動画ファイルが見つかりません",
        "I can't generate an image of a child's face",
        "connection reset by peer",
    ])
    def test_ignores_ordinary_errors(self, message):
        assert is_quota_error(RuntimeError(message)) is False

    def test_looks_at_the_cause_too(self):
        """ImageUnavailableError でラップされていても中身を見る。"""
        from app.services.image_generator import ImageUnavailableError

        try:
            try:
                raise RuntimeError("429 RESOURCE_EXHAUSTED")
            except RuntimeError as inner:
                raise ImageUnavailableError("画像生成に失敗しました") from inner
        except ImageUnavailableError as e:
            assert is_quota_error(e) is True


class TestExecutorHalts:
    def _executor(self, exc, monkeypatch):
        from app.pipeline import executor as ex_mod
        from app.pipeline.executor import StageExecutor

        story = Story(id=1, title="t", content_type="short")
        halted = []
        ex = StageExecutor(
            "images_generated", content_type="short",
            on_fatal_error=lambda reason: halted.append(reason),
        )
        monkeypatch.setattr(ex_mod.db, "mark_failed", MagicMock())
        monkeypatch.setattr(ex_mod.db, "add_log", MagicMock())
        monkeypatch.setattr(ex_mod.db, "update_stage", MagicMock())
        monkeypatch.setitem(
            ex_mod.STAGE_FUNCTIONS, ("short", "images_generated"),
            MagicMock(side_effect=exc),
        )
        ex._process(story)
        return halted

    def test_halts_on_quota_error(self, monkeypatch):
        halted = self._executor(RuntimeError("429 RESOURCE_EXHAUSTED"), monkeypatch)
        assert halted, "クレジット切れで停止していない"

    def test_does_not_halt_on_ordinary_error(self, monkeypatch):
        """普通の失敗は1件スキップして次へ進む (従来どおり)。"""
        halted = self._executor(RuntimeError("Narration file not found"), monkeypatch)
        assert halted == []

    def test_story_is_still_marked_failed(self, monkeypatch):
        from app.pipeline import executor as ex_mod
        from app.pipeline.executor import StageExecutor

        story = Story(id=1, title="t", content_type="short")
        marked = MagicMock()
        ex = StageExecutor("images_generated", content_type="short",
                           on_fatal_error=lambda r: None)
        monkeypatch.setattr(ex_mod.db, "mark_failed", marked)
        monkeypatch.setattr(ex_mod.db, "add_log", MagicMock())
        monkeypatch.setitem(
            ex_mod.STAGE_FUNCTIONS, ("short", "images_generated"),
            MagicMock(side_effect=RuntimeError("429 RESOURCE_EXHAUSTED")),
        )
        ex._process(story)
        marked.assert_called_once()


class TestPipelineHalt:
    def test_halt_stops_all_workers_off_thread(self):
        """ワーカースレッドから stop_all を直接呼ぶと自分待ちで詰まるので別スレッドで。"""
        from app.pipeline.executor import Pipeline

        p = Pipeline(content_type="short")
        with patch.object(p, "stop_all") as stop_all:
            p._halt("429 RESOURCE_EXHAUSTED")
            for _ in range(50):
                if stop_all.called:
                    break
                time.sleep(0.02)
        assert stop_all.called
        assert "RESOURCE_EXHAUSTED" in p.halted_reason

    def test_reason_is_empty_initially(self):
        from app.pipeline.executor import Pipeline

        assert Pipeline(content_type="short").halted_reason == ""

    def test_executors_wired_to_halt(self):
        from app.pipeline.executor import Pipeline

        p = Pipeline(content_type="short")
        for ex in p.executors.values():
            assert ex.on_fatal_error is not None
