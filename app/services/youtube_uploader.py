"""YouTube video uploader using YouTube Data API v3."""

from __future__ import annotations

import os
from pathlib import Path

from app.pipeline.retry import with_retry
from app.utils.log import get_logger

log = get_logger("kaidan.youtube")

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
CLIENT_SECRET_PATH = os.environ.get(
    "YOUTUBE_CLIENT_SECRET_PATH", "data/client_secret.json"
)
TOKEN_PATH = os.environ.get("YOUTUBE_TOKEN_PATH", "data/youtube_token.json")

_youtube_service = None

# In-memory store of OAuth flow state → code_verifier for PKCE.
# Entries are removed after exchange.
_oauth_pending: dict[str, str] = {}


def is_configured() -> bool:
    """Check if YouTube credentials are set up."""
    return Path(CLIENT_SECRET_PATH).exists()


def is_authenticated() -> bool:
    """Check if we have a valid token."""
    if not Path(TOKEN_PATH).exists():
        return False
    try:
        from google.oauth2.credentials import Credentials

        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
        return creds.valid or (creds.expired and creds.refresh_token)
    except Exception:
        return False


def _build_flow(redirect_uri: str):
    """Build an OAuth Flow with the given redirect_uri."""
    if not is_configured():
        raise RuntimeError(
            f"client_secret.json が見つかりません: {CLIENT_SECRET_PATH}"
        )
    from google_auth_oauthlib.flow import Flow

    return Flow.from_client_secrets_file(
        CLIENT_SECRET_PATH, scopes=SCOPES, redirect_uri=redirect_uri,
    )


def get_auth_url(redirect_uri: str) -> str:
    """Build the Google OAuth consent URL for the given redirect_uri.

    Stores the PKCE code_verifier keyed by state so the callback can
    rebuild the Flow with the same verifier.
    """
    flow = _build_flow(redirect_uri)
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",  # Always return a fresh refresh_token
    )
    # Save code_verifier so exchange_code can rehydrate the flow
    _oauth_pending[state] = flow.code_verifier
    return auth_url


def exchange_code(code: str, redirect_uri: str, state: str = "") -> None:
    """Exchange an authorization code for tokens and save them."""
    flow = _build_flow(redirect_uri)
    # Restore the PKCE verifier saved during get_auth_url
    verifier = _oauth_pending.pop(state, None)
    if verifier:
        flow.code_verifier = verifier
    flow.fetch_token(code=code)
    credentials = flow.credentials

    Path(TOKEN_PATH).parent.mkdir(parents=True, exist_ok=True)
    with open(TOKEN_PATH, "w") as f:
        f.write(credentials.to_json())

    reset_service()
    log.info("YouTube認証成功、トークンを保存しました")


def _get_credentials():
    """Load and refresh credentials."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not Path(TOKEN_PATH).exists():
        raise RuntimeError("YouTube未認証。先に認証を実行してください。")

    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
        log.info("YouTubeトークンをリフレッシュしました")

    if not creds.valid:
        raise RuntimeError("YouTubeトークンが無効です。再認証してください。")

    return creds


def _get_service():
    """Get or create YouTube API service."""
    global _youtube_service
    if _youtube_service is None:
        from googleapiclient.discovery import build

        creds = _get_credentials()
        _youtube_service = build("youtube", "v3", credentials=creds)
    return _youtube_service


def reset_service():
    """Reset cached service (e.g., after re-authentication)."""
    global _youtube_service
    _youtube_service = None


def get_next_publish_time(
    day: str = "saturday",
    hour: int = 20,
    minute: int = 0,
) -> str | None:
    """Calculate the next scheduled publish time as RFC 3339 string.

    Returns None if scheduling is disabled.
    """
    import zoneinfo
    from datetime import datetime, timedelta

    jst = zoneinfo.ZoneInfo("Asia/Tokyo")
    now = datetime.now(jst)

    days_map = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
    }
    target_day = days_map.get(day.lower(), 5)

    # Calculate days until next target day
    days_ahead = target_day - now.weekday()
    if days_ahead < 0:
        days_ahead += 7
    elif days_ahead == 0:
        # Same day: if the time has passed, schedule for next week
        target_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now >= target_time:
            days_ahead = 7

    next_date = now + timedelta(days=days_ahead)
    publish_time = next_date.replace(hour=hour, minute=minute, second=0, microsecond=0)

    return publish_time.isoformat()


@with_retry(max_attempts=3, base_delay=30.0)
def set_thumbnail(video_id: str, thumbnail_path: str | Path) -> None:
    """Set a custom thumbnail for a YouTube video.

    Requires channel phone verification. Silently logs warning on failure.
    """
    from googleapiclient.http import MediaFileUpload

    thumbnail_path = Path(thumbnail_path)
    if not thumbnail_path.exists():
        log.warning("サムネイル画像が見つかりません: %s", thumbnail_path)
        return

    service = _get_service()
    media = MediaFileUpload(str(thumbnail_path), mimetype="image/png")

    try:
        service.thumbnails().set(
            videoId=video_id,
            media_body=media,
        ).execute()
        log.info("サムネイル設定完了: %s", video_id)
    except Exception as e:
        log.warning("サムネイル設定失敗（電話番号認証が必要な場合があります）: %s", e)


def upload_video(
    video_path: str | Path,
    title: str,
    description: str = "",
    tags: list[str] | None = None,
    category_id: str = "24",
    privacy_status: str = "private",
    publish_at: str | None = None,
    thumbnail_path: str | Path | None = None,
    progress_callback=None,
) -> dict:
    """Upload a video to YouTube.

    Returns:
        dict with keys: video_id, title, url, privacy_status
    """
    from googleapiclient.http import MediaFileUpload

    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"動画ファイルが見つかりません: {video_path}")

    service = _get_service()

    status_body = {
        "privacyStatus": privacy_status,
        "embeddable": True,
        "selfDeclaredMadeForKids": False,
    }
    if publish_at:
        status_body["privacyStatus"] = "private"
        status_body["publishAt"] = publish_at
        log.info("予約投稿: %s", publish_at)

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags or [],
            "categoryId": category_id,
            "defaultLanguage": "ja",
        },
        "status": status_body,
    }

    media = MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        resumable=True,
        chunksize=1024 * 1024,  # 1MB chunks
    )

    request = service.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    log.info("YouTubeアップロード開始: %s", title)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status and progress_callback:
            progress_callback(int(status.progress() * 100), 100)

    video_id = response.get("id", "")
    result = {
        "video_id": video_id,
        "title": response.get("snippet", {}).get("title", title),
        "url": f"https://youtube.com/watch?v={video_id}",
        "privacy_status": response.get("status", {}).get(
            "privacyStatus", privacy_status
        ),
        "publish_at": publish_at,
    }

    log.info("YouTubeアップロード完了: %s", result["url"])

    # Set custom thumbnail if provided
    if thumbnail_path:
        set_thumbnail(video_id, thumbnail_path)

    return result


REPORT_FORM_URL = "https://hhs.parasite.jp/hhslibrary/?p=6590"


class UsageReportError(RuntimeError):
    """Raised when usage report submission fails with details."""


@with_retry(max_attempts=3, base_delay=5.0)
def submit_usage_report(
    story_title: str,
    video_url: str,
    channel_name: str,
    email: str,
    note: str = "",
) -> None:
    """Submit usage report to HHS Library (ホラホリ) as required by their terms.

    Raises UsageReportError with details on failure.
    """
    if not channel_name or not email:
        raise UsageReportError(
            "チャンネル名またはメールアドレスが未設定です。設定ページから設定してください。"
        )

    import requests as req

    # Get the form page to extract wpcf7 hidden fields
    try:
        page = req.get(REPORT_FORM_URL, timeout=30)
        page.raise_for_status()
    except req.RequestException as e:
        raise UsageReportError(f"フォームページの取得に失敗: {e}") from e

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(page.content, "html.parser")
    form = soup.select_one("form.wpcf7-form")
    if not form:
        raise UsageReportError(
            f"使用報告フォームが見つかりません (URL: {REPORT_FORM_URL}, "
            f"status: {page.status_code}, content_length: {len(page.content)})"
        )

    # Extract hidden fields
    hidden_fields = {}
    for inp in form.find_all("input", type="hidden"):
        name = inp.get("name", "")
        if name:
            hidden_fields[name] = inp.get("value", "")

    wpcf7_id = hidden_fields.get("_wpcf7", "")
    if not wpcf7_id:
        raise UsageReportError("フォームのwpcf7 IDが取得できません")

    unit_tag = hidden_fields.get("_wpcf7_unit_tag", "")

    # Extract REST API root from wpcf7 JS config on the page
    import json as _json
    import re

    api_root = None
    for script in soup.find_all("script"):
        text = script.string or ""
        m = re.search(r'var\s+wpcf7\s*=\s*(\{.*?\})\s*;', text, re.DOTALL)
        if m:
            try:
                cfg = _json.loads(m.group(1))
                api_root = cfg.get("api", {}).get("root", "")
                api_ns = cfg.get("api", {}).get("namespace", "contact-form-7/v1")
            except _json.JSONDecodeError:
                pass
            break

    if api_root:
        api_url = f"{api_root.rstrip('/')}/{api_ns}/contact-forms/{wpcf7_id}/feedback"
    else:
        # Fallback: standard WP REST API path
        api_url = f"https://hhs.parasite.jp/hhslibrary/wp-json/contact-form-7/v1/contact-forms/{wpcf7_id}/feedback"

    data = {
        "_wpcf7": wpcf7_id,
        "_wpcf7_version": hidden_fields.get("_wpcf7_version", ""),
        "_wpcf7_locale": hidden_fields.get("_wpcf7_locale", "ja"),
        "_wpcf7_unit_tag": unit_tag,
        "_wpcf7_container_post": hidden_fields.get("_wpcf7_container_post", ""),
        "_wpcf7_posted_data_hash": "",
        "your-name": channel_name,
        "your-email": email,
        "your-subject": story_title,
        "url-681": video_url,
        "your-message": note or f"{channel_name}で使わせていただきました。",
    }

    # CF7 5.8+ requires multipart/form-data
    multipart = {k: (None, v) for k, v in data.items()}

    try:
        resp = req.post(api_url, files=multipart, timeout=30)
    except req.RequestException as e:
        raise UsageReportError(f"フォーム送信リクエスト失敗: {e}") from e

    if resp.status_code != 200:
        # HTMLレスポンスからtitleを抽出して読みやすいエラーにする
        detail = _extract_error_detail(resp.text)
        raise UsageReportError(
            f"フォーム送信エラー: HTTP {resp.status_code} - {detail} "
            f"(API URL: {api_url})"
        )

    try:
        result = resp.json()
    except ValueError as e:
        detail = _extract_error_detail(resp.text)
        raise UsageReportError(f"レスポンスがJSONではありません: {detail}") from e

    if result.get("status") == "mail_sent":
        log.info("使用報告送信成功: %s", story_title)
        return

    raise UsageReportError(
        f"フォーム送信失敗: status={result.get('status')}, "
        f"message={result.get('message', '不明')}"
    )


def _extract_error_detail(html: str) -> str:
    """Extract readable error detail from an HTML error page."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    title = soup.find("title")
    if title and title.string:
        return title.string.strip()
    # titleがなければbodyのテキスト先頭を返す
    text = soup.get_text(separator=" ", strip=True)
    return text[:100] if text else "(空のレスポンス)"
