from __future__ import annotations

from nicegui import ui

from app import database as db
from app.models import STAGE_LABELS, STAGES_SHORT
from app.services import kikikaikai_scraper


def shorts_stories_page(category: str = "", page: int = 0):
    """Shorts story management page (source: kikikaikai)."""
    ui.label("Shorts ストーリー管理").classes("text-2xl font-bold mb-4")
    ui.label("ソース: 奇々怪々 (kikikaikai.kusuguru.co.jp)").classes(
        "text-sm text-gray-400 mb-4"
    )

    # Controls
    with ui.row().classes("gap-2 mb-4 items-end"):
        url_input = ui.input("奇々怪々URL追加").classes("w-96")
        url_input.props('placeholder="https://kikikaikai.kusuguru.co.jp/12345"')
        ui.button("追加", on_click=lambda: _add_url(url_input)).props("size=sm")
        ui.button(
            "タグインポート",
            on_click=lambda: _show_tag_import_dialog(),
            color="blue",
        ).props("size=sm")

    # Filters
    with ui.row().classes("gap-2 mb-4 items-end"):
        stage_filter = ui.select(
            {"": "全て", **{s: STAGE_LABELS.get(s, s) for s in STAGES_SHORT}},
            value="",
            label="ステージ",
        ).classes("w-48")

        ui.button("検索", on_click=lambda: refresh()).props("size=sm")

    # Story table
    table_container = ui.column().classes("w-full")

    # Pagination
    page_state = {"current": page, "per_page": 20}

    with ui.row().classes("gap-2 mt-4"):
        ui.button("前", on_click=lambda: _prev_page()).props("size=sm")
        page_label = ui.label("1")
        ui.button("次", on_click=lambda: _next_page()).props("size=sm")

    def refresh():
        st = stage_filter.value or None
        offset = page_state["current"] * page_state["per_page"]

        stories = db.get_stories(
            stage=st, content_type="short",
            limit=page_state["per_page"], offset=offset,
        )
        total = db.count_stories(stage=st, content_type="short")
        total_pages = max(1, (total + page_state["per_page"] - 1) // page_state["per_page"])
        page_label.text = f"{page_state['current'] + 1} / {total_pages} ({total}件)"

        table_container.clear()
        with table_container:
            if not stories:
                ui.label("ストーリーなし").classes("text-gray-500")
                return

            columns = [
                {"name": "title", "label": "タイトル", "field": "title", "align": "left"},
                {"name": "author", "label": "作者", "field": "author"},
                {"name": "char_count", "label": "文字数", "field": "char_count"},
                {"name": "stage", "label": "ステージ", "field": "stage"},
                {"name": "error", "label": "エラー", "field": "error"},
                {"name": "actions", "label": "操作", "field": "actions"},
            ]
            rows = []
            for s in stories:
                rows.append({
                    "id": s.id,
                    "title": s.title[:30],
                    "author": s.author or "-",
                    "char_count": s.char_count or "-",
                    "stage": STAGE_LABELS.get(s.stage, s.stage),
                    "error": (s.error or "")[:40],
                    "actions": "",
                })

            table = ui.table(columns=columns, rows=rows, row_key="id").classes("w-full")

            table.add_slot("body-cell-actions", """
                <q-td :props="props">
                    <q-btn size="sm" color="blue" label="詳細"
                        @click="$parent.$emit('detail', props.row)" />
                    <q-btn size="sm" color="red" label="削除"
                        @click="$parent.$emit('delete', props.row)" class="q-ml-sm" />
                </q-td>
            """)

            table.on("detail", lambda e: _show_detail(e.args["id"]))
            table.on("delete", lambda e: _delete_story(e.args["id"], refresh))

    def _prev_page():
        if page_state["current"] > 0:
            page_state["current"] -= 1
            refresh()

    def _next_page():
        page_state["current"] += 1
        refresh()

    refresh()


def _add_url(url_input):
    url = url_input.value.strip()
    if not url:
        ui.notify("URLを入力してください", color="warning")
        return
    if "kikikaikai" not in url:
        ui.notify("奇々怪々のURLを入力してください", color="warning")
        return

    # Fetch story content to get metadata
    try:
        _, metadata = kikikaikai_scraper.fetch_story_content(url)
        title = metadata.get("title", "")
        author = metadata.get("author", "")
        char_count = metadata.get("char_count", 0)
        tags = metadata.get("tags", [])

        result = db.add_story(
            url=url,
            title=title,
            content_type="short",
            author=author,
            char_count=char_count,
            categories=tags,
        )
        if result:
            ui.notify(f"追加: {title} ({char_count}文字)", color="positive")
            url_input.value = ""
        else:
            ui.notify("このURLは既に登録されています", color="warning")
    except Exception as e:
        ui.notify(f"取得エラー: {e}", color="negative")


def _show_tag_import_dialog():
    with ui.dialog() as dialog, ui.card().classes("w-96"):
        ui.label("タグからインポート").classes("text-lg font-bold mb-2")
        ui.label("奇々怪々のタグを選択してストーリーを一括インポートします").classes(
            "text-sm text-gray-500 mb-4"
        )

        tag_input = ui.input("タグスラッグ (例: shinrei, obon)").classes("w-full")
        max_pages_input = ui.number("最大ページ数", value=3, min=1, max=20).classes("w-full")

        result_label = ui.label("").classes("text-sm mt-2")

        async def do_import():
            tag = tag_input.value.strip()
            if not tag:
                ui.notify("タグを入力してください", color="warning")
                return

            max_pages = int(max_pages_input.value or 3)
            result_label.text = "取得中..."

            try:
                stories = kikikaikai_scraper.fetch_stories_from_tag(tag, max_pages=max_pages)
                added = 0
                skipped = 0
                for s in stories:
                    result = db.add_story(
                        url=s["url"],
                        title=s["title"],
                        content_type="short",
                        author=s.get("author", ""),
                    )
                    if result:
                        added += 1
                    else:
                        skipped += 1
                result_label.text = f"完了: {added}件追加 ({skipped}件スキップ)"
                ui.notify(f"タグ '{tag}': {added}件追加", color="positive")
            except Exception as e:
                result_label.text = f"エラー: {e}"
                ui.notify(f"インポートエラー: {e}", color="negative")

        with ui.row().classes("gap-2 mt-4"):
            ui.button("インポート", on_click=do_import, color="green")
            ui.button("閉じる", on_click=dialog.close)

    dialog.open()


def _show_detail(story_id: int):
    ui.navigate.to(f"/results?id={story_id}")


def _delete_story(story_id: int, refresh_fn):
    db.delete_story(story_id)
    ui.notify("削除しました", color="info")
    refresh_fn()
