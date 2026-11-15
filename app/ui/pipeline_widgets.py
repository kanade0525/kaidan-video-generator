"""パイプライン画面 (長編 / Shorts) で共通の表示部品。

2つの画面はステージ一覧が違うだけで構成は同じなので、見た目を司る部品は
ここにまとめる。スタイル定義は `app/ui/layout.py` の THEME_CSS にある。
"""

from __future__ import annotations

from collections.abc import Callable

from nicegui import ui

from app.models import STAGE_LABELS


def _metric(caption: str) -> ui.label:
    """大きな数字 + 小さな見出しの組。数字のラベルを返す。"""
    with ui.column().classes("gap-0"):
        value = ui.label("0").classes("metric__value metric__value--zero")
        ui.label(caption).classes("metric__label")
    return value


def _set_metric(label: ui.label, count: int) -> None:
    label.text = str(count)
    # 0 は薄く表示して、動きのある数字だけ目に入るようにする
    if count:
        label.classes(remove="metric__value--zero")
    else:
        label.classes(add="metric__value--zero")


def stage_card(
    step: int, stage: str, on_start: Callable[[], None], on_stop: Callable[[], None],
) -> dict:
    """ステージ1つ分のカードを描画し、更新用のハンドルを返す。"""
    with ui.card().classes("stage-card") as root:
        with ui.row().classes("items-start justify-between w-full no-wrap gap-2"):
            with ui.column().classes("gap-0"):
                ui.label(f"STEP {step}").classes("stage-card__step")
                ui.label(STAGE_LABELS.get(stage, stage)).classes("stage-card__title")
            status = ui.label("停止中").classes("status-pill status-pill--idle")

        with ui.row().classes("gap-8 mt-4"):
            active = _metric("処理中")
            queue = _metric("キュー")

        blocked = ui.label("").classes("stage-card__blocked")
        blocked.visible = False

        ui.space()
        with ui.row().classes("gap-2 mt-4"):
            ui.button("開始", on_click=on_start, color="positive").props("size=sm unelevated")
            ui.button("停止", on_click=on_stop, color=None).props(
                "size=sm outline color=negative",
            )

    return {"root": root, "status": status, "active": active, "queue": queue, "blocked": blocked}


def update_stage_card(card: dict, *, running: bool, active: int, blocked: str = "") -> None:
    card["status"].text = "稼働中" if running else "停止中"
    card["status"].classes(
        replace="status-pill " + ("status-pill--running" if running else "status-pill--idle"),
    )
    _set_metric(card["active"], active)
    # ゲート待ち (アップロード上限など) は失敗ではないので、エラーとは別の色で出す
    card["blocked"].text = f"待機: {blocked}" if blocked else ""
    card["blocked"].visible = bool(blocked)
    card["root"].classes(remove="stage-card--running stage-card--blocked")
    if blocked:
        card["root"].classes(add="stage-card--blocked")
    elif running:
        card["root"].classes(add="stage-card--running")


def update_stage_queue(card: dict, count: int) -> None:
    _set_metric(card["queue"], count)


def stage_grid() -> ui.element:
    """ステージカードを並べるグリッド。幅に応じて列数が変わる。"""
    return ui.element("div").classes("w-full grid gap-4").style(
        "grid-template-columns: repeat(auto-fill, minmax(240px, 1fr))",
    )


def section_title(text: str) -> None:
    ui.label(text).classes("section-title")


def halt_banner() -> ui.label:
    """自動停止の理由を出す帯。止まった理由が分からないのが一番困る。"""
    banner = ui.label("").classes("halt-banner")
    banner.visible = False
    return banner


def render_counts(container: ui.element, counts: dict[str, int], stage_order: list[str]) -> None:
    """ステージ別件数。パイプラインの順に並べる (処理中などの派生状態は末尾)。"""
    known = [s for s in stage_order if s in counts]
    others = sorted(s for s in counts if s not in stage_order)
    container.clear()
    with container:
        with ui.row().classes("gap-3 flex-wrap"):
            for stage_name in [*known, *others]:
                with ui.card().classes("count-tile"):
                    ui.label(str(counts[stage_name])).classes("metric__value")
                    ui.label(STAGE_LABELS.get(stage_name, stage_name)).classes("metric__label")


def render_logs(container: ui.element, logs: list[dict]) -> None:
    container.clear()
    with container:
        if not logs:
            ui.label("ログなし").classes("text-gray-500")
            return
        with ui.column().classes("log-panel"):
            for entry in logs:
                ts = entry["timestamp"][:19].replace("T", " ")
                stage = entry.get("stage") or "-"
                msg = entry["message"][:100]
                cls = "log-line log-line--error" if entry["level"] == "ERROR" else "log-line"
                ui.label(f"{ts}  {entry['level']:<5}  {stage} - {msg}").classes(cls)


def autopilot_row(content_type: str) -> None:
    """オートパイロットの状態表示と切り替え。

    ここを ON にしておけば、アプリを再起動しても自動で走り出す
    (設定に永続化される)。投稿しすぎないブレーキは設定ページの
    「自動投稿のブレーキ」で調整する。
    """
    from app.pipeline import autopilot

    with ui.card().classes("w-full mb-4").style("padding: 10px 16px"):
        with ui.row().classes("items-center gap-4 flex-wrap"):
            switch = ui.switch(
                "オートパイロット（全自動運転）",
                value=autopilot.is_enabled(content_type),
            ).props("color=positive")
            state_label = ui.label("").classes("status-pill status-pill--idle")
            ui.label(
                "ONの間は投稿まで自動で進みます。上限は設定ページで調整できます。"
            ).classes("text-xs text-gray-500")

        def _refresh_state():
            running = autopilot.is_running(content_type)
            state_label.text = "稼働中" if running else "停止中"
            state_label.classes(
                replace="status-pill "
                + ("status-pill--running" if running else "status-pill--idle"),
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


def build_pipeline_page(
    *, title: str, content_type: str, pipe, stages: list[str], notify_prefix: str = "",
) -> None:
    """パイプラインの操作・監視画面を組み立てる (長編 / Shorts 共通)。

    stages: 'pending' を含むステージ一覧。カードは pending を除いた各ステージ分。
    """
    from app import database as db

    def notify(msg: str, color: str) -> None:
        ui.notify(f"{notify_prefix}{msg}", color=color)

    def start_all():
        pipe.start_all()
        notify("全ワーカーを開始しました", "positive")

    def stop_all():
        pipe.stop_all()
        notify("全ワーカーを停止しました", "warning")

    def recover():
        count = pipe.recover_stale()
        ui.notify(f"{count}件のスタックジョブを回復しました", color="info")

    def start_stage(stage: str):
        pipe.start_stage(stage)
        ui.notify(f"{STAGE_LABELS.get(stage, stage)} ワーカー開始", color="positive")

    def stop_stage(stage: str):
        pipe.stop_stage(stage)
        ui.notify(f"{STAGE_LABELS.get(stage, stage)} ワーカー停止", color="warning")

    ui.label(title).classes("text-2xl font-bold mb-4")

    autopilot_row(content_type)

    with ui.row().classes("gap-2 mb-5 items-center"):
        ui.button("全て開始", icon="play_arrow", on_click=start_all, color="positive").props(
            "unelevated",
        )
        ui.button("全て停止", icon="stop", on_click=stop_all, color=None).props(
            "outline color=negative",
        )
        ui.button("スタック回復", icon="restart_alt", on_click=recover, color=None).props(
            "flat color=grey-8",
        )

    # 自動停止の理由 (クレジット切れ等)。操作ボタンのすぐ下、カードより上に出す。
    banner = halt_banner()

    # Stage cards (skip 'pending')
    auto_stages = stages[1:]
    stage_cards: dict[str, dict] = {}
    with stage_grid():
        for step, stage in enumerate(auto_stages, start=1):
            stage_cards[stage] = stage_card(
                step, stage,
                on_start=lambda s=stage: start_stage(s),
                on_stop=lambda s=stage: stop_stage(s),
            )

    section_title("ステージ別件数")
    counts_container = ui.column().classes("w-full")

    section_title("最近のログ")
    log_container = ui.column().classes("w-full")

    def refresh():
        for stage, info in pipe.get_status().items():
            if stage in stage_cards:
                update_stage_card(
                    stage_cards[stage],
                    running=info["running"],
                    active=info["active"],
                    blocked=info.get("blocked") or "",
                )

        if pipe.halted_reason:
            banner.text = (
                f"自動停止しました: {pipe.halted_reason[:200]} "
                "／ 原因を解消してから再開してください"
            )
            banner.visible = True
        else:
            banner.visible = False

        # 各ステージのキュー = 1つ前のステージまで終わっている件数
        counts = db.get_stage_counts(content_type=content_type)
        for prev, stage in zip(stages, auto_stages, strict=False):
            update_stage_queue(stage_cards[stage], counts.get(prev, 0))

        render_counts(counts_container, counts, stages)
        render_logs(log_container, db.get_logs(limit=20))

    def safe_refresh():
        try:
            refresh()
        except RuntimeError:
            refresh_timer.active = False

    refresh_timer = ui.timer(3.0, safe_refresh)
    refresh()
