from __future__ import annotations

from nicegui import app, ui


def create_layout():
    """Create the common page layout with sidebar navigation."""
    with ui.header().classes("bg-gray-900 text-white"):
        ui.label("怪談動画ジェネレータ").classes("text-xl font-bold")

    with ui.left_drawer(value=True).classes("bg-gray-800 text-white") as drawer:
        ui.label("メニュー").classes("text-lg font-bold mb-4 mt-2")

        with ui.column().classes("gap-1 w-full"):
            ui.link("パイプライン", "/").classes(
                "text-white no-underline px-3 py-2 rounded hover:bg-gray-700 block"
            )
            ui.link("ストーリー管理", "/stories").classes(
                "text-white no-underline px-3 py-2 rounded hover:bg-gray-700 block"
            )
            ui.link("生成結果", "/results").classes(
                "text-white no-underline px-3 py-2 rounded hover:bg-gray-700 block"
            )
            ui.link("設定", "/settings").classes(
                "text-white no-underline px-3 py-2 rounded hover:bg-gray-700 block"
            )

    return drawer
