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


JST = "Asia/Tokyo"


def _jst_now():
    import zoneinfo
    from datetime import datetime

    return datetime.now(zoneinfo.ZoneInfo(JST))


def get_next_publish_time(
    day: str = "saturday",
    hour: int = 20,
    minute: int = 0,
) -> str | None:
    """Calculate the next weekly publish slot as an RFC 3339 string."""
    from datetime import timedelta

    now = _jst_now()

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


def get_next_interval_publish_time(
    interval_days: int = 2,
    hour: int = 20,
    minute: int = 0,
    last_scheduled: str | None = None,
) -> str:
    """N 日おきの次の公開枠を返す (RFC 3339, JST)。

    `last_scheduled` に「既に予約済みの最も遅い公開日時」を渡すと、その枠から
    さらに `interval_days` 進めた時刻を返す。連続で複数本アップロードしても
    全部が同じ日時に固まらず、1 本ずつ N 日おきに並ぶ。

    予約済みが無い / 過去の場合は「今日のこの時刻」を起点にし、既に過ぎていれば
    `interval_days` 日後にする。
    """
    from datetime import datetime, timedelta

    interval = max(1, int(interval_days))
    now = _jst_now()
    today_slot = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    base: datetime | None = None
    if last_scheduled:
        try:
            parsed = datetime.fromisoformat(last_scheduled)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=now.tzinfo)
            base = parsed.astimezone(now.tzinfo)
        except ValueError:
            log.warning("予約済み日時を解釈できません: %s", last_scheduled)

    if base is not None and base >= now:
        candidate = base + timedelta(days=interval)
    else:
        candidate = today_slot if today_slot > now else today_slot + timedelta(days=interval)

    candidate = candidate.replace(hour=hour, minute=minute, second=0, microsecond=0)
    # 予約が現在時刻より後になるまで進める (時刻設定を過去へ変えた場合の保険)
    while candidate <= now:
        candidate += timedelta(days=interval)
    return candidate.isoformat()


def resolve_publish_time() -> str | None:
    """設定に従って次の公開予約時刻を決める。無効なら None。

    "interval" モードでは DB に記録済みの最も遅い予約時刻を起点にするので、
    オートパイロットで動画が溜まっても 1 本ずつ N 日おきに公開される。
    """
    from app.config import get as cfg_get

    if not cfg_get("youtube_schedule_enabled"):
        return None

    hour = cfg_get("youtube_schedule_hour")
    minute = cfg_get("youtube_schedule_minute")
    hour = 20 if hour is None else int(hour)
    minute = 0 if minute is None else int(minute)

    mode = cfg_get("youtube_schedule_mode") or "weekly"
    if mode == "interval":
        from app import database as db

        return get_next_interval_publish_time(
            interval_days=int(cfg_get("youtube_schedule_interval_days") or 2),
            hour=hour,
            minute=minute,
            last_scheduled=db.get_latest_scheduled_publish_at(),
        )

    return get_next_publish_time(
        day=cfg_get("youtube_schedule_day") or "saturday",
        hour=hour,
        minute=minute,
    )


# YouTube のカスタムサムネイルは 2MB まで。超えると
# "Media larger than: 2097152" で失敗する。生成した PNG は縦長 1080x1920 で
# 2MB を超えることがあるので、送る前に必ず収める。
THUMBNAIL_MAX_BYTES = 2 * 1024 * 1024


def fit_thumbnail(source: Path) -> tuple[Path, str]:
    """サムネイルを 2MB 以内に収める。(送信するパス, mimetype) を返す。

    収まっていればそのまま。超えていれば JPEG に変換し、品質を落として再試行する。
    それでも収まらなければ縮小する。1080x1920 が必要なのは動画側で、サムネイルは
    YouTube 側でどのみち縮小されるため、多少小さくても実害はない。
    """
    from io import BytesIO

    from PIL import Image

    mimetype = "image/jpeg" if source.suffix.lower() in (".jpg", ".jpeg") else "image/png"
    if source.stat().st_size <= THUMBNAIL_MAX_BYTES:
        return source, mimetype

    img = Image.open(source)
    if img.mode != "RGB":
        img = img.convert("RGB")

    for scale in (1.0, 0.75, 0.5):
        resized = img if scale == 1.0 else img.resize(
            (int(img.width * scale), int(img.height * scale)), Image.LANCZOS,
        )
        for quality in (90, 80, 70, 60):
            buf = BytesIO()
            resized.save(buf, format="JPEG", quality=quality, optimize=True)
            if buf.tell() <= THUMBNAIL_MAX_BYTES:
                out = source.with_name(f"{source.stem}_thumb.jpg")
                out.write_bytes(buf.getvalue())
                log.info(
                    "サムネイルを圧縮: %s → %s (%.1fMB → %.1fMB, %d%%/q%d)",
                    source.name, out.name,
                    source.stat().st_size / 1024 / 1024, buf.tell() / 1024 / 1024,
                    int(scale * 100), quality,
                )
                return out, "image/jpeg"

    raise RuntimeError(
        f"サムネイルを {THUMBNAIL_MAX_BYTES // 1024 // 1024}MB 以内に圧縮できません: {source}"
    )


@with_retry(max_attempts=3, base_delay=30.0)
def set_thumbnail(video_id: str, thumbnail_path: str | Path) -> None:
    """Set a custom thumbnail for a YouTube video.

    失敗しても例外は投げない — 動画自体は既にアップロード済みで、ここで
    ステージを失敗させると再アップロードされて重複投稿になる。ただし
    ログは ERROR で残し、原因をそのまま出す (以前は原因不明の warning 1行で、
    2MB 超過を「電話番号認証が必要」と誤誘導していた)。
    """
    from googleapiclient.http import MediaFileUpload

    thumbnail_path = Path(thumbnail_path)
    if not thumbnail_path.exists():
        log.error("サムネイル画像が見つかりません: %s", thumbnail_path)
        return

    service = _get_service()
    try:
        send_path, mimetype = fit_thumbnail(thumbnail_path)
    except Exception as e:
        log.error("サムネイルの圧縮に失敗、設定をスキップ: %s", e)
        return

    media = MediaFileUpload(str(send_path), mimetype=mimetype)
    try:
        service.thumbnails().set(
            videoId=video_id,
            media_body=media,
        ).execute()
        log.info("サムネイル設定完了: %s (%s)", video_id, send_path.name)
    except Exception as e:
        log.error(
            "サムネイル設定失敗: %s (%s, %.2fMB) — %s",
            video_id, send_path.name, send_path.stat().st_size / 1024 / 1024, e,
        )


# resumable アップロード中に起きうる「再送すれば直る」ネットワーク例外。
# googleapiclient 内部の num_retries は HTTP エラーしか見ておらず、
# BrokenPipeError / ConnectionResetError / SSL の途中切断はそのまま
# 例外として上がってくる (ユーザー報告の「ブロークンパイプ」がこれ)。
def _retryable_upload_errors() -> tuple[type[BaseException], ...]:
    import http.client
    import socket
    import ssl

    errors: list[type[BaseException]] = [
        BrokenPipeError,
        ConnectionResetError,
        ConnectionAbortedError,
        TimeoutError,
        socket.timeout,
        ssl.SSLError,
        ssl.SSLEOFError,
        http.client.IncompleteRead,
        http.client.BadStatusLine,
        http.client.CannotSendRequest,
        http.client.ResponseNotReady,
        OSError,  # socket.error は OSError の別名。上記以外の I/O 断も拾う
    ]
    del socket  # 参照済み (socket.timeout は OSError の別名)
    try:
        import httplib2

        errors.append(httplib2.HttpLib2Error)
    except ImportError:
        pass
    return tuple(errors)


# 再送で直る見込みのある HTTP ステータス (Google の推奨リトライ対象)
_RETRIABLE_STATUS_CODES = {429, 500, 502, 503, 504}


# OSError のサブクラスだが再送しても直らないもの (ファイルが消えた等)。
# これらまで再送すると無駄に 10 分待ってから失敗することになる。
_NON_RETRIABLE_OS_ERRORS = (
    FileNotFoundError,
    PermissionError,
    IsADirectoryError,
    NotADirectoryError,
    FileExistsError,
)


def _is_retriable_http_error(exc: Exception) -> bool:
    """googleapiclient の HttpError が一過性かどうか。"""
    try:
        from googleapiclient.errors import HttpError
    except ImportError:
        return False
    if not isinstance(exc, HttpError):
        return False
    status = getattr(getattr(exc, "resp", None), "status", None)
    return status in _RETRIABLE_STATUS_CODES


def _upload_chunks(request, progress_callback=None, max_retries: int = 10) -> dict:
    """resumable アップロードを最後まで進める。切断したら同じセッションを再開する。

    `next_chunk()` は resumable セッションの続きから送るので、途中で切れても
    最初からやり直しにはならない。連続失敗が `max_retries` に達したときだけ
    諦めて例外を送出する。1 チャンクでも成功したら失敗カウンタは 0 に戻す
    (長い動画で散発的に切れるケースを通し切るため)。
    """
    import random
    import time

    retryable = _retryable_upload_errors()
    response = None
    consecutive_failures = 0

    while response is None:
        try:
            status, response = request.next_chunk()
            consecutive_failures = 0
            if status and progress_callback:
                progress_callback(int(status.progress() * 100), 100)
        except Exception as e:
            if isinstance(e, _NON_RETRIABLE_OS_ERRORS):
                raise
            if not (isinstance(e, retryable) or _is_retriable_http_error(e)):
                raise
            consecutive_failures += 1
            if consecutive_failures > max_retries:
                log.error(
                    "YouTubeアップロードが %d 回連続で失敗、中断: %s",
                    consecutive_failures, e,
                )
                raise
            # 指数バックオフ + ジッター (同時再送でサーバを叩き続けない)
            delay = min(2 ** consecutive_failures, 60) + random.uniform(0, 1)
            log.warning(
                "YouTubeアップロードが中断 (%d/%d): %s — %.1fs後に再開",
                consecutive_failures, max_retries, e, delay,
            )
            time.sleep(delay)

    return response


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

    from app.config import get as cfg_get

    chunk_mb = cfg_get("youtube_upload_chunk_mb") or 8
    media = MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        resumable=True,
        chunksize=int(chunk_mb) * 1024 * 1024,
    )

    request = service.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    log.info("YouTubeアップロード開始: %s (%dMBチャンク)", title, int(chunk_mb))
    response = _upload_chunks(
        request,
        progress_callback=progress_callback,
        max_retries=int(cfg_get("youtube_upload_max_retries") or 10),
    )

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
