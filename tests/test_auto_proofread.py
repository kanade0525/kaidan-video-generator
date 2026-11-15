"""テキストステージの AI 校正自動化と、予約日時の記録に関するテスト。

ユーザー報告: 「テキスト変換後、必ずジェミニによる校正を入れて確定を押す
作業が発生している」。設定で自動適用できるようにした。
"""

from unittest.mock import patch

import pytest

import app.config as config_module
from app.models import Story


@pytest.fixture
def user_config(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr(config_module, "CONFIG_PATH", config_path)

    def _set(**values):
        config_module.save_config(values)

    return _set


@pytest.fixture
def story_env(tmp_path, monkeypatch):
    """出力先を tmp に逃がしたストーリーを用意する。"""
    import app.utils.paths as paths

    monkeypatch.setattr(paths, "OUTPUT_BASE", tmp_path / "output")
    story = Story(id=1, title="テスト怪談", content_type="long")
    paths.raw_content_path(story.title, "long").write_text(
        "昨日の出来事は本当に怖かった。", encoding="utf-8",
    )
    return story, paths


class TestAutoProofread:
    def _run(self, story_env, user_config, **cfg):
        from app.pipeline.stages import do_text

        story, _ = story_env
        user_config(**cfg)
        with patch("app.services.text_processor.process_text") as pt:
            pt.return_value = "きのうのできごとわほんとうにこわかった。"
            do_text(story)
        return pt.call_args.kwargs["use_ai_proofread"]

    def test_enabled_by_config(self, story_env, user_config):
        assert self._run(story_env, user_config, text_ai_proofread_enabled=True) is True

    def test_disabled_by_config(self, story_env, user_config):
        assert self._run(story_env, user_config, text_ai_proofread_enabled=False) is False

    def test_explicit_argument_wins_over_config(self, story_env, user_config):
        """UI のチェックボックスからの明示指定は設定より優先される。"""
        from app.pipeline.stages import do_text

        story, _ = story_env
        user_config(text_ai_proofread_enabled=True)
        with patch("app.services.text_processor.process_text") as pt:
            pt.return_value = "きのう"
            do_text(story, use_ai_proofread=False)
        assert pt.call_args.kwargs["use_ai_proofread"] is False

    def test_default_config_enables_it(self):
        """既定で自動校正を有効にしてある (手動の確定作業をなくすため)。"""
        assert config_module._DEFAULTS["text_ai_proofread_enabled"] is True


class TestNarrationSourceArtifacts:
    def test_do_text_writes_source_chunks(self, story_env, user_config):
        from app.pipeline.stages import do_text

        story, paths = story_env
        user_config()
        with patch("app.services.text_processor.process_text") as pt:
            pt.return_value = "きのうのできごとわほんとうにこわかった。"
            do_text(story)

        assert paths.narration_source_path(story.title, "long").exists()
        import json
        chunks = json.loads(paths.chunks_path(story.title, "long").read_text())
        sources = json.loads(paths.source_chunks_path(story.title, "long").read_text())
        assert len(sources) == len(chunks)

    def test_load_source_chunks_requires_matching_count(self, story_env, user_config):
        import json

        from app.pipeline.stages import load_source_chunks

        story, paths = story_env
        user_config()
        paths.source_chunks_path(story.title, "long").write_text(
            json.dumps(["a", "b"]), encoding="utf-8",
        )
        assert load_source_chunks(story, 2) == ["a", "b"]
        assert load_source_chunks(story, 3) is None

    def test_load_source_chunks_missing_file(self, story_env, user_config):
        from app.pipeline.stages import load_source_chunks

        story, _ = story_env
        user_config()
        assert load_source_chunks(story, 1) is None

    def test_load_source_chunks_tolerates_broken_json(self, story_env, user_config):
        from app.pipeline.stages import load_source_chunks

        story, paths = story_env
        user_config()
        paths.source_chunks_path(story.title, "long").write_text("{", encoding="utf-8")
        assert load_source_chunks(story, 1) is None

    def test_save_narration_source_survives_failure(self, story_env, user_config):
        """原稿生成に失敗してもテキストステージは止めない。"""
        from app.pipeline.stages import save_narration_source

        story, _ = story_env
        user_config()
        with patch(
            "app.services.text_processor.build_narration_source",
            side_effect=RuntimeError("mecab down"),
        ):
            assert save_narration_source(story, "本文", 1) is False

    def test_save_narration_source_noop_without_mecab(self, story_env, user_config):
        from app.pipeline.stages import save_narration_source

        story, _ = story_env
        user_config()
        with patch(
            "app.services.text_processor.build_narration_source", return_value=None,
        ):
            assert save_narration_source(story, "本文", 1) is False


class TestScheduledPublishTracking:
    def test_records_and_reads_back_latest(self, tmp_path, monkeypatch):
        import app.database as db

        monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
        monkeypatch.setattr(db._local, "conn", None, raising=False)
        db.init_db()

        a = db.add_story(url="https://example.com/a", title="A").id
        b = db.add_story(url="https://example.com/b", title="B").id
        assert db.get_latest_scheduled_publish_at() is None

        db.set_scheduled_publish_at(a, "2026-09-06T21:00:00+09:00")
        db.set_scheduled_publish_at(b, "2026-09-08T21:00:00+09:00")
        assert db.get_latest_scheduled_publish_at() == "2026-09-08T21:00:00+09:00"

    def test_counts_only_real_uploads(self, tmp_path, monkeypatch):
        """日次上限は「実際に送信した本数」で数える。

        ステージ完了で数えると、既に投稿済みでスキップされた作品まで枠を
        消費してしまい、1本も上がっていないのに上限に達する。
        """
        from datetime import datetime, timedelta, timezone

        import app.database as db

        monkeypatch.setattr(db, "DB_PATH", tmp_path / "test2.db")
        monkeypatch.setattr(db._local, "conn", None, raising=False)
        db.init_db()

        uploaded = db.add_story(url="https://example.com/c", title="C").id
        skipped = db.add_story(url="https://example.com/d", title="D").id

        db.mark_uploaded(uploaded, "vid123", "2026-09-10T21:00:00+09:00")
        # 既に投稿済みでスキップされた作品: ステージだけ進む
        db.update_stage(skipped, "youtube_uploaded")

        recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        assert db.count_uploads_since(recent) == 1
        assert db.count_uploads_since(future) == 0

    def test_mark_uploaded_records_everything(self, tmp_path, monkeypatch):
        import app.database as db

        monkeypatch.setattr(db, "DB_PATH", tmp_path / "test3.db")
        monkeypatch.setattr(db._local, "conn", None, raising=False)
        db.init_db()

        sid = db.add_story(url="https://example.com/e", title="E").id
        db.mark_uploaded(sid, "vid999", "2026-09-12T21:00:00+09:00")

        assert db.get_story_by_id(sid).youtube_video_id == "vid999"
        assert db.get_latest_scheduled_publish_at() == "2026-09-12T21:00:00+09:00"
