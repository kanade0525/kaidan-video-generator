from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

from nicegui import app, ui

from app.database import init_db
from app.ui.layout import create_layout
from app.ui.pages.pipeline import pipeline_page
from app.ui.pages.results import results_page
from app.ui.pages.settings import settings_page
from app.ui.pages.stories import stories_page
from app.utils.log import setup_logging

# Initialize
setup_logging()
init_db()


@ui.page("/")
def index():
    create_layout()
    pipeline_page()


@ui.page("/stories")
def stories(category: str = "", page: int = 0):
    create_layout()
    stories_page(category=category, page=page)


@ui.page("/results")
def results(keyword: str = "", id: int = 0):
    create_layout()
    results_page(keyword=keyword, story_id=id)


@ui.page("/settings")
def settings():
    create_layout()
    settings_page()


def main():
    ui.run(
        title="怪談動画ジェネレータ",
        host="0.0.0.0",
        port=8080,
        reload=False,
        show=False,
    )


if __name__ == "__main__":
    main()
