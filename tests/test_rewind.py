"""ステージの巻き戻し（作り直し）のテスト。

「画像がイマイチだから作り直したい」ときに、ステージを前に戻して
ワーカーに再処理させる機能。取り消せない削除を伴うので、何が消えるかを
事前に列挙できること・消してはいけないものを消さないことを重点的に見る。
"""

import pytest

import app.database as db
from app.pipeline import rewind


@pytest.fixture
def story_env(tmp_path, monkeypatch):
    """出力先と DB を隔離し、一通りの生成物を置いたストーリーを返す。"""
    import app.utils.paths as paths

    monkeypatch.setattr(paths, "OUTPUT_BASE", tmp_path / "output")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "kaidan.db")
    monkeypatch.setattr(db._local, "conn", None, raising=False)
    db.init_db()

    story = db.add_story(url="https://example.com/x", title="テスト怪談")
    ct = story.content_type
    t = story.title

    paths.raw_content_path(t, ct).write_text("原文", encoding="utf-8")
    paths.meta_path(t, ct).write_text("{}", encoding="utf-8")
    paths.processed_text_path(t, ct).write_text("げんぶん", encoding="utf-8")
    paths.chunks_path(t, ct).write_text("[]", encoding="utf-8")
    paths.original_chunks_path(t, ct).write_text("[]", encoding="utf-8")
    paths.narration_source_path(t, ct).write_text("原文", encoding="utf-8")
    paths.source_chunks_path(t, ct).write_text("[]", encoding="utf-8")
    (paths.audio_dir(t, ct) / "narration_0000.wav").write_bytes(b"wav")
    paths.narration_path(t, ct).write_bytes(b"wav")
    (paths.story_dir(t, ct) / "title_narration.wav").write_bytes(b"wav")
    (paths.images_dir(t, ct) / "scene_000.png").write_bytes(b"png")
    (paths.images_dir(t, ct) / "scene_001.png").write_bytes(b"png")
    paths.video_path(t, ct).write_bytes(b"mp4")
    paths.timestamps_path(t, ct).write_text("[]", encoding="utf-8")
    # ユーザーが用意したもの (消してはいけない)
    (paths.story_dir(t, ct) / "custom_thumbnail.png").write_bytes(b"png")
    (paths.story_dir(t, ct) / "slideshow.json").write_text("[]", encoding="utf-8")

    return story, paths


class TestRedoableStages:
    def test_excludes_pending(self):
        assert "pending" not in rewind.redoable_stages("long")

    def test_includes_every_worker_stage(self):
        stages = rewind.redoable_stages("long")
        for s in ("scraped", "text_processed", "voice_generated",
                  "images_generated", "video_complete", "youtube_uploaded"):
            assert s in stages

    def test_short_and_long_both_supported(self):
        assert rewind.redoable_stages("short")
        assert rewind.redoable_stages("long")


class TestPlan:
    def test_rewinds_to_previous_stage(self, story_env):
        """画像からやり直す = 音声生成済みまで戻す。"""
        story, _ = story_env
        plan = rewind.plan(story, "images_generated")
        assert plan.new_stage == "voice_generated"

    def test_first_stage_rewinds_to_pending(self, story_env):
        story, _ = story_env
        assert rewind.plan(story, "scraped").new_stage == "pending"

    def test_redo_stages_include_everything_after(self, story_env):
        story, _ = story_env
        plan = rewind.plan(story, "images_generated")
        assert plan.redo_stages[0] == "images_generated"
        assert "video_complete" in plan.redo_stages
        assert "youtube_uploaded" in plan.redo_stages
        assert "voice_generated" not in plan.redo_stages

    def test_labels_are_human_readable(self, story_env):
        story, _ = story_env
        assert "画像生成済" in rewind.plan(story, "images_generated").redo_labels

    def test_unknown_stage_rejected(self, story_env):
        story, _ = story_env
        with pytest.raises(ValueError):
            rewind.plan(story, "nonexistent")

    def test_pending_rejected(self, story_env):
        story, _ = story_env
        with pytest.raises(ValueError):
            rewind.plan(story, "pending")

    def test_no_artifacts_listed_unless_requested(self, story_env):
        story, _ = story_env
        assert rewind.plan(story, "images_generated").artifacts == []

    def test_lists_artifacts_of_redone_stages_only(self, story_env):
        story, _ = story_env
        plan = rewind.plan(story, "images_generated", delete_artifacts=True)
        names = {p.name for p in plan.artifacts}
        assert "scene_000.png" in names
        assert "テスト怪談.mp4" in names        # 動画もやり直しになる
        assert "narration_complete.wav" not in names   # 音声はやり直さない
        assert "processed_text.txt" not in names

    def test_never_lists_user_owned_files(self, story_env):
        """手動サムネイルとスライドショー設定は生成物ではないので残す。"""
        story, _ = story_env
        plan = rewind.plan(story, "scraped", delete_artifacts=True)
        names = {p.name for p in plan.artifacts}
        assert "custom_thumbnail.png" not in names
        assert "slideshow.json" not in names

    def test_clears_youtube_only_when_upload_is_redone(self, story_env):
        story, _ = story_env
        assert rewind.plan(
            story, "images_generated", clear_youtube=True,
        ).clears_youtube is True
        assert rewind.plan(
            story, "report_submitted", clear_youtube=True,
        ).clears_youtube is False

    def test_plan_does_not_touch_anything(self, story_env):
        """計画を立てるだけでは削除も DB 更新も起きない。"""
        story, paths = story_env
        rewind.plan(story, "scraped", delete_artifacts=True, clear_youtube=True)
        assert paths.raw_content_path(story.title, "long").exists()
        assert db.get_story_by_id(story.id).stage == "pending"


class TestStageArtifacts:
    def test_upload_stage_has_no_files(self, story_env):
        story, _ = story_env
        assert rewind.stage_artifacts(story, "youtube_uploaded") == []
        assert rewind.stage_artifacts(story, "report_submitted") == []

    def test_voice_stage_covers_audio_dir_and_title(self, story_env):
        story, _ = story_env
        names = {p.name for p in rewind.stage_artifacts(story, "voice_generated")}
        assert names == {
            "narration_0000.wav", "narration_complete.wav", "title_narration.wav",
        }

    def test_text_stage_covers_narration_source(self, story_env):
        """アクセント用の原稿も作り直し対象。古い原稿が残ると読みがずれる。"""
        story, _ = story_env
        names = {p.name for p in rewind.stage_artifacts(story, "text_processed")}
        assert "narration_source.txt" in names
        assert "source_chunks.json" in names

    def test_missing_files_are_skipped(self, story_env):
        story, paths = story_env
        paths.narration_path(story.title, "long").unlink()
        names = {p.name for p in rewind.stage_artifacts(story, "voice_generated")}
        assert "narration_complete.wav" not in names


class TestApply:
    def test_moves_stage_back(self, story_env):
        story, _ = story_env
        db.update_stage(story.id, "video_complete")
        rewind.apply(story, rewind.plan(story, "images_generated"))
        assert db.get_story_by_id(story.id).stage == "voice_generated"

    def test_clears_error_so_worker_picks_it_up(self, story_env):
        """エラーが残っているとワーカーが二度と拾わない。"""
        story, _ = story_env
        db.mark_failed(story.id, "images_generated", "boom")
        rewind.apply(story, rewind.plan(story, "images_generated"))
        fresh = db.get_story_by_id(story.id)
        assert not fresh.error
        assert fresh.stage == "voice_generated"

    def test_deletes_only_planned_files(self, story_env):
        story, paths = story_env
        plan = rewind.plan(story, "images_generated", delete_artifacts=True)
        deleted = rewind.apply(story, plan)

        assert deleted == len(plan.artifacts)
        assert not (paths.images_dir(story.title, "long") / "scene_000.png").exists()
        assert not paths.video_path(story.title, "long").exists()
        # やり直さない工程の生成物は残る
        assert paths.narration_path(story.title, "long").exists()
        assert paths.processed_text_path(story.title, "long").exists()
        # ユーザーの素材も残る
        assert (paths.story_dir(story.title, "long") / "custom_thumbnail.png").exists()
        assert (paths.story_dir(story.title, "long") / "slideshow.json").exists()

    def test_keeps_files_when_not_requested(self, story_env):
        story, paths = story_env
        rewind.apply(story, rewind.plan(story, "images_generated"))
        assert (paths.images_dir(story.title, "long") / "scene_000.png").exists()

    def test_clears_youtube_link_when_asked(self, story_env):
        story, _ = story_env
        db.set_youtube_video_id(story.id, "abc123")
        db.set_scheduled_publish_at(story.id, "2026-09-10T21:00:00+09:00")

        rewind.apply(story, rewind.plan(story, "video_complete", clear_youtube=True))

        fresh = db.get_story_by_id(story.id)
        assert fresh.youtube_video_id is None
        assert db.get_latest_scheduled_publish_at() is None

    def test_keeps_youtube_link_by_default(self, story_env):
        """既定では YouTube の紐付けを保つ (誤って二重投稿しないため)。"""
        story, _ = story_env
        db.set_youtube_video_id(story.id, "abc123")
        rewind.apply(story, rewind.plan(story, "video_complete"))
        assert db.get_story_by_id(story.id).youtube_video_id == "abc123"

    def test_clears_later_stage_completions(self, story_env):
        story, _ = story_env
        for stage in ("scraped", "text_processed", "voice_generated",
                      "images_generated", "video_complete"):
            db.update_stage(story.id, stage)

        rewind.apply(story, rewind.plan(story, "images_generated"))

        completed = db.get_story_by_id(story.id).stages_completed
        assert "voice_generated" in completed
        assert "images_generated" not in completed
        assert "video_complete" not in completed

    def test_survives_undeletable_file(self, story_env, monkeypatch):
        """1件消せなくても残りは処理し、ステージは戻す。"""
        story, _ = story_env
        plan = rewind.plan(story, "images_generated", delete_artifacts=True)

        real_unlink = rewind.Path.unlink

        def _flaky(self, *a, **k):
            if self.name == "scene_000.png":
                raise OSError("permission denied")
            return real_unlink(self, *a, **k)

        monkeypatch.setattr(rewind.Path, "unlink", _flaky)
        deleted = rewind.apply(story, plan)

        assert deleted == len(plan.artifacts) - 1
        assert db.get_story_by_id(story.id).stage == "voice_generated"

    def test_logs_the_rewind(self, story_env):
        story, _ = story_env
        rewind.apply(story, rewind.plan(story, "images_generated"))
        messages = [entry["message"] for entry in db.get_logs(limit=10)]
        assert any("巻き戻し" in m for m in messages)


class TestResetToStage:
    def test_rejects_unknown_stage(self, story_env):
        story, _ = story_env
        with pytest.raises(ValueError):
            db.reset_to_stage(story.id, "bogus")

    def test_works_for_shorts(self, story_env):
        story, _ = story_env
        db.convert_to_short(story.id)
        db.reset_to_stage(story.id, "text_processed", content_type="short")
        assert db.get_story_by_id(story.id).stage == "text_processed"
