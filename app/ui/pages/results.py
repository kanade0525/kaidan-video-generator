from __future__ import annotations

from nicegui import app, ui

from app import database as db
from app.utils.log import get_logger

log = get_logger("kaidan.ui.results")
from app.models import STAGE_LABELS, STAGES, stages_for
from app.pipeline.executor import pipeline, shorts_pipeline
from app.ui.category_colors import category_color
from app.utils.shorts_duration import estimate_shorts_total_duration
from app.utils.paths import (
    audio_dir,
    chunks_path,
    images_dir,
    narration_path,
    processed_text_path,
    raw_content_path,
    story_dir,
    timestamps_path,
    video_path,
)


def results_page(
    keyword: str = "", story_id: int = 0, content_type: str | None = None,
    base_path: str = "/results", category: str = "",
):
    """Results viewer page."""
    ui.label("生成結果").classes("text-2xl font-bold mb-4")

    # Long 側のみカテゴリフィルタ（Short は kikikaikai 由来でカテゴリ未使用）
    show_category_filter = content_type != "short"
    category_options: dict[str, str] = {}
    if show_category_filter:
        try:
            cats = db.get_categories()
            category_options = {"": "全て", **{c: c for c in cats}}
        except Exception:
            category_options = {"": "全て"}

    # Filters (stage resets on reload so updated stories always appear)
    with ui.row().classes("gap-2 mb-4 items-end"):
        stage_filter = ui.select(
            {"": "全て", **{s: STAGE_LABELS.get(s, s) for s in STAGES[1:]}},
            value="",
            label="ステージ",
        ).classes("w-48")

        category_filter = None
        if show_category_filter:
            category_filter = ui.select(
                category_options,
                value=category if category in category_options else "",
                label="カテゴリ",
            ).classes("w-48")

        search_input = ui.input("タイトル検索", value=keyword).classes("w-64")
        ui.button("検索", on_click=lambda: update_story_list()).props("size=sm")

    select_container = ui.column().classes("w-full mb-4")
    detail_container = ui.column().classes("w-full")

    # Keep reference to current select widget
    state = {"select": None}

    def _update_url(selected_id=None):
        """Update URL query params to preserve search state."""
        from app.ui.url_state import build_results_url
        cat = category_filter.value if category_filter else ""
        url = build_results_url(
            keyword=search_input.value or "",
            story_id=selected_id,
            base_path=base_path,
            category=cat or "",
        )
        ui.run_javascript(f'window.history.replaceState(null, "", "{url}")')

    def show_detail(sid):
        if not sid:
            return

        story = db.get_story_by_id(sid)
        if not story:
            return

        _update_url(selected_id=sid)

        detail_container.clear()
        with detail_container:
            # Progress indicator
            ui.label(f"「{story.title}」").classes("text-xl font-bold mb-2")

            if story.categories:
                with ui.row().classes("gap-1 mb-3 items-center"):
                    ui.label("カテゴリ:").classes("text-sm text-gray-500")
                    for c in story.categories:
                        ui.badge(c, color=category_color(c)).classes("text-xs")

            if story.content_type == "short":
                _render_shorts_duration_badge(story)
            else:
                _render_shorts_duration_badge(story, label_prefix="Shorts移送判定: ")

            with ui.row().classes("gap-1 mb-4"):
                story_stages = stages_for(story.content_type)
                stage_idx = story_stages.index(story.stage) if story.stage in story_stages else 0
                for i, s in enumerate(story_stages):
                    if i < stage_idx:
                        color = "green"
                    elif i == stage_idx:
                        color = "blue"
                    else:
                        color = "gray"
                    label = STAGE_LABELS.get(s, s)
                    ui.badge(label, color=color).classes("text-xs")

            if story.content_type == "long":
                _render_convert_to_short_button(story, on_done=lambda: show_detail(sid))
            else:
                _render_convert_to_long_button(story, on_done=lambda: show_detail(sid))

            if story.error:
                ui.label(f"エラー: {story.error}").classes("text-red-500 mb-2")

            # Tabs for each stage output
            with ui.tabs().classes("w-full") as tabs:
                scrape_tab = ui.tab("スクレイピング")
                text_tab = ui.tab("テキスト")
                voice_tab = ui.tab("音声")
                images_tab = ui.tab("画像")
                video_tab = ui.tab("動画")
                youtube_tab = ui.tab("YouTube")
                # HHS使用報告 is required whenever story is HHS-sourced
                # (includes long-form and migrated shorts)
                report_tab = ui.tab("HHS使用報告") if story.source == "hhs" else None

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

                with ui.tab_panel(youtube_tab):
                    _show_youtube_upload_tab(story)

                if report_tab:
                    with ui.tab_panel(report_tab):
                        _show_usage_report_tab(story)

    def update_story_list():
        s = stage_filter.value or None
        kw = search_input.value.strip() if search_input.value else None
        cat = (category_filter.value if category_filter else None) or None
        # Default order: most recently processed first (updated_at DESC)
        stories = db.get_stories(
            stage=s, keyword=kw, category=cat, limit=200, content_type=content_type,
        )

        select_container.clear()
        detail_container.clear()

        with select_container:
            if not stories:
                _update_url()
                ui.label("該当なし").classes("text-gray-500")
                return
            from app.ui.url_state import resolve_initial_story
            options = {s.id: f"{s.title} [{STAGE_LABELS.get(s.stage, s.stage)}]" for s in stories}
            initial = resolve_initial_story(story_id, options)
            sel = ui.select(options, label="ストーリー選択", value=initial).classes("w-96")
            sel.on_value_change(lambda e: show_detail(e.value))
            state["select"] = sel
            if initial:
                show_detail(initial)
            else:
                _update_url()

    stage_filter.on_value_change(lambda _: update_story_list())
    if category_filter:
        category_filter.on_value_change(lambda _: update_story_list())
    search_input.on("keydown.enter", lambda _: update_story_list())

    update_story_list()


def _retry_button(story, target_stage: str, label: str = "再処理"):
    """Add a retry button with progress bar that runs in a background thread."""
    import threading

    progress = ui.linear_progress(value=0, show_value=False).classes("w-full").props("rounded")
    progress.visible = False
    status_label = ui.label("").classes("text-sm text-gray-500")

    # Thread-safe state shared between worker thread and UI timer
    state = {"running": False, "progress": 0.0, "progress_text": "", "done": False, "error": None}

    def do_retry():
        state.update(running=True, done=False, error=None, progress=0.0, progress_text="")
        status_label.text = "処理中..."
        status_label.classes(replace="text-sm text-blue-500")
        progress.visible = True
        progress.value = 0
        btn.disable()

        def progress_callback(current, total):
            state["progress"] = current / total if total > 0 else 0
            state["progress_text"] = f"処理中... ({current}/{total})"

        def run():
            try:
                p = shorts_pipeline if story.content_type == "short" else pipeline
                p.run_single(story.id, target_stage, progress_callback=progress_callback)
            except Exception as e:
                state["error"] = str(e)
            state["done"] = True

        threading.Thread(target=run, daemon=True).start()

    def poll():
        """Timer callback to safely update UI from main thread."""
        if not state["running"]:
            return
        try:
            progress.value = state["progress"]
            if state["progress_text"]:
                status_label.text = state["progress_text"]
            if state["done"]:
                state["running"] = False
                error = state["error"]
                if error:
                    progress.value = 0
                    status_label.text = f"エラー: {error[:100]}"
                    status_label.classes(replace="text-sm text-red-500")
                else:
                    progress.value = 1.0
                    status_label.text = "完了! (ページを再読み込みで結果を確認)"
                    status_label.classes(replace="text-sm text-green-500")
                btn.enable()
        except RuntimeError:
            state["running"] = False

    ui.timer(0.5, poll)

    btn = ui.button(label, on_click=do_retry, color="orange").props("size=sm")


_DURATION_BADGE_COLORS = {
    "ok": "green-7",
    "warning": "amber-8",
    "over": "red-7",
}

_DURATION_BADGE_LABELS = {
    "ok": "尺OK",
    "warning": "尺注意",
    "over": "180秒超過",
}


def _render_shorts_duration_badge(story, label_prefix: str = "尺: "):
    """Show Shorts duration badge (180s limit) in the story detail header."""
    est = estimate_shorts_total_duration(story)
    if est.seconds is None:
        return
    cls = est.classification
    if cls == "unknown":
        return
    base = _DURATION_BADGE_LABELS.get(cls, "")
    color = _DURATION_BADGE_COLORS.get(cls, "grey")
    with ui.row().classes("gap-1 mb-3 items-center"):
        ui.label(label_prefix).classes("text-sm text-gray-500")
        ui.badge(f"{base} ({est.seconds:.0f}s)", color=color).classes("text-xs")
        if not est.actual:
            ui.label("（音声からの予測）").classes("text-xs text-gray-400")


def _render_convert_to_short_button(story, on_done):
    """Button to migrate a long-form story to the Shorts pipeline.

    Enabled only when audio duration estimate is ≤180s (ok/warning).
    HHS-sourced stories carry the `source="hhs"` field into the Shorts
    pipeline so the correct 引用元 template is used and HHS使用報告 is
    still required after the Shorts upload.
    """
    est = estimate_shorts_total_duration(story)
    cls = est.classification
    disabled = cls in ("over", "unknown")
    tooltip = None
    if cls == "over":
        tooltip = f"180秒超過のため Shorts 化不可 ({est.seconds:.0f}s)"
    elif cls == "unknown":
        tooltip = "ナレーション未生成のため判定不可"
    elif cls == "warning":
        tooltip = f"尺が180秒ギリギリ ({est.seconds:.0f}s). 移送後に再確認推奨"

    def do_migrate():
        def confirm():
            dlg.close()
            try:
                db.convert_to_short(story.id)
                msg = "Shortsパイプラインに移送しました"
                if story.source == "hhs":
                    msg += "（HHS使用報告が別途必要です）"
                ui.notify(msg, color="positive")
                on_done()
            except Exception as e:
                log.exception("convert_to_short failed")
                ui.notify(f"移送失敗: {e}", color="negative")

        with ui.dialog() as dlg, ui.card():
            ui.label(f"「{story.title}」をShortsに移送しますか？").classes("text-lg font-bold")
            ui.label(
                "・content_type を short に切替、stage を テキスト処理済み に巻き戻し\n"
                "・スクレイピング/処理済テキストは流用（再処理なし）\n"
                "・音声/画像/動画/YouTube を Shorts 設定で再生成\n"
                "  （Shorts は speed=1.15 で long の 0.9 と異なるため音声は作り直し必須）\n"
                "・long 側の成果物は残置（手動削除可）"
            ).classes("text-sm whitespace-pre-line")
            if story.source == "hhs":
                ui.label(
                    "※ HHS由来: 移送後の Shorts に対しても別途「HHS使用報告」が必要です "
                    "(規約準拠)。"
                ).classes("text-sm text-orange-600")
            with ui.row().classes("gap-2 mt-3 justify-end"):
                ui.button("キャンセル", on_click=dlg.close).props("flat")
                ui.button("移送実行", on_click=confirm, color="primary")
        dlg.open()

    with ui.row().classes("gap-2 mb-3 items-center"):
        btn = ui.button(
            "Shortsへ移送",
            on_click=do_migrate,
            color="orange" if cls == "warning" else "primary",
        ).props("size=sm icon=content_copy")
        if disabled:
            btn.disable()
        if tooltip:
            btn.tooltip(tooltip)


def _render_convert_to_long_button(story, on_done):
    """Button to migrate a Shorts story to the long-form pipeline.

    No duration constraint (long has no upper limit). Audio must be
    regenerated because long speed (0.9) differs from shorts speed (1.15).
    HHS-sourced shorts (migrated long→short previously) require HHS使用報告
    again after the new long upload.
    """
    def do_migrate():
        def confirm():
            dlg.close()
            try:
                db.convert_to_long(story.id)
                msg = "長尺パイプラインに移送しました"
                if story.source == "hhs":
                    msg += "（HHS使用報告が別途必要です）"
                ui.notify(msg, color="positive")
                on_done()
            except Exception as e:
                log.exception("convert_to_long failed")
                ui.notify(f"移送失敗: {e}", color="negative")

        with ui.dialog() as dlg, ui.card():
            ui.label(f"「{story.title}」を長尺に移送しますか？").classes("text-lg font-bold")
            ui.label(
                "・content_type を long に切替、stage を テキスト処理済み に巻き戻し\n"
                "・スクレイピング/処理済テキストは流用（再処理なし）\n"
                "・音声/画像/動画/YouTube を 長尺設定で再生成\n"
                "  （long は speed=0.9 で Shorts の 1.15 と異なるため音声は作り直し必須）\n"
                "・short 側の成果物は残置（手動削除可）"
            ).classes("text-sm whitespace-pre-line")
            if story.source == "hhs":
                ui.label(
                    "※ HHS由来: 移送後の long 動画に対しても別途「HHS使用報告」が必要です "
                    "(規約準拠)。"
                ).classes("text-sm text-orange-600")
            elif story.source == "kikikaikai":
                ui.label(
                    "※ 奇々怪々由来: 長尺 description テンプレは source=kikikaikai 用に自動切替。"
                ).classes("text-sm text-blue-600")
            with ui.row().classes("gap-2 mt-3 justify-end"):
                ui.button("キャンセル", on_click=dlg.close).props("flat")
                ui.button("移送実行", on_click=confirm, color="primary")
        dlg.open()

    with ui.row().classes("gap-2 mb-3 items-center"):
        ui.button(
            "長尺へ移送",
            on_click=do_migrate,
            color="primary",
        ).props("size=sm icon=content_copy")


def _show_scrape_result(story):
    raw_path = raw_content_path(story.title, story.content_type)
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
    proc_path = processed_text_path(story.title, story.content_type)
    if proc_path.exists():
        text = proc_path.read_text(encoding="utf-8")
        char_label = ui.label(f"文字数: {len(text)}").classes("text-sm text-gray-500")
        edited = {"text": text}
        textarea = ui.textarea(value=text).classes("w-full").props("rows=10")
        textarea.on_value_change(lambda e: edited.update(text=e.value))

        chunk_file = chunks_path(story.title, story.content_type)
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
            chunk_file = chunks_path(story.title, story.content_type)
            chunk_file.write_text(_json.dumps(new_chunks, ensure_ascii=False, indent=2))
            log.info("チャンク保存: %d チャンク", len(new_chunks))
            char_label.text = f"文字数: {len(new_text)}"
            ui.notify(f"処理済みテキストを保存（{len(new_chunks)}チャンク）", color="positive")

        ui.button("テキストを保存", on_click=save_processed, color="green").props("size=sm")

        # Title furigana editing
        ui.separator().classes("my-4")
        ui.label("タイトルふりがな").classes("text-lg font-semibold")
        furigana_input = ui.input(value=story.title_furigana, placeholder="ひらがな/カタカナで入力").classes("w-full")

        def save_furigana():
            new_furigana = furigana_input.value
            from app.database import update_title_furigana
            update_title_furigana(story.id, new_furigana)
            ui.notify("タイトルふりがなを保存しました", color="positive")

        ui.button("ふりがなを保存", on_click=save_furigana, color="blue").props("size=sm")
    else:
        ui.label("未処理").classes("text-gray-500")

    _retry_button(story, "text_processed", "テキスト再処理")


def _show_voice_result(story):
    narr_path = narration_path(story.title, story.content_type)
    if narr_path.exists():
        # Serve audio file with cache-busting timestamp
        ts = int(narr_path.stat().st_mtime)
        static_path = f"/audio/{story.id}"
        app.add_static_files(static_path, str(narr_path.parent))
        ui.audio(f"{static_path}/{narr_path.name}?t={ts}").classes("w-full")

        # Individual chunks
        a_dir = audio_dir(story.title, story.content_type)
        chunk_files = sorted(a_dir.glob("*.wav"))
        if chunk_files:
            ui.label(f"チャンク音声: {len(chunk_files)}件").classes("text-sm text-gray-500 mt-2")
    else:
        ui.label("未生成").classes("text-gray-500")

    _retry_button(story, "voice_generated", "音声再生成")

    ui.separator().classes("my-4")
    _text_reference_panel(story)


def _show_images_result(story):
    import json as _json

    img_dir = images_dir(story.title, story.content_type)
    images = sorted(img_dir.glob("*.png"))

    if images:
        static_path = f"/images/{story.id}"
        app.add_static_files(static_path, str(img_dir))

        # Load or create slideshow config
        config_path = story_dir(story.title, story.content_type) / "slideshow.json"
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
    vid_path = video_path(story.title, story.content_type)
    if vid_path.exists():
        ts = int(vid_path.stat().st_mtime)
        static_path = f"/video/{story.id}"
        app.add_static_files(static_path, str(vid_path.parent))

        if story.content_type == "short":
            # iPhone mockup frame for shorts preview
            with ui.element("div").classes("flex justify-center"):
                with ui.element("div").style(
                    "width: 300px; height: 650px; "
                    "border-radius: 40px; "
                    "border: 6px solid #1a1a1a; "
                    "background: #000; "
                    "padding: 12px 4px; "
                    "box-shadow: 0 8px 32px rgba(0,0,0,0.3), "
                    "inset 0 0 0 2px #333; "
                    "position: relative; "
                    "overflow: hidden;"
                ):
                    # Notch
                    ui.element("div").style(
                        "width: 120px; height: 24px; "
                        "background: #1a1a1a; "
                        "border-radius: 0 0 16px 16px; "
                        "position: absolute; "
                        "top: 0; left: 50%; "
                        "transform: translateX(-50%); "
                        "z-index: 10;"
                    )
                    # Video inside phone — contain keeps full frame visible
                    # with black bars top/bottom (9:16 video in 9:19.5 phone)
                    ui.video(f"{static_path}/{vid_path.name}?t={ts}").props("controls").style(
                        "width: 100%; height: 100%; "
                        "object-fit: contain; "
                        "border-radius: 32px;"
                    )
        else:
            ui.video(f"{static_path}/{vid_path.name}?t={ts}").props("controls").classes("w-full max-w-2xl")
    else:
        ui.label("未生成").classes("text-gray-500")

    _retry_button(story, "video_complete", "動画再生成")

    ui.separator().classes("my-4")
    _text_reference_panel(story)


def _text_reference_panel(story):
    """Read-only side-by-side preview of raw and processed text.

    Placed below audio/video players so the user can cross-check scraped
    vs processed content while reviewing playback.
    """
    with ui.row().classes("items-center gap-3 mb-1"):
        ui.label("テキスト参照").classes("text-sm font-bold text-gray-500")
        if story.url:
            ui.label("元ソース:").classes("text-xs text-gray-500")
            ui.link(story.url, story.url, new_tab=True).classes(
                "text-xs text-blue-500 underline break-all"
            )
    with ui.row().classes("w-full gap-4 items-start no-wrap"):
        with ui.column().classes("flex-1 min-w-0"):
            raw_path = raw_content_path(story.title, story.content_type)
            ui.label("スクレイピング原文").classes("text-sm font-semibold")
            if raw_path.exists():
                raw_text = raw_path.read_text(encoding="utf-8")
                ui.label(f"{len(raw_text)}文字").classes("text-xs text-gray-500")
                ui.textarea(value=raw_text).props(
                    "readonly dense autogrow"
                ).classes("w-full").style("font-size: 15px; line-height: 1.6;")
            else:
                ui.label("未取得").classes("text-gray-500 text-sm")

        with ui.column().classes("flex-1 min-w-0"):
            proc_path = processed_text_path(story.title, story.content_type)
            ui.label("処理後テキスト (ひらがな)").classes("text-sm font-semibold")
            if proc_path.exists():
                proc_text = proc_path.read_text(encoding="utf-8")
                ui.label(f"{len(proc_text)}文字").classes("text-xs text-gray-500")
                ui.textarea(value=proc_text).props(
                    "readonly dense autogrow"
                ).classes("w-full").style("font-size: 15px; line-height: 1.6;")
            else:
                ui.label("未処理").classes("text-gray-500 text-sm")


def _show_youtube_upload_tab(story):
    """YouTube upload tab."""
    vid_path = video_path(story.title, story.content_type)
    if vid_path.exists():
        _show_youtube_upload(story)
    else:
        ui.label("動画が未生成のため、アップロードできません。").classes("text-gray-500")


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
        is_short = story.content_type == "short"
        title_template = cfg_get("shorts_youtube_title_template" if is_short else "youtube_title_template")
        category = story.categories[0] if story.categories else "怪談"
        if is_short:
            yt_title_val = title_template.format(title=story.title)
        else:
            yt_title_val = title_template.format(title=story.title, category=category)
        yt_title = ui.input("タイトル", value=yt_title_val).classes("w-full")
        description_template = cfg_get("shorts_youtube_description_template" if is_short else "youtube_description_template")
        from app.services.voice_generator import get_speaker_name
        speaker_name = get_speaker_name()
        playlist_url = cfg_get("youtube_playlist_url") or ""
        if is_short:
            yt_desc_val = description_template.format(
                title=story.title, url=story.url, author=story.author,
                speaker=speaker_name, playlist_url=playlist_url,
            )
        else:
            yt_desc_val = description_template.format(
                title=story.title, url=story.url, speaker=speaker_name,
                playlist_url=playlist_url,
            )

        # Prepend timestamps if available
        ts_file = timestamps_path(story.title, story.content_type)
        if ts_file.exists():
            import json as _json
            from app.pipeline.stages import _format_timestamp
            parts = _json.loads(ts_file.read_text(encoding="utf-8"))
            ts_lines = [f"{_format_timestamp(p['start'])} {p['label']}" for p in parts]
            yt_desc_val = "\n".join(ts_lines) + "\n\n" + yt_desc_val

        yt_desc = ui.textarea("説明", value=yt_desc_val).classes("w-full")
        tags_str = cfg_get("shorts_youtube_tags" if is_short else "youtube_tags")
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
        next_publish = None
        if schedule_enabled:
            next_publish = youtube_uploader.get_next_publish_time(
                cfg_get("youtube_schedule_day"),
                cfg_get("youtube_schedule_hour"),
                cfg_get("youtube_schedule_minute"),
            )

        yt_schedule = ui.checkbox("予約投稿", value=schedule_enabled)

        # Date/time picker for scheduled publish
        schedule_row = ui.row().classes("gap-2 items-end")
        with schedule_row:
            default_date = next_publish[:10] if next_publish else ""
            default_time = next_publish[11:16] if next_publish else "20:00"
            yt_pub_date = ui.input(label="公開日", value=default_date).classes("w-40")
            with yt_pub_date:
                with ui.menu() as date_menu:
                    ui.date(value=default_date).bind_value(yt_pub_date).on(
                        "update:model-value", lambda: date_menu.close()
                    )
                with yt_pub_date.add_slot("append"):
                    ui.icon("edit_calendar").on("click", date_menu.open).classes("cursor-pointer")

            yt_pub_time = ui.input(label="公開時間", value=default_time).classes("w-32")
            with yt_pub_time:
                with ui.menu() as time_menu:
                    ui.time(value=default_time).bind_value(yt_pub_time).on(
                        "update:model-value", lambda: time_menu.close()
                    )
                with yt_pub_time.add_slot("append"):
                    ui.icon("access_time").on("click", time_menu.open).classes("cursor-pointer")

            ui.label("JST").classes("text-sm text-gray-500")

        schedule_row.bind_visibility_from(yt_schedule, "value")

        progress = ui.linear_progress(value=0, show_value=False).classes("w-full mt-2")
        progress.visible = False
        status_label = ui.label("").classes("text-sm")

        upload_state = {"running": False, "done": False, "progress": 0.0, "msg": "", "error": None}

        def do_upload():
            # Duplicate check
            if show_form is not None and not show_form.value:
                ui.notify("再アップロードを確認してください", color="warning")
                return

            import threading

            upload_state.update(running=True, done=False, error=None, progress=0.0, msg="")
            btn.disable()
            progress.visible = True
            progress.value = 0
            status_label.text = "アップロード中..."

            def run():
                try:
                    tags = [t.strip() for t in yt_tags.value.split(",") if t.strip()]
                    publish_at = None
                    if yt_schedule.value and yt_pub_date.value and yt_pub_time.value:
                        import zoneinfo
                        from datetime import datetime
                        jst = zoneinfo.ZoneInfo("Asia/Tokyo")
                        dt = datetime.strptime(
                            f"{yt_pub_date.value} {yt_pub_time.value}", "%Y-%m-%d %H:%M"
                        ).replace(tzinfo=jst)
                        publish_at = dt.isoformat()

                    def on_progress(cur, total):
                        upload_state["progress"] = cur / total if total > 0 else 0

                    # Use title card as thumbnail
                    thumb = images_dir(story.title, story.content_type) / "000_title_card.png"
                    result = youtube_uploader.upload_video(
                        video_path=video_path(story.title, story.content_type),
                        title=yt_title.value,
                        description=yt_desc.value,
                        tags=tags,
                        category_id=yt_category.value,
                        privacy_status=yt_privacy.value,
                        publish_at=publish_at,
                        thumbnail_path=thumb if thumb.exists() else None,
                        progress_callback=on_progress,
                    )
                    db.set_youtube_video_id(story.id, result["video_id"])
                    db.update_stage(story.id, "youtube_uploaded")

                    msg = f"完了! {result['url']}"
                    if result.get("publish_at"):
                        msg += f"\n予約公開: {result['publish_at'][:16].replace('T', ' ')} JST"
                    upload_state["msg"] = msg
                except Exception as e:
                    upload_state["error"] = str(e)
                upload_state["done"] = True

            threading.Thread(target=run, daemon=True).start()

        def poll_upload():
            if not upload_state["running"]:
                return
            try:
                progress.value = upload_state["progress"]
                if upload_state["done"]:
                    upload_state["running"] = False
                    if upload_state["error"]:
                        status_label.text = f"エラー: {upload_state['error']}"
                        status_label.classes(replace="text-sm text-red-500")
                    else:
                        status_label.text = upload_state["msg"]
                        status_label.classes(replace="text-sm text-green-500")
                        progress.value = 1.0
                    btn.enable()
            except RuntimeError:
                upload_state["running"] = False

        ui.timer(0.5, poll_upload)

        btn = ui.button(
            "承認してYouTubeにアップロード", on_click=do_upload, color="red"
        ).props("size=sm").classes("mt-2")

    # Pinned comment section
    comment_template = cfg_get("youtube_pinned_comment_template")
    if comment_template:
        playlist_url = cfg_get("youtube_playlist_url") or ""
        comment_text = comment_template.format(
            title=story.title, playlist_url=playlist_url,
        )
        ui.separator().classes("my-4")
        ui.label("固定コメント用テキスト").classes("text-lg font-bold")
        comment_area = ui.textarea(value=comment_text).classes("w-full").props("rows=6")

        def copy_comment():
            ui.run_javascript(
                f'navigator.clipboard.writeText({comment_area.value!r})'
            )
            ui.notify("コピーしました", color="positive")

        ui.button("コメントをコピー", on_click=copy_comment, icon="content_copy").props("size=sm outline")


def _show_usage_report_tab(story):
    """HHS Library usage report tab with content preview, retry and error details."""
    import threading

    from app.config import get as cfg_get
    from app.services import youtube_uploader
    from app.services.youtube_uploader import UsageReportError

    fresh = db.get_story_by_id(story.id)
    already_reported = fresh and fresh.stage == "report_submitted"

    if already_reported:
        ui.label("報告済み").classes("text-green-500 font-bold mb-2")

    if not fresh or not fresh.youtube_video_id:
        ui.label("YouTube未アップロードのため、使用報告はできません。").classes("text-gray-500")
        return

    # Show error from previous attempt
    if fresh.error and "使用報告" in (fresh.error or ""):
        with ui.card().classes("w-full p-3 mt-2 mb-4 bg-red-50"):
            ui.label("前回のエラー:").classes("text-sm font-bold text-red-600")
            ui.label(fresh.error).classes("text-sm text-red-500 break-all")

    # Preview: show what will be submitted
    video_url = f"https://youtube.com/watch?v={fresh.youtube_video_id}"
    channel_name = cfg_get("youtube_channel_name") or ""
    contact_email = cfg_get("youtube_contact_email") or ""
    message = f"{channel_name}で使わせていただきました。" if channel_name else ""

    ui.label("送信内容プレビュー").classes("text-sm font-bold mt-2 mb-1")
    with ui.card().classes("w-full p-4 mb-4"):
        with ui.grid(columns=2).classes("gap-x-4 gap-y-1"):
            ui.label("チャンネル名:").classes("text-sm text-gray-500")
            ui.label(channel_name or "未設定").classes(
                "text-sm " + ("text-red-500 font-bold" if not channel_name else "")
            )
            ui.label("メールアドレス:").classes("text-sm text-gray-500")
            ui.label(contact_email or "未設定").classes(
                "text-sm " + ("text-red-500 font-bold" if not contact_email else "")
            )
            ui.label("タイトル:").classes("text-sm text-gray-500")
            ui.label(story.title).classes("text-sm")
            ui.label("動画URL:").classes("text-sm text-gray-500")
            ui.link(video_url, video_url, new_tab=True).classes("text-sm text-blue-500")
            ui.label("メッセージ:").classes("text-sm text-gray-500")
            ui.label(message or "未設定").classes("text-sm")
        ui.label(f"送信先: {youtube_uploader.REPORT_FORM_URL}").classes(
            "text-xs text-gray-400 mt-2"
        )

    status_label = ui.label("").classes("text-sm")
    progress = ui.linear_progress(value=0, show_value=False).classes("w-full")
    progress.visible = False

    report_state = {"running": False, "done": False, "error": None}

    def do_report():
        report_state.update(running=True, done=False, error=None)
        btn.disable()
        progress.visible = True
        progress.value = 0
        status_label.text = "使用報告送信中（最大3回リトライ）..."
        status_label.classes(replace="text-sm text-blue-500")

        def run():
            try:
                youtube_uploader.submit_usage_report(
                    story_title=story.title,
                    video_url=video_url,
                    channel_name=channel_name,
                    email=contact_email,
                )
                db.update_stage(story.id, "report_submitted")
            except (UsageReportError, Exception) as e:
                error_msg = f"使用報告失敗: {e}"
                db.update_stage(story.id, "youtube_uploaded", error=error_msg)
                report_state["error"] = error_msg
            report_state["done"] = True

        threading.Thread(target=run, daemon=True).start()

    def poll_report():
        if not report_state["running"]:
            return
        try:
            if report_state["done"]:
                report_state["running"] = False
                if report_state["error"]:
                    progress.value = 0
                    status_label.text = report_state["error"]
                    status_label.classes(replace="text-sm text-red-500")
                else:
                    progress.value = 1.0
                    status_label.text = "使用報告送信完了!"
                    status_label.classes(replace="text-sm text-green-500")
                btn.enable()
        except RuntimeError:
            report_state["running"] = False

    ui.timer(0.5, poll_report)

    label = "再送信" if already_reported else "使用報告を送信"
    btn = ui.button(label, on_click=do_report, color="purple").props("size=sm")
