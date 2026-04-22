from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from nicegui import app as fastapi_app
from nicegui import ui

from app.database import init_db
from app.ui.layout import create_layout
from app.ui.pages.pipeline import pipeline_page
from app.ui.pages.results import results_page
from app.ui.pages.settings import settings_page
from app.ui.pages.shorts_pipeline import shorts_pipeline_page
from app.ui.pages.shorts_stories import shorts_stories_page
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
def results(keyword: str = "", id: int = 0, category: str = ""):
    create_layout()
    results_page(keyword=keyword, story_id=id, content_type="long", category=category)


@ui.page("/settings")
def settings():
    create_layout()
    settings_page()


@ui.page("/shorts")
def shorts():
    create_layout()
    shorts_pipeline_page()


@ui.page("/shorts/stories")
def shorts_stories(page: int = 0):
    create_layout()
    shorts_stories_page(page=page)


@ui.page("/shorts/results")
def shorts_results(keyword: str = "", id: int = 0):
    create_layout()
    results_page(keyword=keyword, story_id=id, content_type="short", base_path="/shorts/results")


@fastapi_app.get("/youtube/auth")
async def youtube_auth(request: Request):
    """Start YouTube OAuth flow by redirecting to Google consent page."""
    from app.services import youtube_uploader

    base = f"{request.url.scheme}://{request.url.netloc}"
    redirect_uri = f"{base}/oauth2callback"
    try:
        auth_url = youtube_uploader.get_auth_url(redirect_uri)
    except Exception as e:
        return HTMLResponse(
            f"<h1>認証開始に失敗</h1><pre>{e}</pre>", status_code=500,
        )
    return RedirectResponse(auth_url)


@fastapi_app.get("/oauth2callback")
async def oauth2callback(
    request: Request, code: str = "", state: str = "", error: str = "",
):
    """Handle Google OAuth redirect and save the token."""
    from app.services import youtube_uploader

    if error:
        return HTMLResponse(
            f"<h1>認証エラー</h1><p>{error}</p>"
            f'<p><a href="/settings">設定ページに戻る</a></p>',
            status_code=400,
        )
    if not code:
        return HTMLResponse(
            "<h1>認可コードがありません</h1>"
            '<p><a href="/settings">設定ページに戻る</a></p>',
            status_code=400,
        )

    base = f"{request.url.scheme}://{request.url.netloc}"
    redirect_uri = f"{base}/oauth2callback"
    try:
        youtube_uploader.exchange_code(code, redirect_uri, state=state)
    except Exception as e:
        return HTMLResponse(
            f"<h1>認証失敗</h1><pre>{e}</pre>"
            f'<p><a href="/settings">設定ページに戻る</a></p>',
            status_code=500,
        )

    return HTMLResponse(
        "<h1>YouTube認証成功 🎉</h1>"
        "<p>このタブを閉じて、アプリの設定ページに戻ってください。</p>"
        '<p><a href="/settings">設定ページに戻る</a></p>',
    )


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
