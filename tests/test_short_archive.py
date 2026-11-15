"""アップ済み Short を保管先 (Google Drive) へ移してローカルを空ける機能のテスト。

取り消せない削除を伴うので、「保管先に確実に移ったときだけ消す」
「怪しいときは何も消さない」ことを重点的に見る。
"""

import pytest

import app.database as db
from app.pipeline import archive


@pytest.fixture
def env(tmp_path, monkeypatch):
    """出力先・DB・保管先を隔離し、生成物一式を持つアップ済み Short を返す。"""
    import app.utils.paths as paths

    monkeypatch.setattr(paths, "OUTPUT_BASE", tmp_path / "output")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "kaidan.db")
    monkeypatch.setattr(db._local, "conn", None, raising=False)
    db.init_db()

    dest = tmp_path / "drive_short"
    dest.mkdir()
    (dest / "既存の動画.mp4").write_bytes(b"old")  # 空でない = マウント済みの保管先
    monkeypatch.setenv(archive.ARCHIVE_DIR_ENV, str(dest))

    story = _make_short(paths, "テスト怪談", video=b"mp4-data")
    return {"paths": paths, "dest": dest, "story": story, "tmp": tmp_path}


def _make_short(paths, title, *, video=b"mp4", stage="youtube_uploaded", video_id="yt123"):
    story = db.add_story(
        url=f"https://example.com/{title}", title=title, content_type="short",
    )
    ct = "short"
    paths.raw_content_path(title, ct).write_text("原文", encoding="utf-8")
    paths.processed_text_path(title, ct).write_text("げんぶん", encoding="utf-8")
    paths.chunks_path(title, ct).write_text("[]", encoding="utf-8")
    sdir = paths.story_dir(title, ct)
    (sdir / "scroll_subtitle.txt").write_text("字幕", encoding="utf-8")
    (sdir / "scroll_subtitle.png").write_bytes(b"png")
    (sdir / "custom_thumbnail.png").write_bytes(b"user-thumb")
    (paths.audio_dir(title, ct) / "narration_0000.wav").write_bytes(b"wav")
    paths.narration_path(title, ct).write_bytes(b"wav")
    (sdir / "title_narration.wav").write_bytes(b"wav")
    (paths.images_dir(title, ct) / "scene_000.png").write_bytes(b"png")
    if video is not None:
        paths.video_path(title, ct).write_bytes(video)
    if video_id:
        db.set_youtube_video_id(story.id, video_id)
    db.update_stage(story.id, stage)
    return db.get_story_by_id(story.id)


def _sdir(env, title="テスト怪談"):
    return env["paths"].OUTPUT_BASE / "shorts" / env["paths"].safe_dirname(title)


class TestArchiveStory:
    def test_moves_video_and_removes_media(self, env):
        assert archive.archive_story(env["story"]) == archive.ARCHIVED

        sdir = _sdir(env)
        assert (env["dest"] / "テスト怪談.mp4").read_bytes() == b"mp4-data"
        assert not (sdir / "テスト怪談.mp4").exists()
        assert not (sdir / "audio").exists()
        assert not (sdir / "images").exists()
        assert not (sdir / "narration_complete.wav").exists()
        assert not (sdir / "title_narration.wav").exists()
        assert not (sdir / "scroll_subtitle.png").exists()

    def test_keeps_text_and_user_files(self, env):
        archive.archive_story(env["story"])

        sdir = _sdir(env)
        for name in ("raw_content.txt", "processed_text.txt", "chunks.json",
                     "scroll_subtitle.txt", "custom_thumbnail.png"):
            assert (sdir / name).exists(), name

    def test_leaves_no_temp_file_in_destination(self, env):
        archive.archive_story(env["story"])
        assert sorted(p.name for p in env["dest"].iterdir()) == [
            "テスト怪談.mp4", "既存の動画.mp4",
        ]

    def test_second_run_is_noop(self, env):
        archive.archive_story(env["story"])
        assert archive.archive_story(env["story"]) == archive.ALREADY
        assert (env["dest"] / "テスト怪談.mp4").read_bytes() == b"mp4-data"

    def test_records_log(self, env):
        archive.archive_story(env["story"])
        logs = db.get_logs()
        assert any("保管先へ移動" in str(entry) for entry in logs)


class TestNothingDeletedWhenUnsafe:
    """少しでも怪しいときはローカルに手を付けない。"""

    def _assert_local_intact(self, env, title="テスト怪談"):
        sdir = _sdir(env, title)
        assert (sdir / f"{title}.mp4").exists()
        assert (sdir / "audio" / "narration_0000.wav").exists()
        assert (sdir / "images" / "scene_000.png").exists()
        assert (sdir / "narration_complete.wav").exists()

    def test_disabled_when_env_unset(self, env, monkeypatch):
        monkeypatch.delenv(archive.ARCHIVE_DIR_ENV)
        assert archive.archive_story(env["story"]) == archive.DISABLED
        self._assert_local_intact(env)

    def test_disabled_when_env_empty(self, env, monkeypatch):
        """compose が未設定時に空文字を渡してくるケース。"""
        monkeypatch.setenv(archive.ARCHIVE_DIR_ENV, "")
        assert archive.archive_story(env["story"]) == archive.DISABLED
        self._assert_local_intact(env)

    def test_destination_missing(self, env, monkeypatch):
        monkeypatch.setenv(archive.ARCHIVE_DIR_ENV, str(env["tmp"] / "nope"))
        assert archive.archive_story(env["story"]) == archive.NOT_READY
        self._assert_local_intact(env)

    def test_empty_destination_is_treated_as_unmounted(self, env, monkeypatch):
        """Drive 未マウントで空フォルダが作られた場合、そこへは移さない。"""
        empty = env["tmp"] / "empty"
        empty.mkdir()
        monkeypatch.setenv(archive.ARCHIVE_DIR_ENV, str(empty))
        assert archive.archive_story(env["story"]) == archive.NOT_READY
        self._assert_local_intact(env)
        assert list(empty.iterdir()) == []

    def test_conflicting_file_is_not_overwritten(self, env):
        (env["dest"] / "テスト怪談.mp4").write_bytes(b"different-older-version")
        assert archive.archive_story(env["story"]) == archive.CONFLICT
        self._assert_local_intact(env)
        assert (env["dest"] / "テスト怪談.mp4").read_bytes() == b"different-older-version"

    def test_copy_error_keeps_local(self, env, monkeypatch):
        def boom(src, dst):
            raise OSError("disk full")

        monkeypatch.setattr(archive.shutil, "copyfile", boom)
        assert archive.archive_story(env["story"]) == archive.FAILED
        self._assert_local_intact(env)

    def test_corrupted_copy_keeps_local_and_cleans_temp(self, env, monkeypatch):
        """コピーが途中で切れて中身が違う場合、消さずに一時ファイルも残さない。"""
        def truncated(src, dst):
            from pathlib import Path
            Path(dst).write_bytes(b"mp4-")

        monkeypatch.setattr(archive.shutil, "copyfile", truncated)
        assert archive.archive_story(env["story"]) == archive.FAILED
        self._assert_local_intact(env)
        assert sorted(p.name for p in env["dest"].iterdir()) == ["既存の動画.mp4"]

    @pytest.mark.parametrize("stage", ["video_complete", "images_generated", "pending"])
    def test_not_uploaded_stage(self, env, stage):
        story = _make_short(env["paths"], "未投稿", stage=stage, video_id=None)
        assert archive.archive_story(story) == archive.NOT_UPLOADED
        self._assert_local_intact(env, "未投稿")

    def test_regenerated_video_awaiting_reupload_is_kept(self, env):
        """動画IDはあるが作り直して video_complete に戻っている作品は対象外。"""
        story = _make_short(env["paths"], "作り直し中", stage="video_complete", video_id="old")
        assert archive.archive_story(story) == archive.NOT_UPLOADED
        self._assert_local_intact(env, "作り直し中")

    def test_uploaded_stage_without_video_id(self, env):
        story = _make_short(env["paths"], "ID無し", video_id=None)
        assert archive.archive_story(story) == archive.NOT_UPLOADED
        self._assert_local_intact(env, "ID無し")

    def test_long_video_is_never_archived(self, env):
        paths = env["paths"]
        story = db.add_story(url="https://example.com/long", title="長編", content_type="long")
        paths.video_path("長編", "long").write_bytes(b"mp4")
        db.set_youtube_video_id(story.id, "yt")
        db.update_stage(story.id, "youtube_uploaded")
        assert archive.archive_story(db.get_story_by_id(story.id)) == archive.NOT_UPLOADED
        assert paths.video_path("長編", "long").exists()


class TestAlreadyInDestination:
    def test_identical_file_already_there_removes_local(self, env):
        """手でコピー済み (中身が同じ) なら、コピーせずローカルだけ消す。"""
        (env["dest"] / "テスト怪談.mp4").write_bytes(b"mp4-data")
        assert archive.archive_story(env["story"]) == archive.ARCHIVED
        assert not (_sdir(env) / "テスト怪談.mp4").exists()
        assert not (_sdir(env) / "audio").exists()

    def test_video_moved_by_hand_cleans_leftover_media(self, env):
        """動画だけ手で移してあり音声・画像が残っている場合、残りを片付ける。"""
        sdir = _sdir(env)
        (sdir / "テスト怪談.mp4").rename(env["dest"] / "テスト怪談.mp4")
        assert archive.archive_story(env["story"]) == archive.ARCHIVED
        assert not (sdir / "audio").exists()
        assert not (sdir / "images").exists()
        assert (sdir / "raw_content.txt").exists()

    def test_no_video_anywhere_touches_nothing(self, env):
        sdir = _sdir(env)
        (sdir / "テスト怪談.mp4").unlink()
        assert archive.archive_story(env["story"]) == archive.NO_VIDEO
        assert (sdir / "audio" / "narration_0000.wav").exists()
        assert (sdir / "images" / "scene_000.png").exists()

    def test_missing_story_dir_is_not_created(self, env):
        """フォルダごと消してある作品を掃き出しても、空フォルダを作り直さない。"""
        import shutil

        (env["dest"] / "テスト怪談.mp4").write_bytes(b"mp4-data")
        shutil.rmtree(_sdir(env))
        assert archive.archive_story(env["story"]) == archive.ALREADY
        assert not _sdir(env).exists()


class TestStages:
    @pytest.mark.parametrize(
        "stage", ["youtube_uploaded", "report_submitted:running", "report_submitted"],
    )
    def test_all_uploaded_stages_are_archived(self, env, stage):
        story = _make_short(env["paths"], "報告段階", stage=stage)
        assert archive.archive_story(story) == archive.ARCHIVED
        assert (env["dest"] / "報告段階.mp4").exists()

    def test_by_id_reads_fresh_state(self, env):
        """ワーカーが持っている story は古い (動画ID無し) ので DB から読み直す。"""
        assert archive.archive_story_by_id(env["story"].id) == archive.ARCHIVED

    def test_by_id_unknown_story(self, env):
        assert archive.archive_story_by_id(999_999) == archive.NOT_UPLOADED


class TestSweep:
    def test_sweeps_only_uploaded_shorts(self, env):
        paths = env["paths"]
        _make_short(paths, "報告済み", stage="report_submitted")
        _make_short(paths, "未投稿", stage="video_complete", video_id=None)

        counts = archive.archive_uploaded_shorts()

        assert counts.get(archive.ARCHIVED) == 2
        assert (env["dest"] / "テスト怪談.mp4").exists()
        assert (env["dest"] / "報告済み.mp4").exists()
        assert not (env["dest"] / "未投稿.mp4").exists()
        assert (_sdir(env, "未投稿") / "未投稿.mp4").exists()

    def test_sweep_does_nothing_when_disabled(self, env, monkeypatch):
        monkeypatch.delenv(archive.ARCHIVE_DIR_ENV)
        assert archive.archive_uploaded_shorts() == {}
        assert (_sdir(env) / "テスト怪談.mp4").exists()

    def test_sweep_continues_after_conflict(self, env):
        _make_short(env["paths"], "二本目", stage="report_submitted")
        (env["dest"] / "テスト怪談.mp4").write_bytes(b"other")

        counts = archive.archive_uploaded_shorts()

        assert counts.get(archive.CONFLICT) == 1
        assert counts.get(archive.ARCHIVED) == 1
        assert (env["dest"] / "二本目.mp4").exists()


class TestExecutorHook:
    """アップロード完了時に移動が走り、失敗してもステージの成否に響かないこと。"""

    def test_run_single_archives_after_youtube_upload(self, env, monkeypatch):
        from app.pipeline import executor, stages

        paths = env["paths"]
        story = _make_short(paths, "手動実行", stage="video_complete", video_id=None)

        def fake_upload(s, progress_callback=None):
            db.mark_uploaded(s.id, "new-id", None)

        monkeypatch.setitem(stages.STAGE_FUNCTIONS, ("short", "youtube_uploaded"), fake_upload)
        executor.Pipeline("short").run_single(story.id, "youtube_uploaded")

        assert db.get_story_by_id(story.id).stage == "youtube_uploaded"
        assert (env["dest"] / "手動実行.mp4").exists()
        assert not (_sdir(env, "手動実行") / "手動実行.mp4").exists()

    def test_archive_failure_does_not_fail_the_stage(self, env, monkeypatch):
        """移動に失敗してもアップロードは成功扱い (再アップロードで重複させない)。"""
        from app.pipeline import executor, stages

        story = _make_short(env["paths"], "移動失敗", stage="video_complete", video_id=None)

        def fake_upload(s, progress_callback=None):
            db.mark_uploaded(s.id, "new-id", None)

        def boom(story_id):
            raise RuntimeError("drive down")

        monkeypatch.setitem(stages.STAGE_FUNCTIONS, ("short", "youtube_uploaded"), fake_upload)
        monkeypatch.setattr(archive, "archive_story_by_id", boom)
        executor.Pipeline("short").run_single(story.id, "youtube_uploaded")

        after = db.get_story_by_id(story.id)
        assert after.stage == "youtube_uploaded"
        assert not after.error

    def test_other_stages_do_not_archive(self, env, monkeypatch):
        from app.pipeline import executor

        called = []
        monkeypatch.setattr(archive, "archive_story_by_id", lambda sid: called.append(sid))
        executor.after_stage_completed("short", "video_complete", 1)
        executor.after_stage_completed("long", "youtube_uploaded", 1)
        assert called == []
        executor.after_stage_completed("short", "youtube_uploaded", 7)
        assert called == [7]
