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


def authenticate() -> bool:
    """Run OAuth flow. Returns True if successful.

    This opens a browser window for the user to authorize.
    Must be run where a browser is available (not inside Docker).
    """
    if not is_configured():
        raise RuntimeError(
            f"client_secret.json が見つかりません: {CLIENT_SECRET_PATH}"
        )

    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_PATH, SCOPES)
    credentials = flow.run_local_server(port=0, open_browser=False)

    Path(TOKEN_PATH).parent.mkdir(parents=True, exist_ok=True)
    with open(TOKEN_PATH, "w") as f:
        f.write(credentials.to_json())

    log.info("YouTube認証成功、トークンを保存しました")
    return True


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
    from datetime import datetime, timedelta
    import zoneinfo

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
def upload_video(
    video_path: str | Path,
    title: str,
    description: str = "",
    tags: list[str] | None = None,
    category_id: str = "24",
    privacy_status: str = "private",
    publish_at: str | None = None,
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
    return result


REPORT_FORM_URL = "https://hhs.parasite.jp/hhslibrary/?p=6590"


def submit_usage_report(
    story_title: str,
    video_url: str,
    channel_name: str,
    email: str,
    note: str = "",
) -> bool:
    """Submit usage report to HHS Library (ホラホリ) as required by their terms.

    Returns True if submission was successful.
    """
    if not channel_name or not email:
        log.warning("使用報告スキップ: チャンネル名またはメールアドレスが未設定")
        return False

    import requests as req

    # Get the form page to extract wpcf7 hidden fields
    page = req.get(REPORT_FORM_URL, timeout=30)
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(page.content, "html.parser")
    form = soup.select_one("form.wpcf7-form")
    if not form:
        log.error("使用報告フォームが見つかりません")
        return False

    # Extract hidden fields
    hidden_fields = {}
    for inp in form.find_all("input", type="hidden"):
        name = inp.get("name", "")
        if name:
            hidden_fields[name] = inp.get("value", "")

    wpcf7_id = hidden_fields.get("_wpcf7", "")
    unit_tag = hidden_fields.get("_wpcf7_unit_tag", "")

    # Submit via REST API
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

    resp = req.post(api_url, data=data, timeout=30)

    if resp.status_code == 200:
        result = resp.json()
        if result.get("status") == "mail_sent":
            log.info("使用報告送信成功: %s", story_title)
            return True
        log.warning("使用報告送信失敗: %s", result.get("message", ""))
        return False

    log.error("使用報告送信エラー: HTTP %d", resp.status_code)
    return False
