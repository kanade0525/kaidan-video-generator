"""Tests for the retry decorator."""

from unittest.mock import patch

import pytest
import requests

from app.pipeline.retry import with_retry


class TestWithRetry:
    def test_succeeds_first_try(self):
        @with_retry(max_attempts=3, base_delay=0.01)
        def ok():
            return "success"

        assert ok() == "success"

    def test_retries_on_connection_error(self):
        call_count = {"n": 0}

        @with_retry(max_attempts=3, base_delay=0.01)
        def flaky():
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise requests.ConnectionError("network down")
            return "recovered"

        with patch("app.pipeline.retry.time.sleep"):
            result = flaky()
        assert result == "recovered"
        assert call_count["n"] == 3

    def test_raises_after_max_attempts(self):
        @with_retry(max_attempts=2, base_delay=0.01)
        def always_fail():
            raise requests.Timeout("timeout")

        with patch("app.pipeline.retry.time.sleep"):
            with pytest.raises(requests.Timeout):
                always_fail()

    def test_non_retryable_exception_propagates_immediately(self):
        call_count = {"n": 0}

        @with_retry(max_attempts=3, base_delay=0.01)
        def bad():
            call_count["n"] += 1
            raise ValueError("not retryable")

        with pytest.raises(ValueError, match="not retryable"):
            bad()
        assert call_count["n"] == 1  # No retry

    def test_custom_retryable_exceptions(self):
        call_count = {"n": 0}

        @with_retry(max_attempts=3, base_delay=0.01, retryable=(OSError,))
        def flaky():
            call_count["n"] += 1
            if call_count["n"] < 2:
                raise OSError("disk error")
            return "ok"

        with patch("app.pipeline.retry.time.sleep"):
            assert flaky() == "ok"
        assert call_count["n"] == 2

    def test_exponential_backoff(self):
        delays = []

        @with_retry(max_attempts=4, base_delay=1.0, max_delay=10.0)
        def always_fail():
            raise requests.ConnectionError()

        with patch("app.pipeline.retry.time.sleep", side_effect=lambda d: delays.append(d)):
            with pytest.raises(requests.ConnectionError):
                always_fail()

        # base_delay * 2^(attempt-1): 1.0, 2.0, 4.0
        assert delays == [1.0, 2.0, 4.0]

    def test_max_delay_cap(self):
        delays = []

        @with_retry(max_attempts=5, base_delay=10.0, max_delay=15.0)
        def always_fail():
            raise requests.ConnectionError()

        with patch("app.pipeline.retry.time.sleep", side_effect=lambda d: delays.append(d)):
            with pytest.raises(requests.ConnectionError):
                always_fail()

        # 10, 15 (capped), 15 (capped), 15 (capped)
        assert all(d <= 15.0 for d in delays)

    def test_preserves_function_name(self):
        @with_retry(max_attempts=2)
        def my_func():
            pass

        assert my_func.__name__ == "my_func"
