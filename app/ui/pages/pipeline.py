from __future__ import annotations

from nicegui import ui

from app import database as db
from app.models import STAGE_LABELS, STAGES
from app.pipeline.executor import pipeline


def pipeline_page():
    """Pipeline control and monitoring page."""
    ui.label("パイプライン").classes("text-2xl font-bold mb-4")

    # Global controls
    with ui.row().classes("gap-2 mb-4"):
        ui.button("全て開始", on_click=lambda: _start_all(), color="green")
        ui.button("全て停止", on_click=lambda: _stop_all(), color="red")
        ui.button("スタック回復", on_click=lambda: _recover())

    # Stage cards
    stage_cards = {}
    with ui.grid(columns=5).classes("w-full gap-4"):
        for stage in STAGES[1:]:
            with ui.card().classes("p-4"):
                label = STAGE_LABELS.get(stage, stage)
                ui.label(label).classes("text-lg font-bold")

                status_label = ui.label("停止中").classes("text-sm text-gray-500")
                active_label = ui.label("処理中: 0").classes("text-sm")
                queue_label = ui.label("キュー: 0").classes("text-sm")

                with ui.row().classes("gap-1 mt-2"):
                    ui.button(
                        "開始",
                        on_click=lambda s=stage: _start_stage(s),
                        color="green",
                    ).props("size=sm")
                    ui.button(
                        "停止",
                        on_click=lambda s=stage: _stop_stage(s),
                        color="red",
                    ).props("size=sm")

                stage_cards[stage] = {
                    "status": status_label,
                    "active": active_label,
                    "queue": queue_label,
                }

    # Stage counts overview
    ui.separator().classes("my-4")
    ui.label("ステージ別件数").classes("text-xl font-bold mb-2")
    counts_container = ui.column()

    # Recent logs
    ui.separator().classes("my-4")
    ui.label("最近のログ").classes("text-xl font-bold mb-2")
    log_container = ui.column()

    def refresh():
        # Update stage cards
        status = pipeline.get_status()
        for stage, info in status.items():
            if stage in stage_cards:
                cards = stage_cards[stage]
                cards["status"].text = "稼働中" if info["running"] else "停止中"
                cards["status"].classes(
                    replace="text-green-500" if info["running"] else "text-gray-500"
                )
                cards["active"].text = f"処理中: {info['active']}"

        # Update queue counts
        counts = db.get_stage_counts()
        input_stages = {
            "scraped": "pending",
            "text_processed": "scraped",
            "voice_generated": "text_processed",
            "images_generated": "voice_generated",
            "video_complete": "images_generated",
        }
        for stage, input_stage in input_stages.items():
            if stage in stage_cards:
                queue_count = counts.get(input_stage, 0)
                stage_cards[stage]["queue"].text = f"キュー: {queue_count}"

        # Update counts overview
        counts_container.clear()
        with counts_container:
            with ui.row().classes("gap-4 flex-wrap"):
                for stage_name, count in sorted(counts.items(), key=lambda x: x[1], reverse=True):
                    label = STAGE_LABELS.get(stage_name, stage_name)
                    with ui.card().classes("p-2 min-w-[120px]"):
                        ui.label(str(count)).classes("text-2xl font-bold text-center")
                        ui.label(label).classes("text-xs text-center text-gray-500")

        # Update logs
        log_container.clear()
        with log_container:
            logs = db.get_logs(limit=20)
            if logs:
                for entry in logs:
                    color = "text-red-400" if entry["level"] == "ERROR" else "text-gray-300"
                    ts = entry["timestamp"][:19]
                    ui.label(
                        f"[{ts}] [{entry['level']}] {entry.get('stage', '')} - {entry['message'][:100]}"
                    ).classes(f"text-xs font-mono {color}")
            else:
                ui.label("ログなし").classes("text-gray-500")

    ui.timer(3.0, refresh)
    refresh()


def _start_all():
    pipeline.start_all()
    ui.notify("全ワーカーを開始しました", color="positive")


def _stop_all():
    pipeline.stop_all()
    ui.notify("全ワーカーを停止しました", color="warning")


def _start_stage(stage: str):
    pipeline.start_stage(stage)
    ui.notify(f"{STAGE_LABELS.get(stage, stage)} ワーカー開始", color="positive")


def _stop_stage(stage: str):
    pipeline.stop_stage(stage)
    ui.notify(f"{STAGE_LABELS.get(stage, stage)} ワーカー停止", color="warning")


def _recover():
    count = pipeline.recover_stale()
    ui.notify(f"{count}件のスタックジョブを回復しました", color="info")
