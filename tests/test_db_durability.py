"""DB書き込みが黙って失敗しないことのテスト。

実障害: macOS のバインドマウト上で WAL が壊れ、アプリの DB 書き込みが
9/5 21:33 以降ずっと失敗していた。しかもその例外は ThreadPoolExecutor の
Future に飲み込まれ、ログにも DB にも一切残らず、444 件が `:running` の
まま取り残された。
"""

from concurrent.futures import Future
from unittest.mock import MagicMock, patch

import app.database as db
from app.models import Story


class TestJournalMode:
    def test_wal_is_not_used(self):
        """WAL はバインドマウト上で -shm が機能せず、書き込みが死ぬ。"""
        assert db._JOURNAL_MODE.upper() != "WAL"

    def test_connection_uses_configured_mode(self, tmp_path, monkeypatch):
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
        monkeypatch.setattr(db._local, "conn", None, raising=False)
        conn = db._get_conn()
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.upper() == db._JOURNAL_MODE.upper()

    def test_busy_timeout_is_set(self, tmp_path, monkeypatch):
        """同時書き込みで即座に諦めない。"""
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "t2.db")
        monkeypatch.setattr(db._local, "conn", None, raising=False)
        conn = db._get_conn()
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] >= 10000

    def test_second_connection_can_open_while_first_is_open(self, tmp_path, monkeypatch):
        """アプリが掴んでいる間も、別の接続から開けること。

        WAL のときはここが "unable to open database file" で落ちていた。
        """
        import sqlite3

        monkeypatch.setattr(db, "DB_PATH", tmp_path / "t3.db")
        monkeypatch.setattr(db._local, "conn", None, raising=False)
        db.init_db()
        first = db._get_conn()
        first.execute("SELECT 1")

        second = sqlite3.connect(str(tmp_path / "t3.db"), timeout=5)
        assert second.execute("SELECT COUNT(*) FROM stories").fetchone()[0] == 0
        second.close()


class TestUnhandledExceptionIsReported:
    def _executor(self):
        from app.pipeline.executor import StageExecutor

        return StageExecutor("voice_generated", content_type="short")

    def test_exception_escaping_process_is_logged_and_reset(self, monkeypatch, caplog):
        import logging

        from app.pipeline import executor as ex_mod

        marked = MagicMock()
        monkeypatch.setattr(ex_mod.db, "mark_failed", marked)
        ex = self._executor()
        story = Story(id=7, title="幽霊レコード", content_type="short")

        future = Future()
        future.set_exception(sqlite_error := RuntimeError("unable to open database file"))
        with caplog.at_level(logging.ERROR):
            ex._report_unhandled(future, story)

        assert "処理外で例外" in caplog.text
        assert "unable to open database file" in caplog.text
        marked.assert_called_once()
        assert marked.call_args[0][0] == 7
        assert sqlite_error  # 参照して lint を通す

    def test_successful_future_is_ignored(self, monkeypatch):
        from app.pipeline import executor as ex_mod

        marked = MagicMock()
        monkeypatch.setattr(ex_mod.db, "mark_failed", marked)
        future = Future()
        future.set_result(None)
        self._executor()._report_unhandled(future, Story(id=1, title="t"))
        marked.assert_not_called()

    def test_failure_to_reset_is_also_logged(self, monkeypatch, caplog):
        """ステージを戻すことすら失敗したら、それも黙らせない。"""
        import logging

        from app.pipeline import executor as ex_mod

        monkeypatch.setattr(
            ex_mod.db, "mark_failed", MagicMock(side_effect=RuntimeError("db dead")),
        )
        future = Future()
        future.set_exception(RuntimeError("boom"))
        with caplog.at_level(logging.ERROR):
            self._executor()._report_unhandled(future, Story(id=1, title="t"))
        assert "戻しにも失敗" in caplog.text

    def test_submitted_work_gets_a_done_callback(self, monkeypatch):
        """submit しっぱなしにしない。"""
        from app.pipeline import executor as ex_mod
        from app.pipeline.executor import StageExecutor

        story = Story(id=1, title="t", content_type="short")
        monkeypatch.setattr(ex_mod.db, "get_stories_at_stage", lambda *a, **k: [story])
        monkeypatch.setattr(ex_mod.db, "mark_running", MagicMock())
        # 在庫ゲートは実DBを見るので、ここでは素通しにする
        monkeypatch.setitem(
            ex_mod.STAGE_GATES, "voice_generated", lambda ct: (True, ""),
        )

        ex = StageExecutor("voice_generated", content_type="short")
        submitted = {}

        class _FakePool:
            def submit(self, fn, arg):
                f = Future()
                f.set_result(None)
                submitted["future"] = f
                return f

        ex._executor = _FakePool()
        with patch.object(ex, "_report_unhandled") as report:
            original_wait = ex._stop_event.wait

            def _wait(timeout=None):
                ex._stop_event.set()
                return original_wait(0)

            monkeypatch.setattr(ex._stop_event, "wait", _wait)
            ex._poll_loop()

        assert "future" in submitted, "submit されていない"
        report.assert_called()
