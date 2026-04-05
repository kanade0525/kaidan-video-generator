from __future__ import annotations

import threading
from collections import defaultdict
from collections.abc import Callable
from typing import Any


class EventBus:
    """Simple thread-safe publish-subscribe event bus."""

    def __init__(self):
        self._listeners: dict[str, list[Callable]] = defaultdict(list)
        self._lock = threading.Lock()

    def subscribe(self, event_type: str, callback: Callable) -> None:
        with self._lock:
            self._listeners[event_type].append(callback)

    def unsubscribe(self, event_type: str, callback: Callable) -> None:
        with self._lock:
            if callback in self._listeners[event_type]:
                self._listeners[event_type].remove(callback)

    def publish(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        with self._lock:
            listeners = list(self._listeners.get(event_type, []))
        for cb in listeners:
            try:
                cb(data or {})
            except Exception:
                pass


# Singleton
bus = EventBus()

# Event types
STAGE_STARTED = "stage_started"
STAGE_COMPLETED = "stage_completed"
STAGE_FAILED = "stage_failed"
WORKER_STARTED = "worker_started"
WORKER_STOPPED = "worker_stopped"
