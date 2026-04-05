from __future__ import annotations

import functools
import time
from collections.abc import Callable
from typing import Any

import requests

from app.utils.log import get_logger

log = get_logger("kaidan.retry")


def with_retry(
    max_attempts: int = 3,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
    retryable: tuple[type[Exception], ...] = (
        requests.ConnectionError,
        requests.Timeout,
        requests.HTTPError,
    ),
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
