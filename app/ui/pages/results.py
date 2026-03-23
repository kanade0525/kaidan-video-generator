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

    # Story selector
    completed_stages = STAGES[1:]  # Everything except pending
    stories = db.get_stories(limit=100)

    if not stories:
        ui.label("ストーリーなし").classes("text-gray-500")
        return

    story_options = {s.id: f"{s.title} [{STAGE_LABELS.get(s.stage, s.stage)}]" for s in stories}
    selected = ui.select(story_options, label="ストーリー選択").classes("w-96 mb-4")

    detail_container = ui.column().classes("w-full")

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

    selected.on_value_change(lambda e: show_detail(e.value))

    # Auto-select from query param
    query_id = app.storage.general.get("selected_story_id")
    if query_id:
        selected.value = int(query_id)


def _retry_button(story: db, target_stage: str, label: str = "再処理"):
    """Add a retry button for a specific stage."""
    def do_retry():
        try:
            ns = target_stage
            pipeline.run_single(story.id, ns)
            ui.notify(f"{STAGE_LABELS.get(ns, ns)} 完了", color="positive")
        except Exception as e:
            ui.notify(f"エラー: {e}", color="negative")

    ui.button(label, on_click=do_retry, color="orange").props("size=sm")


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
        # Serve audio file
        app.add_static_files("/audio", str(narr_path.parent))
        ui.audio(f"/audio/{narr_path.name}").classes("w-full")

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
        # Serve image files
        static_path = f"/images/{story.id}"
        app.add_static_files(static_path, str(img_dir))

        with ui.row().classes("gap-2 flex-wrap"):
            for img in images:
                ui.image(f"{static_path}/{img.name}").classes("w-64 rounded")
    else:
        ui.label("未生成").classes("text-gray-500")

    _retry_button(story, "images_generated", "画像再生成")


def _show_video_result(story):
    vid_path = video_path(story.title)
    if vid_path.exists():
        static_path = f"/video/{story.id}"
        app.add_static_files(static_path, str(vid_path.parent))
        ui.video(f"{static_path}/{vid_path.name}").classes("w-full max-w-2xl")
    else:
        ui.label("未生成").classes("text-gray-500")

    _retry_button(story, "video_complete", "動画再生成")
