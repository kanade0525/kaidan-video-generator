"""一時的な通信断で作品を落とさないことのテスト。

実障害: google-genai は httpx を使うのに、リトライ対象が requests の例外
だけだった。そのため画像生成が "Server disconnected" や DNS 一時失敗で
1回落ちただけで確定エラーになり、14件がワーカーに二度と拾われない状態に
なった。
"""

import socket

import pytest
import requests

from app.pipeline.retry import TRANSIENT_NETWORK_ERRORS, with_retry


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)


class TestTransientErrorSet:
    def test_includes_httpx_transport_errors(self):
        """google-genai の通信エラーは httpx 由来。"""
        import httpx

        assert httpx.TransportError in TRANSIENT_NETWORK_ERRORS

    @pytest.mark.parametrize("exc_type", [
        requests.ConnectionError, requests.Timeout,
        ConnectionError, TimeoutError, socket.gaierror,
    ])
    def test_includes_standard_network_errors(self, exc_type):
        assert issubclass(exc_type, TRANSIENT_NETWORK_ERRORS)

    def test_excludes_http_status_errors(self):
        """4xx/5xx は再送しても直らないので対象外。"""
        import httpx

        assert not issubclass(httpx.HTTPStatusError, TRANSIENT_NETWORK_ERRORS)


class TestRetryBehaviour:
    def _flaky(self, exc, fail_times):
        calls = {"n": 0}

        @with_retry(max_attempts=4, base_delay=0)
        def _fn():
            calls["n"] += 1
            if calls["n"] <= fail_times:
                raise exc
            return "ok"

        return _fn, calls

    @pytest.mark.parametrize("exc", [
        pytest.param(ConnectionError("Server disconnected without sending a response."),
                     id="Server disconnected"),
        pytest.param(ConnectionError("peer closed connection without sending complete message"),
                     id="peer closed connection"),
        pytest.param(socket.gaierror(-2, "Name or service not known"),
                     id="DNS 一時失敗"),
    ])
    def test_reported_failures_now_recover(self, exc):
        """実際に作品を止めた3種類のエラーが、再送で復帰すること。"""
        fn, calls = self._flaky(exc, fail_times=1)
        assert fn() == "ok"
        assert calls["n"] == 2

    def test_httpx_read_error_recovers(self):
        import httpx

        fn, calls = self._flaky(httpx.ReadError("read failed"), fail_times=2)
        assert fn() == "ok"
        assert calls["n"] == 3

    def test_gives_up_after_max_attempts(self):
        fn, calls = self._flaky(ConnectionError("down"), fail_times=99)
        with pytest.raises(ConnectionError):
            fn()
        assert calls["n"] == 4

    def test_non_network_error_is_not_retried(self):
        """安全フィルタや設定ミスは再送しても無駄なので即座に上げる。"""
        fn, calls = self._flaky(ValueError("IMAGE_SAFETY"), fail_times=1)
        with pytest.raises(ValueError):
            fn()
        assert calls["n"] == 1

    def test_quota_error_is_not_retried(self):
        """クレジット切れは再送しない (待っても直らないうえ停止が遅れる)。"""
        fn, calls = self._flaky(RuntimeError("429 RESOURCE_EXHAUSTED"), fail_times=1)
        with pytest.raises(RuntimeError):
            fn()
        assert calls["n"] == 1


class TestImageGeneratorSettings:
    def test_image_generation_retries_more_than_once(self):
        """画像1枚の失敗が作品ごと止めるので、粘る設定になっていること。"""
        import inspect

        from app.services import image_generator

        src = inspect.getsource(image_generator)
        assert "@with_retry(max_attempts=4" in src
