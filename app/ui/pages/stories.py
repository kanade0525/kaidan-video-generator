from __future__ import annotations

from nicegui import ui

from app import database as db
from app.models import STAGE_LABELS, STAGES
from app.pipeline.executor import pipeline
from app.services.scraper import fetch_rss_stories


def stories_page(category: str = "", page: int = 0):
    """Story management page."""
    ui.label("ストーリー管理").classes("text-2xl font-bold mb-4")

    # Controls
    with ui.row().classes("gap-2 mb-4 items-end"):
        url_input = ui.input("URL追加").classes("w-96")
        ui.button("追加", on_click=lambda: _add_url(url_input)).props("size=sm")
        ui.button("RSSインポート", on_click=lambda: _import_rss(), color="blue").props("size=sm")

    # Filters (stage resets on reload so updated stories always appear)
    with ui.row().classes("gap-2 mb-4 items-end"):
        stage_filter = ui.select(
            {"": "全て", **{s: STAGE_LABELS.get(s, s) for s in STAGES}},
            value="",
            label="ステージ",
        ).classes("w-48")

        categories = db.get_categories()
        cat_options = {"": "全て", **{c: c for c in categories}}
        cat_filter = ui.select(cat_options, value=category, label="カテゴリ").classes("w-48")

        ui.button("検索", on_click=lambda: refresh()).props("size=sm")

    # Story table
    table_container = ui.column().classes("w-full")

    # Pagination
    page_state = {"current": page, "per_page": 20}

    with ui.row().classes("gap-2 mt-4"):
        ui.button("前", on_click=lambda: _prev_page()).props("size=sm")
        page_label = ui.label("1")
        ui.button("次", on_click=lambda: _next_page()).props("size=sm")

    def _update_url():
        """Update URL query params to preserve filter state."""
        from app.ui.url_state import build_stories_url
        url = build_stories_url(
            category=cat_filter.value or "",
            page=page_state["current"],
        )
        ui.run_javascript(f'window.history.replaceState(null, "", "{url}")')

    def refresh():
        st = stage_filter.value or None
        cat = cat_filter.value or None
        offset = page_state["current"] * page_state["per_page"]
        _update_url()

        stories = db.get_stories(
            stage=st, category=cat,
            limit=page_state["per_page"], offset=offset,
        )
        total = db.count_stories(stage=st, category=cat)
        total_pages = max(1, (total + page_state["per_page"] - 1) // page_state["per_page"])
        page_label.text = f"{page_state['current'] + 1} / {total_pages} ({total}件)"

        table_container.clear()
        with table_container:
            if not stories:
                ui.label("ストーリーなし").classes("text-gray-500")
                return

            columns = [
                {"name": "title", "label": "タイトル", "field": "title", "align": "left"},
                {"name": "url", "label": "URL", "field": "url", "align": "left"},
                {"name": "categories", "label": "カテゴリ", "field": "categories"},
                {"name": "stage", "label": "ステージ", "field": "stage"},
                {"name": "error", "label": "エラー", "field": "error"},
                {"name": "actions", "label": "操作", "field": "actions"},
            ]
            rows = []
            for s in stories:
                rows.append({
                    "id": s.id,
                    "title": s.title[:30],
                    "url": s.url,
                    "categories": ", ".join(s.categories),
                    "stage": STAGE_LABELS.get(s.stage, s.stage),
                    "error": (s.error or "")[:40],
                    "actions": "",
                })

            table = ui.table(columns=columns, rows=rows, row_key="id").classes("w-full")

            table.add_slot("body-cell-url", """
                <q-td :props="props">
                    <a :href="props.value" target="_blank" class="text-blue-500 underline">
                        元ページ
                    </a>
                </q-td>
            """)

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
    result = db.add_story(url=url)
    if result:
        ui.notify(f"追加: {result.title or url}", color="positive")
        url_input.value = ""
    else:
        ui.notify("このURLは既に登録されています", color="warning")


def _import_rss():
    try:
        items = fetch_rss_stories()
        added = 0
        for item in items:
            result = db.add_story(
                url=item["url"],
                title=item["title"],
                pub_date=item.get("pub_date", ""),
            )
            if result:
                added += 1
        ui.notify(f"RSS: {added}件追加 ({len(items)}件中)", color="positive")
    except Exception as e:
        ui.notify(f"RSSエラー: {e}", color="negative")


def _show_detail(story_id: int):
    ui.navigate.to(f"/results?id={story_id}")


def _delete_story(story_id: int, refresh_fn):
    db.delete_story(story_id)
    ui.notify("削除しました", color="info")
    refresh_fn()
