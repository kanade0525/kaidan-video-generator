from __future__ import annotations

from nicegui import ui


def create_layout():
    """Create the common page layout with sidebar navigation."""
    with ui.header().classes("bg-gray-900 text-white"):
        ui.label("怪談動画ジェネレータ").classes("text-xl font-bold")

    with ui.left_drawer(value=True).classes("bg-gray-800 text-white") as drawer:
        ui.label("メニュー").classes("text-lg font-bold mb-4 mt-2")

        link_cls = "text-white no-underline px-3 py-2 rounded hover:bg-gray-700 block"

        with ui.column().classes("gap-1 w-full"):
            ui.label("長編動画").classes("text-xs text-gray-400 px-3 mt-2")
            ui.link("パイプライン", "/").classes(link_cls)
            ui.link("ストーリー管理", "/stories").classes(link_cls)
            ui.link("生成結果", "/results").classes(link_cls)

            ui.separator().classes("my-2 bg-gray-600")

            ui.label("ショート動画").classes("text-xs text-gray-400 px-3 mt-2")
            ui.link("Shortsパイプライン", "/shorts").classes(link_cls)
            ui.link("Shortsストーリー", "/shorts/stories").classes(link_cls)
            ui.link("Shorts生成結果", "/shorts/results").classes(link_cls)

            ui.separator().classes("my-2 bg-gray-600")

            ui.link("読み監査レビュー", "/audit_review").classes(link_cls)
            ui.link("設定", "/settings").classes(link_cls)

    return drawer
