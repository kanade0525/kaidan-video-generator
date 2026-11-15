from __future__ import annotations

import functools
import socket
import time
from collections.abc import Callable
from typing import Any

import requests

from app.utils.log import get_logger

log = get_logger("kaidan.retry")


def _transient_network_errors() -> tuple[type[BaseException], ...]:
    """再送すれば直る見込みのある通信エラー。

    google-genai は requests ではなく **httpx** を使うので、requests の例外だけ
    見ていると画像生成の一時切断 ("Server disconnected", "peer closed
    connection", DNS 一時失敗) が 1 回で確定エラーになる。実際にこれで 14 件が
    停止し、ワーカーが二度と拾わない状態になった。
    """
    errors: list[type[BaseException]] = [
        requests.ConnectionError,
        requests.Timeout,
        requests.HTTPError,
        ConnectionError,        # ConnectionReset/Aborted/BrokenPipe の親
        TimeoutError,
        socket.gaierror,        # "Name or service not known" (DNS 一時失敗)
    ]
    try:
        import httpx

        # TransportError = 接続/読み書き/プロトコルの各エラーの親。
        # HTTPStatusError (4xx/5xx) は含まれないので、恒久的な失敗は再送しない。
        errors.append(httpx.TransportError)
    except ImportError:
        pass
    return tuple(errors)


TRANSIENT_NETWORK_ERRORS = _transient_network_errors()


# 「お金/割当が尽きた」ことを示す文字列。レート制限 (待てば直る) と区別する。
# credits are depleted / billing 系は待っても直らないので、再送も継続もしない。
_CREDIT_EXHAUSTED_MARKERS = (
    "credits are depleted",
    "insufficient_quota",
    "billing",
    "exceeded your current quota",
    "quota exceeded",
)


def is_credit_exhausted(exc: BaseException) -> bool:
    """例外が「クレジット/割当の枯渇」を示すか。

    RESOURCE_EXHAUSTED や 429 は一時的なレート制限でも出るので、それだけでは
    判定しない。残高切れを示す文言があるときだけ True にする。
    """
    message = f"{exc} {getattr(exc, '__cause__', '') or ''}".lower()
    return any(marker in message for marker in _CREDIT_EXHAUSTED_MARKERS)


def with_retry(
    max_attempts: int = 3,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
    retryable: tuple[type[BaseException], ...] = TRANSIENT_NETWORK_ERRORS,
) -> Callable:
    """Decorator that retries a function with exponential backoff."""

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except retryable as e:
                    last_exc = e
                    if attempt == max_attempts:
                        log.error(
                            "%s failed after %d attempts: %s",
                            func.__name__,
                            max_attempts,
                            e,
                        )
                        raise
                    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    log.warning(
                        "%s attempt %d/%d failed (%s), retrying in %.1fs",
                        func.__name__,
                        attempt,
                        max_attempts,
                        e,
                        delay,
                    )
                    time.sleep(delay)
            raise last_exc  # unreachable but satisfies type checker

        return wrapper

    return decorator
