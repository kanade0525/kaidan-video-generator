from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from app import database as db
from app.models import STAGES, STAGES_SHORT, prev_stage, stages_for
from app.pipeline.events import (
    STAGE_COMPLETED,
    STAGE_FAILED,
    STAGE_STARTED,
    WORKER_STARTED,
    WORKER_STOPPED,
    bus,
)
from app.pipeline.stages import (
    STAGE_FUNCTIONS,
    STAGE_GATES,
    STAGE_POST_DELAY,
    is_quota_error,
)
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

    def __init__(
        self, target_stage: str, content_type: str = "long",
        on_fatal_error: Callable[[str], None] | None = None,
    ):
        self.target_stage = target_stage
        self.content_type = content_type
        # 続けても無駄な失敗 (課金切れ等) を踏んだときに呼ぶ。Pipeline が
        # 全ワーカーの停止を仕込む。
        self.on_fatal_error = on_fatal_error
        self.input_stage = prev_stage(target_stage, content_type) or "pending"
        self.max_workers = STAGE_CONCURRENCY.get(target_stage, 1)
        self._executor: ThreadPoolExecutor | None = None
        self._poll_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._active_count = 0
        self._lock = threading.Lock()
        # ゲートで待たされている理由。5秒ごとに同じ内容を出し続けないよう、
        # 変化したときだけログに出す。UI の表示にも使う。
        self._blocked_reason = ""

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

            stories = db.get_stories_at_stage(
                self.input_stage, limit=1, content_type=self.content_type,
            )
            if not stories:
                self._set_blocked("")
                self._stop_event.wait(POLL_INTERVAL)
                continue

            # 実行ゲート (アップロード上限など)。ブロックされたストーリーは
            # 取り出さずに待つだけなので、失敗扱いにも running にもならない。
            gate = STAGE_GATES.get(self.target_stage)
            if gate is not None:
                allowed, reason = gate(self.content_type)
                if not allowed:
                    self._set_blocked(reason)
                    self._stop_event.wait(POLL_INTERVAL)
                    continue
            self._set_blocked("")

            story = stories[0]
            db.mark_running(story.id, self.target_stage)

            with self._lock:
                self._active_count += 1

            if self._executor:
                future = self._executor.submit(self._process, story)
                # Future を放置すると、_process から漏れた例外がどこにも
                # 出ない。実際にこれで DB 更新の失敗が握りつぶされ、444 件が
                # :running のまま取り残された。必ず回収してログに残す。
                future.add_done_callback(
                    lambda f, s=story: self._report_unhandled(f, s),
                )

            post_delay = STAGE_POST_DELAY.get(self.target_stage, 0.0)
            if post_delay > 0:
                self._stop_event.wait(post_delay)

    def _report_unhandled(self, future, story) -> None:
        """`_process` から漏れた例外を記録し、ストーリーを :running から戻す。

        ここに来るのは `_process` の except 節自体が失敗したときなど。放置すると
        ワーカーが二度と拾わない幽霊レコードになる。
        """
        exc = None
        try:
            exc = future.exception()
        except Exception:  # noqa: BLE001 — cancel 等
            return
        if exc is None:
            return
        msg = f"{type(exc).__name__}: {exc}"[:500]
        log.error(
            "✗✗ %s: %s - 処理外で例外 (ステージを戻します): %s",
            self.target_stage, story.title, msg,
        )
        try:
            db.mark_failed(
                story.id, self.target_stage, f"処理外で例外: {msg}",
                content_type=self.content_type,
            )
        except Exception as e:  # noqa: BLE001 — DB 自体が死んでいる場合
            log.error("ステージの戻しにも失敗しました (DB異常の可能性): %s", e)

    def _set_blocked(self, reason: str) -> None:
        """ゲートの待機理由を更新する。変化したときだけログに残す。"""
        if reason == self._blocked_reason:
            return
        self._blocked_reason = reason
        if reason:
            log.info("%s ワーカー待機中: %s", self.target_stage, reason)
        else:
            log.info("%s ワーカーの待機解除", self.target_stage)

    @property
    def blocked_reason(self) -> str:
        return self._blocked_reason

    def _process(self, story) -> None:
        try:
            func = STAGE_FUNCTIONS[(self.content_type, self.target_stage)]
            bus.publish(STAGE_STARTED, {"stage": self.target_stage, "story_id": story.id})

            func(story)

            db.update_stage(story.id, self.target_stage)
            db.add_log("INFO", f"Completed: {story.title}", self.target_stage, story.id)
            bus.publish(STAGE_COMPLETED, {"stage": self.target_stage, "story_id": story.id})
            log.info("✓ %s: %s", self.target_stage, story.title)
        except Exception as e:
            error_msg = str(e)[:500]
            db.mark_failed(story.id, self.target_stage, error_msg, content_type=self.content_type)
            db.add_log("ERROR", error_msg, self.target_stage, story.id)
            fail_data = {
                "stage": self.target_stage,
                "story_id": story.id,
                "error": error_msg,
            }
            bus.publish(STAGE_FAILED, fail_data)
            log.error("✗ %s: %s - %s", self.target_stage, story.title, error_msg)

            # 課金切れは次のストーリーでも必ず同じ結果になる。放っておくと
            # キュー全件を数分で「失敗」にして在庫を焦がすので、ここで止める。
            if is_quota_error(e):
                log.error(
                    "APIのクレジット切れ/クォータ超過を検知しました。"
                    "%s パイプラインを停止します: %s",
                    self.content_type, error_msg,
                )
                db.add_log(
                    "ERROR",
                    f"APIのクレジット切れを検知したため自動停止しました: {error_msg}",
                    self.target_stage, story.id,
                )
                if self.on_fatal_error:
                    self.on_fatal_error(error_msg)
        finally:
            with self._lock:
                self._active_count -= 1


class Pipeline:
    """Manages all stage executors."""

    def __init__(self, content_type: str = "long"):
        self.content_type = content_type
        stage_list = stages_for(content_type)
        self.executors: dict[str, StageExecutor] = {}
        for stage in stage_list[1:]:  # Skip 'pending'
            self.executors[stage] = StageExecutor(
                stage, content_type=content_type, on_fatal_error=self._halt,
            )
        # 直近の停止理由 (UI 表示用)。停止後もユーザーが理由を確認できるように残す。
        self.halted_reason = ""

    def _halt(self, reason: str) -> None:
        """致命的エラーで全ワーカーを止める。

        ワーカースレッドから呼ばれるので、その場で stop_all() すると自分自身の
        ThreadPoolExecutor.shutdown(wait=True) を待って詰まる。別スレッドに逃がす。
        """
        self.halted_reason = reason
        threading.Thread(target=self.stop_all, daemon=True, name="pipeline-halt").start()

    def start_stage(self, stage: str) -> None:
        if stage in self.executors:
            self.executors[stage].start()

    def stop_stage(self, stage: str) -> None:
        if stage in self.executors:
            self.executors[stage].stop()

    def start_all(self) -> None:
        # 手動で再開したら前回の停止理由は消す (原因を直したはずなので)
        self.halted_reason = ""
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
                "blocked": ex.blocked_reason,
            }
        return result

    def recover_stale(self) -> int:
        return db.recover_running()

    def run_single(
        self,
        story_id: int,
        target_stage: str,
        progress_callback=None,
        **stage_kwargs,
    ) -> None:
        """Run a single story through a stage synchronously (for retry from UI).

        Extra `stage_kwargs` are forwarded to the stage function (e.g.
        `use_ai_proofread=True` for the text stage).
        """
        story = db.get_story_by_id(story_id)
        if not story:
            return

        func = STAGE_FUNCTIONS.get((self.content_type, target_stage))
        if not func:
            return

        db.mark_running(story.id, target_stage)
        try:
            func(story, progress_callback=progress_callback, **stage_kwargs)
            db.update_stage(story.id, target_stage)
            db.add_log("INFO", f"Manual run completed: {story.title}", target_stage, story.id)
        except Exception as e:
            db.mark_failed(story.id, target_stage, str(e)[:500], content_type=self.content_type)
            db.add_log("ERROR", str(e)[:500], target_stage, story.id)
            raise


# Singletons
pipeline = Pipeline(content_type="long")
shorts_pipeline = Pipeline(content_type="short")
