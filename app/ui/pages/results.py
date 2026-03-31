from __future__ import annotations

from pathlib import Path

from nicegui import app, ui

from app import database as db
from app.utils.log import get_logger

log = get_logger("kaidan.ui.results")
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
        char_label = ui.label(f"文字数: {len(text)}").classes("text-sm text-gray-500")
        textarea = ui.textarea(value=text).classes("w-full").props("rows=10")

        def save_raw():
            new_text = textarea.value
            raw_path.write_text(new_text, encoding="utf-8")
            char_label.text = f"文字数: {len(new_text)}"
            ui.notify("スクレイピングテキストを保存しました", color="positive")

        ui.button("テキストを保存", on_click=save_raw, color="green").props("size=sm")
    else:
        ui.label("未取得").classes("text-gray-500")

    _retry_button(story, "scraped", "再スクレイピング")


def _show_text_result(story):
    proc_path = processed_text_path(story.title)
    if proc_path.exists():
        text = proc_path.read_text(encoding="utf-8")
        char_label = ui.label(f"文字数: {len(text)}").classes("text-sm text-gray-500")
        edited = {"text": text}
        textarea = ui.textarea(value=text, on_change=lambda e: edited.update(text=e.value)).classes("w-full").props("rows=10")

        chunk_file = chunks_path(story.title)
        if chunk_file.exists():
            import json
            chunks = json.loads(chunk_file.read_text(encoding="utf-8"))
            ui.label(f"チャンク数: {len(chunks)}").classes("text-sm text-gray-500 mt-2")

        def save_processed():
            import json as _json
            from app.services.text_processor import split_into_chunks
            new_text = edited["text"]
            log.info("テキスト保存: %d文字, 先頭50文字: %s", len(new_text), new_text[:50])
            proc_path.write_text(new_text, encoding="utf-8")
            # Re-split into chunks using the proper splitter
            new_chunks = split_into_chunks(new_text)
            chunk_file = chunks_path(story.title)
            chunk_file.write_text(_json.dumps(new_chunks, ensure_ascii=False, indent=2))
            log.info("チャンク保存: %d チャンク", len(new_chunks))
            char_label.text = f"文字数: {len(new_text)}"
            ui.notify(f"処理済みテキストを保存（{len(new_chunks)}チャンク）", color="positive")

        ui.button("テキストを保存", on_click=save_processed, color="green").props("size=sm")
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
    import json as _json

    img_dir = images_dir(story.title)
    images = sorted(img_dir.glob("*.png"))

    if images:
        static_path = f"/images/{story.id}"
        app.add_static_files(static_path, str(img_dir))

        # Load or create slideshow config
        config_path = story_dir(story.title) / "slideshow.json"
        if config_path.exists():
            slide_config = _json.loads(config_path.read_text())
        else:
            slide_config = [{"file": img.name, "duration": 0} for img in images]
            # duration 0 = auto (total / num_images)

        ui.label("スライドショー編集").classes("text-lg font-bold mt-2 mb-2")
        ui.label("表示時間0 = 自動（均等分割）。ドラッグで並び替えはできないため、順番は番号で指定してください。").classes("text-xs text-gray-500 mb-2")

        slides_container = ui.column().classes("w-full")

        def render_slides():
            nonlocal slide_config
            slides_container.clear()
            slide_inputs.clear()
            with slides_container:
                for i, slide in enumerate(slide_config):
                    img_file = slide["file"]
                    img_path = img_dir / img_file
                    if not img_path.exists():
                        continue
                    ts = int(img_path.stat().st_mtime)

                    with ui.row().classes("items-center gap-2 mb-2 w-full"):
                        ui.label(f"{i + 1}.").classes("text-sm w-6")
                        ui.image(f"{static_path}/{img_file}?t={ts}").classes("w-32 h-20 rounded object-cover")
                        ui.label(img_file).classes("text-xs text-gray-500 w-32")
                        dur_input = ui.number(
                            "秒", value=slide.get("duration", 0),
                            min=0, max=60, step=0.5, format="%.1f"
                        ).classes("w-20").props("dense size=sm")
                        order_input = ui.number(
                            "順番", value=i + 1,
                            min=1, max=len(slide_config), step=1
                        ).classes("w-16").props("dense size=sm")

                        def make_delete(f=img_file):
                            def delete_img():
                                nonlocal slide_config
                                (img_dir / f).unlink(missing_ok=True)
                                slide_config = [s for s in slide_config if s["file"] != f]
                                ui.notify(f"{f} を削除しました", color="warning")
                                render_slides()
                            return delete_img

                        ui.button(icon="delete", on_click=make_delete(), color="red").props("flat size=sm")
                        slide_inputs.append({"file": img_file, "duration": dur_input, "order": order_input})

        slide_inputs = []
        render_slides()

        def save_slideshow():
            # Sort by order
            sorted_slides = sorted(slide_inputs, key=lambda s: s["order"].value)
            config = [{"file": s["file"], "duration": s["duration"].value} for s in sorted_slides]
            config_path.write_text(_json.dumps(config, ensure_ascii=False, indent=2))
            ui.notify("スライドショー設定を保存しました", color="positive")

        ui.button("スライドショー設定を保存", on_click=save_slideshow, color="blue").props("size=sm")

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

    # YouTube upload section
    if vid_path.exists():
        ui.separator().classes("my-4")
        _show_youtube_upload(story)


def _show_youtube_upload(story):
    """YouTube upload UI with approval flow and duplicate prevention."""
    from app.config import get as cfg_get
    from app.services import youtube_uploader

    ui.label("YouTubeアップロード").classes("text-lg font-bold mt-2")

    # Check if already uploaded
    fresh_story = db.get_story_by_id(story.id)
    if fresh_story and fresh_story.youtube_video_id:
        vid_id = fresh_story.youtube_video_id
        with ui.row().classes("items-center gap-2"):
            ui.label("アップロード済み").classes("text-green-500 font-bold")
            ui.link(
                f"https://youtube.com/watch?v={vid_id}",
                f"https://youtube.com/watch?v={vid_id}",
                new_tab=True,
            ).classes("text-blue-500 underline")

        with ui.row().classes("gap-2 mt-2"):
            ui.label("再アップロードしますか？").classes("text-sm text-gray-500")
            reupload_check = ui.checkbox("はい、再アップロードする")
        show_form = reupload_check
    else:
        show_form = None

    if not youtube_uploader.is_authenticated():
        ui.label("YouTube未認証。設定ページから認証してください。").classes("text-red-500")
        return

    # Upload form
    with ui.card().classes("w-full p-4 mt-2"):
        title_template = cfg_get("youtube_title_template")
        yt_title = ui.input("タイトル", value=title_template.format(title=story.title)).classes("w-full")
        description_template = cfg_get("youtube_description_template")
        from app.services.voice_generator import get_speaker_name
        speaker_name = get_speaker_name()
        yt_desc = ui.textarea(
            "説明", value=description_template.format(title=story.title, url=story.url, speaker=speaker_name)
        ).classes("w-full")
        tags_str = cfg_get("youtube_tags")
        yt_tags = ui.input("タグ（カンマ区切り）", value=tags_str).classes("w-full")

        with ui.row().classes("gap-4"):
            yt_privacy = ui.select(
                {"private": "非公開", "unlisted": "限定公開", "public": "公開"},
                value=cfg_get("youtube_privacy_status"),
                label="公開状態",
            ).classes("w-48")
            yt_category = ui.select(
                {"24": "エンターテインメント", "22": "ブログ", "27": "教育"},
                value=cfg_get("youtube_category_id"),
                label="カテゴリ",
            ).classes("w-48")

        # Schedule
        schedule_enabled = cfg_get("youtube_schedule_enabled")
        schedule_day = cfg_get("youtube_schedule_day")
        schedule_hour = cfg_get("youtube_schedule_hour")
        schedule_minute = cfg_get("youtube_schedule_minute")
        next_publish = None
        if schedule_enabled:
            next_publish = youtube_uploader.get_next_publish_time(
                schedule_day, schedule_hour, schedule_minute
            )

        yt_schedule = ui.checkbox(
            f"予約投稿（次回: {next_publish[:16].replace('T', ' ')} JST）" if next_publish else "予約投稿",
            value=schedule_enabled,
        )

        progress = ui.linear_progress(value=0, show_value=False).classes("w-full mt-2")
        progress.visible = False
        status_label = ui.label("").classes("text-sm")

        def do_upload():
            # Duplicate check
            if show_form is not None and not show_form.value:
                ui.notify("再アップロードを確認してください", color="warning")
                return

            import threading

            btn.disable()
            progress.visible = True
            progress.value = 0
            status_label.text = "アップロード中..."

            def run():
                try:
                    tags = [t.strip() for t in yt_tags.value.split(",") if t.strip()]
                    publish_at = None
                    if yt_schedule.value:
                        publish_at = youtube_uploader.get_next_publish_time(
                            cfg_get("youtube_schedule_day"),
                            cfg_get("youtube_schedule_hour"),
                            cfg_get("youtube_schedule_minute"),
                        )
                    result = youtube_uploader.upload_video(
                        video_path=video_path(story.title),
                        title=yt_title.value,
                        description=yt_desc.value,
                        tags=tags,
                        category_id=yt_category.value,
                        privacy_status=yt_privacy.value,
                        publish_at=publish_at,
                        progress_callback=lambda cur, total: setattr(progress, 'value', cur / total),
                    )
                    db.set_youtube_video_id(story.id, result["video_id"])
                    db.update_stage(story.id, "youtube_uploaded")

                    # Submit usage report to HHS Library
                    channel_name = cfg_get("youtube_channel_name")
                    contact_email = cfg_get("youtube_contact_email")
                    if channel_name and contact_email:
                        try:
                            youtube_uploader.submit_usage_report(
                                story_title=story.title,
                                video_url=result["url"],
                                channel_name=channel_name,
                                email=contact_email,
                            )
                        except Exception as e:
                            log.warning("使用報告送信失敗（アップロードは成功）: %s", e)

                    try:
                        progress.value = 1.0
                        msg = f"完了! {result['url']}"
                        if result.get("publish_at"):
                            msg += f"\n予約公開: {result['publish_at'][:16].replace('T', ' ')} JST"
                        status_label.text = msg
                        status_label.classes(replace="text-sm text-green-500")
                    except Exception:
                        pass
                except Exception as e:
                    try:
                        status_label.text = f"エラー: {e}"
                        status_label.classes(replace="text-sm text-red-500")
                    except Exception:
                        pass
                finally:
                    try:
                        btn.enable()
                    except Exception:
                        pass

            threading.Thread(target=run, daemon=True).start()

        btn = ui.button(
            "承認してYouTubeにアップロード", on_click=do_upload, color="red"
        ).props("size=sm").classes("mt-2")
