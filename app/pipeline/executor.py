from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from app import database as db
from app.models import STAGES, prev_stage
from app.pipeline.events import (
    STAGE_COMPLETED,
    STAGE_FAILED,
    STAGE_STARTED,
    WORKER_STARTED,
    WORKER_STOPPED,
    bus,
)
from app.pipeline.stages import STAGE_FUNCTIONS
from app.utils.log import get_logger

log = get_logger("kaidan.executor")

STAGE_CONCURRENCY = {
    "scraped": 1,
    "text_processed": 2,
    "voice_generated": 1,
    "images_generated": 1,
    "video_complete": 1,
}

POLL_INTERVAL = 5.0


class StageExecutor:
    """Manages workers for a single pipeline stage."""

    def __init__(self, target_stage: str):
        self.target_stage = target_stage
        self.input_stage = prev_stage(target_stage) or "pending"
        self.max_workers = STAGE_CONCURRENCY.get(target_stage, 1)
        self._executor: ThreadPoolExecutor | None = None
        self._poll_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._active_count = 0
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._poll_thread is not None and self._poll_thread.is_alive()

    @property
    def active_count(self) -> int:
        return self._active_count

    def start(self) -> None:
        if self.running:
            return
        self._stop_event.clear()
        self._executor = ThreadPoolExecutor(max_workers=self.max_workers)
        self._poll_thread = threading.Thread(
            target=self._poll_loop, daemon=True, name=f"worker-{self.target_stage}"
        )
        self._poll_thread.start()
        bus.publish(WORKER_STARTED, {"stage": self.target_stage})
        log.info("Worker started: %s", self.target_stage)

    def stop(self) -> None:
        if not self.running:
            return
        self._stop_event.set()
        if self._executor:
            self._executor.shutdown(wait=True, cancel_futures=False)
            self._executor = None
        self._poll_thread = None
        bus.publish(WORKER_STOPPED, {"stage": self.target_stage})
        log.info("Worker stopped: %s", self.target_stage)

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            if self._active_count >= self.max_workers:
                self._stop_event.wait(POLL_INTERVAL)
                continue

            stories = db.get_stories_at_stage(self.input_stage, limit=1)
            if not stories:
                self._stop_event.wait(POLL_INTERVAL)
                continue

            story = stories[0]
            db.mark_running(story.id, self.target_stage)

            with self._lock:
                self._active_count += 1

            if self._executor:
                self._executor.submit(self._process, story)

            # Scraping delay
            if self.target_stage == "scraped":
                self._stop_event.wait(2.0)

    def _process(self, story) -> None:
        try:
            func = STAGE_FUNCTIONS[self.target_stage]
            bus.publish(STAGE_STARTED, {"stage": self.target_stage, "story_id": story.id})

            func(story)

            db.update_stage(story.id, self.target_stage)
            db.add_log("INFO", f"Completed: {story.title}", self.target_stage, story.id)
            bus.publish(STAGE_COMPLETED, {"stage": self.target_stage, "story_id": story.id})
            log.info("✓ %s: %s", self.target_stage, story.title)
        except Exception as e:
            error_msg = str(e)[:500]
            db.mark_failed(story.id, self.target_stage, error_msg)
            db.add_log("ERROR", error_msg, self.target_stage, story.id)
            bus.publish(STAGE_FAILED, {"stage": self.target_stage, "story_id": story.id, "error": error_msg})
            log.error("✗ %s: %s - %s", self.target_stage, story.title, error_msg)
        finally:
            with self._lock:
                self._active_count -= 1


class Pipeline:
    """Manages all stage executors."""

    def __init__(self):
        self.executors: dict[str, StageExecutor] = {}
        for stage in STAGES[1:]:  # Skip 'pending'
            self.executors[stage] = StageExecutor(stage)

    def start_stage(self, stage: str) -> None:
        if stage in self.executors:
            self.executors[stage].start()

    def stop_stage(self, stage: str) -> None:
        if stage in self.executors:
            self.executors[stage].stop()

    def start_all(self) -> None:
        for ex in self.executors.values():
            ex.start()

    def stop_all(self) -> None:
        for ex in self.executors.values():
            ex.stop()

    def is_stage_running(self, stage: str) -> bool:
        return stage in self.executors and self.executors[stage].running

    def get_status(self) -> dict[str, dict]:
        result = {}
        for stage, ex in self.executors.items():
            result[stage] = {
                "running": ex.running,
                "active": ex.active_count,
            }
        return result

    def recover_stale(self) -> int:
        return db.recover_running()

    def run_single(self, story_id: int, target_stage: str) -> None:
        """Run a single story through a stage synchronously (for retry from UI)."""
        story = db.get_story_by_id(story_id)
        if not story:
            return

        func = STAGE_FUNCTIONS.get(target_stage)
        if not func:
            return

        db.mark_running(story.id, target_stage)
        try:
            func(story)
            db.update_stage(story.id, target_stage)
            db.add_log("INFO", f"Manual run completed: {story.title}", target_stage, story.id)
        except Exception as e:
            db.mark_failed(story.id, target_stage, str(e)[:500])
            db.add_log("ERROR", str(e)[:500], target_stage, story.id)
            raise


# Singleton
pipeline = Pipeline()
