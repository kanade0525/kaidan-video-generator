from __future__ import annotations

from pathlib import Path

from nicegui import app, ui

from app import database as db
from app.models import STAGE_LABELS, STAGES, next_stage
from app.pipeline.executor import pipeline
from app.utils.paths import (
    audio_dir,
    chunks_path,
    images_dir,
    narration_path,
    processed_text_path,
    raw_content_path,
    story_dir,
    video_path,
)


def results_page():
    """Results viewer page."""
    ui.label("生成結果").classes("text-2xl font-bold mb-4")

    # Filters
    with ui.row().classes("gap-2 mb-4 items-end"):
        stage_filter = ui.select(
            {"": "全て", **{s: STAGE_LABELS.get(s, s) for s in STAGES[1:]}},
            value="",
            label="ステージ",
        ).classes("w-48")

        search_input = ui.input("タイトル検索").classes("w-64")
        ui.button("検索", on_click=lambda: update_story_list()).props("size=sm")

    select_container = ui.column().classes("w-full mb-4")
    detail_container = ui.column().classes("w-full")

    # Keep reference to current select widget
    state = {"select": None}

    def update_story_list():
        stage = stage_filter.value or None
        keyword = search_input.value.strip() if search_input.value else None
        stories = db.get_stories(stage=stage, keyword=keyword, limit=200)

        select_container.clear()
        detail_container.clear()

        with select_container:
            if not stories:
                ui.label("該当なし").classes("text-gray-500")
                return
            options = {s.id: f"{s.title} [{STAGE_LABELS.get(s.stage, s.stage)}]" for s in stories}
            sel = ui.select(options, label="ストーリー選択").classes("w-96")
            sel.on_value_change(lambda e: show_detail(e.value))
            state["select"] = sel

    stage_filter.on_value_change(lambda _: update_story_list())
    search_input.on("keydown.enter", lambda _: update_story_list())

    selected = None
    update_story_list()

    def show_detail(story_id):
        if not story_id:
            return

        story = db.get_story_by_id(story_id)
        if not story:
            return

        detail_container.clear()
        with detail_container:
            # Progress indicator
            ui.label(f"「{story.title}」").classes("text-xl font-bold mb-2")

            with ui.row().classes("gap-1 mb-4"):
                stage_idx = STAGES.index(story.stage) if story.stage in STAGES else 0
                for i, s in enumerate(STAGES):
                    color = "green" if i <= stage_idx else "gray"
                    label = STAGE_LABELS.get(s, s)
                    ui.badge(label, color=color).classes("text-xs")

            if story.error:
                ui.label(f"エラー: {story.error}").classes("text-red-500 mb-2")

            # Tabs for each stage output
            with ui.tabs().classes("w-full") as tabs:
                scrape_tab = ui.tab("スクレイピング")
                text_tab = ui.tab("テキスト")
                voice_tab = ui.tab("音声")
                images_tab = ui.tab("画像")
                video_tab = ui.tab("動画")

            with ui.tab_panels(tabs, value=scrape_tab).classes("w-full"):
                with ui.tab_panel(scrape_tab):
                    _show_scrape_result(story)

                with ui.tab_panel(text_tab):
                    _show_text_result(story)

                with ui.tab_panel(voice_tab):
                    _show_voice_result(story)

                with ui.tab_panel(images_tab):
                    _show_images_result(story)

                with ui.tab_panel(video_tab):
                    _show_video_result(story)

    # (event binding is done inside update_story_list)


def _retry_button(story, target_stage: str, label: str = "再処理"):
    """Add a retry button with progress bar that runs in a background thread."""
    import threading

    progress = ui.linear_progress(value=0, show_value=False).classes("w-full").props("rounded")
    progress.visible = False
    status_label = ui.label("").classes("text-sm text-gray-500")

    def do_retry():
        status_label.text = "処理中..."
        status_label.classes(replace="text-sm text-blue-500")
        progress.visible = True
        progress.value = 0
        btn.disable()

        def progress_callback(current, total):
            try:
                progress.value = current / total if total > 0 else 0
                status_label.text = f"処理中... ({current}/{total})"
            except Exception:
                pass

        def run():
            try:
                pipeline.run_single(story.id, target_stage, progress_callback=progress_callback)
                error = None
            except Exception as e:
                error = str(e)

            try:
                progress.value = 1.0 if not error else 0
                if error:
                    status_label.text = f"エラー: {error[:100]}"
                    status_label.classes(replace="text-sm text-red-500")
                else:
                    status_label.text = "完了! (ページを再読み込みで結果を確認)"
                    status_label.classes(replace="text-sm text-green-500")
                btn.enable()
            except Exception:
                pass

        threading.Thread(target=run, daemon=True).start()

    btn = ui.button(label, on_click=do_retry, color="orange").props("size=sm")


def _show_scrape_result(story):
    raw_path = raw_content_path(story.title)
    if raw_path.exists():
        text = raw_path.read_text(encoding="utf-8")
        ui.label(f"文字数: {len(text)}").classes("text-sm text-gray-500")
        ui.textarea(value=text).classes("w-full").props("readonly rows=10")
    else:
        ui.label("未取得").classes("text-gray-500")

    _retry_button(story, "scraped", "再スクレイピング")


def _show_text_result(story):
    proc_path = processed_text_path(story.title)
    if proc_path.exists():
        text = proc_path.read_text(encoding="utf-8")
        ui.label(f"文字数: {len(text)}").classes("text-sm text-gray-500")
        ui.textarea(value=text).classes("w-full").props("readonly rows=10")

        chunk_file = chunks_path(story.title)
        if chunk_file.exists():
            import json
            chunks = json.loads(chunk_file.read_text(encoding="utf-8"))
            ui.label(f"チャンク数: {len(chunks)}").classes("text-sm text-gray-500 mt-2")
    else:
        ui.label("未処理").classes("text-gray-500")

    _retry_button(story, "text_processed", "テキスト再処理")


def _show_voice_result(story):
    narr_path = narration_path(story.title)
    if narr_path.exists():
        # Serve audio file with cache-busting timestamp
        import time
        ts = int(narr_path.stat().st_mtime)
        static_path = f"/audio/{story.id}"
        app.add_static_files(static_path, str(narr_path.parent))
        ui.audio(f"{static_path}/{narr_path.name}?t={ts}").classes("w-full")

        # Individual chunks
        a_dir = audio_dir(story.title)
        chunk_files = sorted(a_dir.glob("*.wav"))
        if chunk_files:
            ui.label(f"チャンク音声: {len(chunk_files)}件").classes("text-sm text-gray-500 mt-2")
    else:
        ui.label("未生成").classes("text-gray-500")

    _retry_button(story, "voice_generated", "音声再生成")


def _show_images_result(story):
    img_dir = images_dir(story.title)
    images = sorted(img_dir.glob("*.png"))

    if images:
        import time
        static_path = f"/images/{story.id}"
        app.add_static_files(static_path, str(img_dir))

        with ui.row().classes("gap-2 flex-wrap"):
            for img in images:
                ts = int(img.stat().st_mtime)
                ui.image(f"{static_path}/{img.name}?t={ts}").classes("w-64 rounded")
    else:
        ui.label("未生成").classes("text-gray-500")

    _retry_button(story, "images_generated", "画像再生成")


def _show_video_result(story):
    vid_path = video_path(story.title)
    if vid_path.exists():
        import time
        ts = int(vid_path.stat().st_mtime)
        static_path = f"/video/{story.id}"
        app.add_static_files(static_path, str(vid_path.parent))
        ui.video(f"{static_path}/{vid_path.name}?t={ts}").classes("w-full max-w-2xl")
    else:
        ui.label("未生成").classes("text-gray-500")

    _retry_button(story, "video_complete", "動画再生成")
