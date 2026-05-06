"""詰め合わせ動画 (bundle) generation UI page.

Shows video_complete Long stories with checkboxes and order controls,
estimates total duration in real time, and triggers bundle generation.
"""

from __future__ import annotations

import threading
from pathlib import Path

from nicegui import ui

from app import database as db
from app.config import get as cfg_get
from app.utils.log import get_logger
from app.utils.paths import (
    bundle_video_path,
    narration_path,
    video_path,
)

log = get_logger("kaidan.ui.bundle")


def bundle_page():
    """Render the 詰め合わせ動画 creation page."""
    ui.label("詰め合わせ動画 (1〜2時間の長編)").classes("text-2xl font-bold mb-2")
    ui.label(
        "生成済み Long ストーリーを順序付きで選び、OP+ジングル区切り+ED で繋ぎます。"
        "各ストーリーの中間生成物 (タイトルカード/ナレーション/シーン画像/字幕) から"
        "再構築するので、既存最終 mp4 の OP/ED 重複は発生しません。",
    ).classes("text-sm text-gray-600 mb-4")

    # State: selected story IDs in display order
    state: dict = {"order": []}  # list[story_id]

    # Fetch candidates: 動画生成済以降 (video_complete, youtube_uploaded, report_submitted)
    # YouTube投稿済 / 使用報告済も詰め合わせ素材として再利用可能。
    bundle_eligible_stages = ("video_complete", "youtube_uploaded", "report_submitted")
    stories: list = []
    seen: set[int] = set()
    for st in bundle_eligible_stages:
        for s in db.get_stories(stage=st, content_type="long", limit=500):
            if s.id not in seen:
                stories.append(s)
                seen.add(s.id)
    story_map = {s.id: s for s in stories}

    if not stories:
        ui.label("詰め合わせ素材になる Long ストーリーがありません (動画生成済以降)。").classes(
            "text-orange-500",
        )
        return

    # Pre-compute narration durations (cheap once at page load)
    durations: dict[int, float] = {}
    for s in stories:
        n = narration_path(s.title, "long")
        if n.exists():
            try:
                from app.utils.ffmpeg import get_audio_duration
                durations[s.id] = get_audio_duration(n)
            except Exception:
                durations[s.id] = 0.0

    # ── Total duration estimate display ────────────────────────────────────
    total_label = ui.label("").classes("text-lg font-semibold text-blue-600")
    target_min = cfg_get("bundle_target_duration_min") or 3600
    target_max = cfg_get("bundle_target_duration_max") or 7200

    def update_total():
        total = sum(durations.get(sid, 0.0) for sid in state["order"])
        # Add ~6s per title call (rough) and ~0.5s per jingle gap
        per_title_overhead = 8.0
        gap_overhead = max(0, len(state["order"]) - 1) * 0.5
        total += len(state["order"]) * per_title_overhead + gap_overhead
        # Add OP / ED if configured
        for key in ("op_path", "ed_path"):
            p = cfg_get(key)
            if p and Path(p).exists():
                try:
                    from app.utils.ffmpeg import get_audio_duration
                    total += get_audio_duration(Path(p))
                except Exception:
                    pass
        m = int(total // 60)
        s = int(total % 60)
        if total_min := total < target_min:
            color = "text-orange-500"
            note = f"(目安 {target_min // 60}分 未満)"
        elif total > target_max:
            color = "text-red-500"
            note = f"(目安 {target_max // 60}分 超過)"
        else:
            color = "text-green-600"
            note = "(目安範囲内)"
        total_label.classes(replace=f"text-lg font-semibold {color}")
        total_label.text = f"推定総尺: {m}分 {s:02d}秒 {note}"

    update_total()

    # ── Selection list ─────────────────────────────────────────────────────
    ui.label("選択 (順序は ↑↓ ボタンで変更)").classes("text-md font-bold mt-4")

    list_container = ui.column().classes("w-full gap-1")

    def render_list():
        list_container.clear()
        with list_container:
            for pos, sid in enumerate(state["order"]):
                story = story_map.get(sid)
                if not story:
                    continue
                dur_sec = durations.get(sid, 0.0)
                with ui.row().classes("items-center gap-2 bg-gray-100 p-2 rounded w-full"):
                    ui.label(f"{pos + 1}.").classes("font-mono w-8")
                    ui.label(story.title).classes("flex-1")
                    ui.label(f"{int(dur_sec / 60)}分{int(dur_sec % 60):02d}秒").classes(
                        "text-xs text-gray-500 w-20",
                    )

                    def make_move(idx, delta):
                        def _move():
                            new = idx + delta
                            if 0 <= new < len(state["order"]):
                                state["order"][idx], state["order"][new] = (
                                    state["order"][new], state["order"][idx]
                                )
                                render_list()
                                update_total()
                        return _move

                    ui.button("↑", on_click=make_move(pos, -1)).props("size=sm flat").classes(
                        "text-blue-500" if pos > 0 else "text-gray-300",
                    )
                    ui.button("↓", on_click=make_move(pos, 1)).props("size=sm flat").classes(
                        "text-blue-500" if pos < len(state["order"]) - 1 else "text-gray-300",
                    )

                    def make_remove(s_id):
                        def _remove():
                            state["order"].remove(s_id)
                            render_list()
                            update_total()
                            update_select_options()
                        return _remove

                    ui.button("削除", on_click=make_remove(sid), color="red").props("size=sm flat")

    # ── Add-story selector ─────────────────────────────────────────────────
    def selector_options():
        return {
            s.id: f"{s.title} ({int(durations.get(s.id, 0.0) / 60)}分)"
            for s in stories if s.id not in state["order"]
        }

    with ui.row().classes("items-end gap-2 mt-4"):
        select_widget = ui.select(selector_options(), label="ストーリーを追加").classes("w-96")

        def add_selected():
            v = select_widget.value
            if v and v not in state["order"]:
                state["order"].append(v)
                render_list()
                update_total()
                update_select_options()

        ui.button("追加", on_click=add_selected, color="primary")

        def update_select_options():
            select_widget.options = selector_options()
            select_widget.value = None
            select_widget.update()

    render_list()

    # ── Bundle name & jingle override ──────────────────────────────────────
    ui.separator().classes("my-4")
    bundle_name = ui.input(
        "詰め合わせ動画名",
        value="",
        placeholder="例: 怪談まとめ_1",
    ).classes("w-96")

    default_jingle = cfg_get("bundle_jingle_path") or ""
    ui.label(
        f"使用ジングル: {Path(default_jingle).name if default_jingle else '無音0.5秒 (フォールバック)'} "
        "  (変更は設定画面から)",
    ).classes("text-xs text-gray-500 mt-2")

    # ── Generate button + progress ─────────────────────────────────────────
    progress = ui.linear_progress(value=0, show_value=False).classes("w-full mt-4").props("rounded")
    progress.visible = False
    status_label = ui.label("").classes("text-sm")
    result_link_container = ui.column().classes("w-full")

    work = {
        "running": False, "progress": 0.0, "progress_text": "",
        "done": False, "error": None, "result": None,
    }

    def progress_cb(current, total):
        work["progress"] = current / total if total > 0 else 0
        work["progress_text"] = f"処理中... ({current}/{total})"

    def do_generate():
        if not state["order"]:
            ui.notify("ストーリーを1件以上選択してください", color="warning")
            return
        name = (bundle_name.value or "").strip()
        if not name:
            ui.notify("詰め合わせ動画名を入力してください", color="warning")
            return

        work.update(running=True, done=False, error=None, progress=0.0, result=None)
        progress.visible = True
        status_label.text = "開始..."
        status_label.classes(replace="text-sm text-blue-500")
        gen_btn.disable()
        result_link_container.clear()

        ordered_stories = [story_map[sid] for sid in state["order"]]
        op_p = Path(cfg_get("op_path")) if cfg_get("op_path") else None
        ed_p = Path(cfg_get("ed_path")) if cfg_get("ed_path") else None
        jp = Path(cfg_get("bundle_jingle_path")) if cfg_get("bundle_jingle_path") else None

        def run():
            try:
                from app.services.bundle_generator import build_bundle
                work["result"] = build_bundle(
                    stories=ordered_stories,
                    bundle_name=name,
                    op_path=op_p,
                    ed_path=ed_p,
                    jingle_path=jp,
                    progress_callback=progress_cb,
                )
            except Exception as e:
                log.exception("[bundle] 生成失敗")
                work["error"] = str(e)
            work["done"] = True

        threading.Thread(target=run, daemon=True).start()

    def poll():
        if not work["running"]:
            timer.active = False
            return
        try:
            progress.value = work["progress"]
            if work["progress_text"]:
                status_label.text = work["progress_text"]
            if work["done"]:
                work["running"] = False
                err = work["error"]
                gen_btn.enable()
                if err:
                    progress.value = 0
                    status_label.text = f"エラー: {err[:200]}"
                    status_label.classes(replace="text-sm text-red-500")
                else:
                    progress.value = 1.0
                    out: Path = work["result"]
                    status_label.text = f"完了: {out.name}"
                    status_label.classes(replace="text-sm text-green-600")
                    with result_link_container:
                        ui.label(f"出力: {out}").classes("text-sm font-mono")
        except (RuntimeError, AttributeError):
            timer.active = False

    timer = ui.timer(0.5, poll, active=False)

    gen_btn = ui.button(
        "詰め合わせ動画を生成",
        on_click=lambda: (timer.activate(), do_generate()),
        color="primary",
    ).classes("mt-4")
