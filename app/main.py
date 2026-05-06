from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from nicegui import app as fastapi_app
from nicegui import ui

from app.database import init_db
from app.ui.layout import create_layout
from app.ui.pages.bundle import bundle_page
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


@ui.page("/long/bundle")
def long_bundle():
    create_layout()
    bundle_page()


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


def _tiktok_redirect_uri(request: Request) -> str:
    """Build the TikTok redirect_uri.

    ngrok terminates HTTPS externally and forwards as plain HTTP to the
    container, so ``request.url.scheme`` would be ``http`` even when the
    public URL is ``https``. Prefer the explicit ``TIKTOK_REDIRECT_URI`` env
    var so Sandbox-registered URIs match exactly. Fall back to building
    from the request URL when the env var is not set (local-only dev).
    """
    import os

    explicit = os.environ.get("TIKTOK_REDIRECT_URI", "").strip()
    if explicit:
        return explicit
    base = f"{request.url.scheme}://{request.url.netloc}"
    return f"{base}/tiktok/callback"


@fastapi_app.get("/tiktok/auth")
async def tiktok_auth(request: Request):
    """Start TikTok OAuth flow by redirecting to TikTok consent page."""
    from app.services import tiktok_uploader

    redirect_uri = _tiktok_redirect_uri(request)
    try:
        auth_url = tiktok_uploader.get_auth_url(redirect_uri)
    except Exception as e:
        return HTMLResponse(
            f"<h1>TikTok認証開始に失敗</h1><pre>{e}</pre>", status_code=500,
        )
    return RedirectResponse(auth_url)


@fastapi_app.get("/tiktok/callback")
async def tiktok_callback(
    request: Request, code: str = "", state: str = "", error: str = "",
    error_description: str = "",
):
    """Handle TikTok OAuth redirect and save the token."""
    from app.services import tiktok_uploader

    if error:
        return HTMLResponse(
            f"<h1>TikTok認証エラー</h1><p>{error}: {error_description}</p>"
            f'<p><a href="/settings">設定ページに戻る</a></p>',
            status_code=400,
        )
    if not code:
        return HTMLResponse(
            "<h1>認可コードがありません</h1>"
            '<p><a href="/settings">設定ページに戻る</a></p>',
            status_code=400,
        )

    redirect_uri = _tiktok_redirect_uri(request)
    try:
        tiktok_uploader.exchange_code(code, redirect_uri, state=state)
    except Exception as e:
        return HTMLResponse(
            f"<h1>TikTok認証失敗</h1><pre>{e}</pre>"
            f'<p><a href="/settings">設定ページに戻る</a></p>',
            status_code=500,
        )

    return HTMLResponse(
        "<h1>TikTok認証成功 🎉</h1>"
        "<p>このタブを閉じて、アプリの設定ページまたはストーリー詳細に戻ってください。</p>"
        '<p><a href="/settings">設定ページに戻る</a></p>',
    )


@fastapi_app.get("/terms", include_in_schema=False)
@fastapi_app.get("/terms.html", include_in_schema=False)
async def serve_terms():
    """Serve Terms of Service page (linked from TikTok app review form).

    Hosting on the same verified ngrok URL prefix as the OAuth callback so a
    single TikTok URL Property covers all required URLs.
    """
    from pathlib import Path

    p = Path("docs/terms.html")
    if not p.exists():
        return HTMLResponse("Terms file missing", status_code=404)
    return HTMLResponse(p.read_text(encoding="utf-8"))


@fastapi_app.get("/privacy", include_in_schema=False)
@fastapi_app.get("/privacy.html", include_in_schema=False)
async def serve_privacy():
    """Serve Privacy Policy page (linked from TikTok app review form)."""
    from pathlib import Path

    p = Path("docs/privacy.html")
    if not p.exists():
        return HTMLResponse("Privacy file missing", status_code=404)
    return HTMLResponse(p.read_text(encoding="utf-8"))


@fastapi_app.get("/{filename:str}", include_in_schema=False)
async def tiktok_verify_file(filename: str):
    """Serve TikTok URL property verification signature files.

    TikTok issues a unique file name like ``tiktok123abc.txt`` to prove
    domain ownership. Drop the downloaded file into ``data/tiktok_verify/``
    and this route serves it at the URL root. Only matches files starting
    with ``tiktok`` and ending with ``.txt`` to avoid shadowing other paths.
    Uses ``:str`` (not ``:path``) so slashes are not matched, keeping
    multi-segment routes like ``/tiktok/auth`` reachable by the more
    specific handlers registered above.
    """
    if not filename.startswith("tiktok") or not filename.endswith(".txt"):
        return HTMLResponse("Not Found", status_code=404)

    from pathlib import Path

    p = Path("data/tiktok_verify") / filename
    if not p.exists() or not p.is_file():
        return HTMLResponse("Verification file not found", status_code=404)
    return HTMLResponse(p.read_text(encoding="utf-8"), media_type="text/plain")


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
