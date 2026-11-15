from __future__ import annotations

from nicegui import ui

from app import database as db
from app.models import STAGE_LABELS, STAGES_SHORT
from app.pipeline.executor import shorts_pipeline


def shorts_pipeline_page():
    """Shorts pipeline control and monitoring page."""
    ui.label("Shorts パイプライン").classes("text-2xl font-bold mb-4")

    # Global controls
    _autopilot_row("short")

    with ui.row().classes("gap-2 mb-4"):
        ui.button("全て開始", on_click=lambda: _start_all(), color="green")
        ui.button("全て停止", on_click=lambda: _stop_all(), color="red")
        ui.button("スタック回復", on_click=lambda: _recover())

    # Stage cards (skip 'pending')
    auto_stages = STAGES_SHORT[1:]  # scraped through youtube_uploaded
    stage_cards = {}
    with ui.grid(columns=4).classes("w-full gap-4"):
        for stage in auto_stages:
            with ui.card().classes("p-4"):
                label = STAGE_LABELS.get(stage, stage)
                ui.label(label).classes("text-lg font-bold")

                status_label = ui.label("停止中").classes("text-sm text-gray-500")
                active_label = ui.label("処理中: 0").classes("text-sm")
                queue_label = ui.label("キュー: 0").classes("text-sm")
                blocked_label = ui.label("").classes("text-xs text-amber-600")

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
                    "blocked": blocked_label,
                }

    # 自動停止の理由 (クレジット切れ等)。止まった理由が分からないのが一番困る。
    halt_banner = ui.label("").classes(
        "w-full p-3 mb-2 rounded bg-red-100 text-red-700 text-sm font-bold",
    )
    halt_banner.visible = False

    # Stage counts overview
    ui.separator().classes("my-4")
    ui.label("ステージ別件数").classes("text-xl font-bold mb-2")
    counts_container = ui.column()

    # Recent logs
    ui.separator().classes("my-4")
    ui.label("最近のログ").classes("text-xl font-bold mb-2")
    log_container = ui.column()

    def refresh():
        status = shorts_pipeline.get_status()
        for stage, info in status.items():
            if stage in stage_cards:
                cards = stage_cards[stage]
                cards["status"].text = "稼働中" if info["running"] else "停止中"
                cards["status"].classes(
                    replace="text-green-500" if info["running"] else "text-gray-500"
                )
                cards["active"].text = f"処理中: {info['active']}"
                # ゲート待ち (アップロード上限など) は失敗ではないので別扱いで出す
                cards["blocked"].text = f"待機: {info['blocked']}" if info.get("blocked") else ""

        if shorts_pipeline.halted_reason:
            halt_banner.text = (
                f"自動停止しました: {shorts_pipeline.halted_reason[:200]} "
                "／ 原因を解消してから再開してください"
            )
            halt_banner.visible = True
        else:
            halt_banner.visible = False

        counts = db.get_stage_counts(content_type="short")
        input_stages = {
            "scraped": "pending",
            "text_processed": "scraped",
            "voice_generated": "text_processed",
            "images_generated": "voice_generated",
            "video_complete": "images_generated",
            "youtube_uploaded": "video_complete",
            "report_submitted": "youtube_uploaded",
        }
        for stage, input_stage in input_stages.items():
            if stage in stage_cards:
                queue_count = counts.get(input_stage, 0)
                stage_cards[stage]["queue"].text = f"キュー: {queue_count}"

        counts_container.clear()
        with counts_container:
            with ui.row().classes("gap-4 flex-wrap"):
                for stage_name, count in sorted(counts.items(), key=lambda x: x[1], reverse=True):
                    label = STAGE_LABELS.get(stage_name, stage_name)
                    with ui.card().classes("p-2 min-w-[120px]"):
                        ui.label(str(count)).classes("text-2xl font-bold text-center")
                        ui.label(label).classes("text-xs text-center text-gray-500")

        log_container.clear()
        with log_container:
            logs = db.get_logs(limit=20)
            if logs:
                for entry in logs:
                    color = "text-red-400" if entry["level"] == "ERROR" else "text-gray-300"
                    ts = entry["timestamp"][:19]
                    stage = entry.get("stage", "")
                    msg = entry["message"][:100]
                    ui.label(
                        f"[{ts}] [{entry['level']}] {stage} - {msg}"
                    ).classes(f"text-xs font-mono {color}")
            else:
                ui.label("ログなし").classes("text-gray-500")

    def safe_refresh():
        try:
            refresh()
        except RuntimeError:
            refresh_timer.active = False

    refresh_timer = ui.timer(3.0, safe_refresh)
    refresh()


def _start_all():
    shorts_pipeline.start_all()
    ui.notify("Shorts 全ワーカーを開始しました", color="positive")


def _stop_all():
    shorts_pipeline.stop_all()
    ui.notify("Shorts 全ワーカーを停止しました", color="warning")


def _start_stage(stage: str):
    shorts_pipeline.start_stage(stage)
    ui.notify(f"{STAGE_LABELS.get(stage, stage)} ワーカー開始", color="positive")


def _stop_stage(stage: str):
    shorts_pipeline.stop_stage(stage)
    ui.notify(f"{STAGE_LABELS.get(stage, stage)} ワーカー停止", color="warning")


def _recover():
    count = shorts_pipeline.recover_stale()
    ui.notify(f"{count}件のスタックジョブを回復しました", color="info")


def _autopilot_row(content_type: str) -> None:
    """オートパイロットの状態表示と切り替え。

    ここを ON にしておけば、アプリを再起動しても自動で走り出す
    (設定に永続化される)。投稿しすぎないブレーキは設定ページの
    「自動投稿のブレーキ」で調整する。
    """
    from app.pipeline import autopilot

    with ui.card().classes("w-full mb-4 p-3"):
        with ui.row().classes("items-center gap-4 flex-wrap"):
            switch = ui.switch(
                "オートパイロット（全自動運転）",
                value=autopilot.is_enabled(content_type),
            )
            state_label = ui.label("").classes("text-sm")
            ui.label(
                "ONの間は投稿まで自動で進みます。上限は設定ページで調整できます。"
            ).classes("text-xs text-gray-500")

        def _refresh_state():
            running = autopilot.is_running(content_type)
            state_label.text = "稼働中" if running else "停止中"
            state_label.classes(
                replace="text-sm font-bold "
                + ("text-green-600" if running else "text-gray-500"),
            )

        def _on_change(e):
            try:
                autopilot.set_enabled(content_type, bool(e.value))
            except Exception as exc:  # noqa: BLE001
                ui.notify(f"切り替えに失敗: {exc}", color="negative")
                return
            _refresh_state()
            ui.notify(
                "オートパイロットを" + ("開始" if e.value else "停止") + "しました",
                color="positive" if e.value else "warning",
            )

        switch.on_value_change(_on_change)
        _refresh_state()

        def _safe():
            try:
                _refresh_state()
            except RuntimeError:
                state_timer.active = False

        state_timer = ui.timer(5.0, _safe)
