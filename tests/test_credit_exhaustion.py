"""クレジット切れを握りつぶさないことのテスト。

実障害: 残高が尽きた 9/7 21:48 から、パイプラインが止まったのは翌 12:04。
14時間のずれは `_ai_proofread` が例外を飲んで MeCab 結果を返し、
「成功」として処理を続けていたため。
"""

from unittest.mock import patch

import pytest

import app.config as config_module
from app.pipeline.retry import is_credit_exhausted
from app.pipeline.stages import is_quota_error


@pytest.fixture
def user_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "CONFIG_PATH", tmp_path / "config.toml")

    def _set(**values):
        config_module.save_config(values)

    return _set


class TestCreditDetection:
    @pytest.mark.parametrize("message", [
        "429 RESOURCE_EXHAUSTED. Your prepayment credits are depleted.",
        "insufficient_quota",
        "billing account not configured",
        "You exceeded your current quota",
    ])
    def test_detects_exhaustion(self, message):
        assert is_credit_exhausted(RuntimeError(message)) is True

    @pytest.mark.parametrize("message", [
        "429 Too Many Requests: rate limit",
        "503 UNAVAILABLE",
        "Server disconnected without sending a response.",
    ])
    def test_rate_limits_are_not_exhaustion(self, message):
        """レート制限は待てば直るので、止めずに再送させる。"""
        assert is_credit_exhausted(RuntimeError(message)) is False

    def test_looks_at_the_cause(self):
        try:
            try:
                raise RuntimeError("Your prepayment credits are depleted.")
            except RuntimeError as inner:
                raise ValueError("校正に失敗") from inner
        except ValueError as e:
            assert is_credit_exhausted(e) is True

    def test_quota_error_covers_exhaustion(self):
        """パイプライン停止の判定でも拾えること。"""
        assert is_quota_error(RuntimeError("credits are depleted")) is True


class TestProofreadDoesNotSwallow:
    def _run(self, exc, user_config):
        from app.services.text_processor import process_text

        user_config(text_ai_proofread_enabled=True)
        with patch("app.services.text_processor._ai_proofread", side_effect=exc):
            return process_text("昨日の出来事", use_ai_proofread=True)

    def test_credit_exhaustion_propagates(self, user_config):
        """握りつぶすと、校正なしのテキストが延々と作られ続ける。"""
        with pytest.raises(RuntimeError, match="depleted"):
            self._run(RuntimeError("credits are depleted"), user_config)

    def test_ordinary_failure_still_falls_back(self, user_config):
        """一時的な失敗まで止めない。MeCab結果で先に進む。"""
        result = self._run(RuntimeError("503 UNAVAILABLE"), user_config)
        assert "きのう" in result

    def test_fallback_result_is_mecab_output(self, user_config):
        result = self._run(RuntimeError("timeout"), user_config)
        assert "できごと" in result


class TestGeminiRetryStopsOnExhaustion:
    def test_exhaustion_is_not_retried(self):
        """残高切れを再送すると、停止が遅れるだけで直らない。"""
        from app.services.text_processor import _gemini_generate_with_retry

        calls = {"n": 0}

        class _Client:
            class models:
                @staticmethod
                def generate_content(model, contents):
                    calls["n"] += 1
                    raise RuntimeError("429 RESOURCE_EXHAUSTED credits are depleted")

        with pytest.raises(RuntimeError):
            _gemini_generate_with_retry(_Client(), "gemini-2.5-pro", "p")
        assert calls["n"] == 1

    def test_transient_server_error_is_retried(self, monkeypatch):
        from app.services import text_processor

        monkeypatch.setattr("time.sleep", lambda *_: None)
        calls = {"n": 0}

        class _Resp:
            text = "ok"
            usage_metadata = None

        class _Client:
            class models:
                @staticmethod
                def generate_content(model, contents):
                    calls["n"] += 1
                    if calls["n"] == 1:
                        raise RuntimeError("503 UNAVAILABLE")
                    return _Resp()

        text_processor._gemini_generate_with_retry(_Client(), "gemini-2.5-flash", "p")
        assert calls["n"] == 2
