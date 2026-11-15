"""YouTube アップロードの中断復帰と予約投稿のテスト。

ユーザー報告: 「YouTubeへのアップロードにたまに失敗する（ブロークンパイプ）」。
resumable アップロードは途中から再開できるので、切断されても同じセッションで
続きを送り直す。
"""

from datetime import datetime, timedelta, timezone

import pytest

import app.config as config_module
from app.services import youtube_uploader as yt


@pytest.fixture
def user_config(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr(config_module, "CONFIG_PATH", config_path)

    def _set(**values):
        config_module.save_config(values)

    return _set


class _FakeStatus:
    def __init__(self, progress):
        self._progress = progress

    def progress(self):
        return self._progress


class _FakeRequest:
    """next_chunk() が指定のシナリオ通りに返す/投げるスタブ。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def next_chunk(self):
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)


class TestUploadChunks:
    def test_completes_without_errors(self):
        req = _FakeRequest([
            (_FakeStatus(0.5), None),
            (None, {"id": "abc"}),
        ])
        assert yt._upload_chunks(req)["id"] == "abc"

    def test_reports_progress(self):
        seen = []
        req = _FakeRequest([
            (_FakeStatus(0.25), None),
            (_FakeStatus(0.75), None),
            (None, {"id": "abc"}),
        ])
        yt._upload_chunks(req, progress_callback=lambda a, b: seen.append(a))
        assert seen == [25, 75]

    def test_resumes_after_broken_pipe(self):
        """BrokenPipeError で落ちても同じセッションを再開して完走する。"""
        req = _FakeRequest([
            (_FakeStatus(0.3), None),
            BrokenPipeError("Broken pipe"),
            (_FakeStatus(0.6), None),
            (None, {"id": "abc"}),
        ])
        assert yt._upload_chunks(req)["id"] == "abc"
        assert req.calls == 4

    def test_resumes_after_connection_reset(self):
        req = _FakeRequest([
            ConnectionResetError("reset"),
            (None, {"id": "abc"}),
        ])
        assert yt._upload_chunks(req)["id"] == "abc"

    def test_resumes_after_incomplete_read(self):
        import http.client

        req = _FakeRequest([
            http.client.IncompleteRead(b""),
            (None, {"id": "abc"}),
        ])
        assert yt._upload_chunks(req)["id"] == "abc"

    def test_resumes_after_ssl_error(self):
        import ssl

        req = _FakeRequest([
            ssl.SSLError("eof"),
            (None, {"id": "abc"}),
        ])
        assert yt._upload_chunks(req)["id"] == "abc"

    def test_gives_up_after_max_retries(self):
        req = _FakeRequest([BrokenPipeError("x") for _ in range(10)])
        with pytest.raises(BrokenPipeError):
            yt._upload_chunks(req, max_retries=3)
        assert req.calls == 4  # 初回 + 3 リトライ

    def test_failure_counter_resets_on_success(self):
        """散発的な切断が続く長時間アップロードでも通し切れる。"""
        req = _FakeRequest([
            BrokenPipeError("1"),
            (_FakeStatus(0.2), None),
            BrokenPipeError("2"),
            (_FakeStatus(0.5), None),
            BrokenPipeError("3"),
            (None, {"id": "abc"}),
        ])
        assert yt._upload_chunks(req, max_retries=1)["id"] == "abc"

    def test_does_not_retry_missing_file(self):
        """再送しても直らない OSError は即座に諦める。"""
        req = _FakeRequest([FileNotFoundError("gone")])
        with pytest.raises(FileNotFoundError):
            yt._upload_chunks(req)
        assert req.calls == 1

    def test_does_not_retry_unrelated_error(self):
        req = _FakeRequest([ValueError("bad body")])
        with pytest.raises(ValueError):
            yt._upload_chunks(req)
        assert req.calls == 1


class TestRetriableHttpError:
    def _http_error(self, status):
        from googleapiclient.errors import HttpError

        class _Resp:
            def __init__(self, status):
                self.status = status
                self.reason = "err"

        return HttpError(_Resp(status), b"{}")

    def test_server_errors_are_retriable(self):
        for status in (429, 500, 502, 503, 504):
            assert yt._is_retriable_http_error(self._http_error(status)) is True

    def test_client_errors_are_not(self):
        for status in (400, 401, 403, 404):
            assert yt._is_retriable_http_error(self._http_error(status)) is False

    def test_non_http_error_is_not(self):
        assert yt._is_retriable_http_error(ValueError("x")) is False

    def test_retries_transient_http_error(self):
        req = _FakeRequest([
            self._http_error(503),
            (None, {"id": "abc"}),
        ])
        assert yt._upload_chunks(req)["id"] == "abc"

    def test_does_not_retry_permission_error(self):
        from googleapiclient.errors import HttpError

        req = _FakeRequest([self._http_error(403)])
        with pytest.raises(HttpError):
            yt._upload_chunks(req)
        assert req.calls == 1


class TestIntervalSchedule:
    """N日おき投稿の枠計算。"""

    def test_first_slot_is_today_when_time_not_passed(self, monkeypatch):
        now = datetime(2026, 9, 4, 10, 0, tzinfo=timezone(timedelta(hours=9)))
        monkeypatch.setattr(yt, "_jst_now", lambda: now)
        slot = yt.get_next_interval_publish_time(interval_days=2, hour=21)
        assert slot.startswith("2026-09-04T21:00")

    def test_first_slot_moves_ahead_when_time_passed(self, monkeypatch):
        now = datetime(2026, 9, 4, 22, 0, tzinfo=timezone(timedelta(hours=9)))
        monkeypatch.setattr(yt, "_jst_now", lambda: now)
        slot = yt.get_next_interval_publish_time(interval_days=2, hour=21)
        assert slot.startswith("2026-09-06T21:00")

    def test_stacks_on_top_of_existing_reservation(self, monkeypatch):
        """既に予約済みの枠から更に N 日進める (同じ時刻に固まらない)。"""
        now = datetime(2026, 9, 4, 10, 0, tzinfo=timezone(timedelta(hours=9)))
        monkeypatch.setattr(yt, "_jst_now", lambda: now)
        slot = yt.get_next_interval_publish_time(
            interval_days=2, hour=21, last_scheduled="2026-09-08T21:00:00+09:00",
        )
        assert slot.startswith("2026-09-10T21:00")

    def test_ignores_past_reservation(self, monkeypatch):
        now = datetime(2026, 9, 4, 10, 0, tzinfo=timezone(timedelta(hours=9)))
        monkeypatch.setattr(yt, "_jst_now", lambda: now)
        slot = yt.get_next_interval_publish_time(
            interval_days=2, hour=21, last_scheduled="2026-08-01T21:00:00+09:00",
        )
        assert slot.startswith("2026-09-04T21:00")

    def test_tolerates_unparsable_reservation(self, monkeypatch):
        now = datetime(2026, 9, 4, 10, 0, tzinfo=timezone(timedelta(hours=9)))
        monkeypatch.setattr(yt, "_jst_now", lambda: now)
        slot = yt.get_next_interval_publish_time(
            interval_days=2, hour=21, last_scheduled="not-a-date",
        )
        assert slot.startswith("2026-09-04T21:00")

    def test_always_in_the_future(self, monkeypatch):
        now = datetime(2026, 9, 4, 23, 59, tzinfo=timezone(timedelta(hours=9)))
        monkeypatch.setattr(yt, "_jst_now", lambda: now)
        slot = yt.get_next_interval_publish_time(interval_days=1, hour=0, minute=0)
        assert datetime.fromisoformat(slot) > now

    def test_interval_of_zero_is_clamped(self, monkeypatch):
        now = datetime(2026, 9, 4, 10, 0, tzinfo=timezone(timedelta(hours=9)))
        monkeypatch.setattr(yt, "_jst_now", lambda: now)
        slot = yt.get_next_interval_publish_time(
            interval_days=0, hour=9, last_scheduled="2026-09-05T09:00:00+09:00",
        )
        assert slot.startswith("2026-09-06T09:00")


class TestResolvePublishTime:
    def test_disabled_returns_none(self, user_config):
        user_config(youtube_schedule_enabled=False)
        assert yt.resolve_publish_time() is None

    def test_weekly_mode_uses_weekday(self, user_config, monkeypatch):
        now = datetime(2026, 9, 4, 10, 0, tzinfo=timezone(timedelta(hours=9)))  # 金曜
        monkeypatch.setattr(yt, "_jst_now", lambda: now)
        user_config(
            youtube_schedule_enabled=True,
            youtube_schedule_mode="weekly",
            youtube_schedule_day="saturday",
            youtube_schedule_hour=21,
        )
        assert yt.resolve_publish_time().startswith("2026-09-05T21:00")

    def test_interval_mode_consults_database(self, user_config, monkeypatch):
        now = datetime(2026, 9, 4, 10, 0, tzinfo=timezone(timedelta(hours=9)))
        monkeypatch.setattr(yt, "_jst_now", lambda: now)
        monkeypatch.setattr(
            "app.database.get_latest_scheduled_publish_at",
            lambda: "2026-09-06T21:00:00+09:00",
        )
        user_config(
            youtube_schedule_enabled=True,
            youtube_schedule_mode="interval",
            youtube_schedule_interval_days=2,
            youtube_schedule_hour=21,
        )
        assert yt.resolve_publish_time().startswith("2026-09-08T21:00")
