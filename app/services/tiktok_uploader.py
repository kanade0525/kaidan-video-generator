"""TikTok Content Posting API uploader (Inbox / Draft mode).

Uploads videos to the authenticated user's TikTok Inbox so the user can review
and manually publish via the TikTok mobile app. Uses Login Kit OAuth (PKCE) for
authentication. The `video.publish` (Direct Post) scope is intentionally NOT
requested — review difficulty is much higher and we don't auto-publish.

References:
- Login Kit auth flow: https://developers.tiktok.com/doc/login-kit-web
- Content Posting API (Inbox): https://developers.tiktok.com/doc/content-posting-api-reference-upload-video
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from pathlib import Path

import requests

from app.utils.log import get_logger

log = get_logger("kaidan.tiktok")

# Endpoints — TikTok docs are emphatic that these are the canonical hosts.
AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
USER_INFO_URL = "https://open.tiktokapis.com/v2/user/info/"
INBOX_INIT_URL = "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"
STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"

# Scopes required for Inbox/Draft upload. `user.info.basic` is for the
# username display in the UI, `video.upload` is for the inbox upload itself.
# `video.publish` is NOT requested (Direct Post requires harder review).
SCOPES = ["user.info.basic", "video.upload"]

TOKEN_PATH = Path(os.environ.get("TIKTOK_TOKEN_PATH", "data/tiktok_token.json"))

# In-memory: state → code_verifier (PKCE pair). Removed after exchange.
_oauth_pending: dict[str, str] = {}


# ---------- credential helpers ----------

def _client_key() -> str:
    key = os.environ.get("TIKTOK_CLIENT_KEY", "")
    if not key:
        raise RuntimeError("TIKTOK_CLIENT_KEY が未設定です")
    return key


def _client_secret() -> str:
    sec = os.environ.get("TIKTOK_CLIENT_SECRET", "")
    if not sec:
        raise RuntimeError("TIKTOK_CLIENT_SECRET が未設定です")
    return sec


def is_configured() -> bool:
    """True if client credentials are present in env."""
    return bool(os.environ.get("TIKTOK_CLIENT_KEY")) and bool(
        os.environ.get("TIKTOK_CLIENT_SECRET"),
    )


def is_authenticated() -> bool:
    """True if we have a saved token (regardless of whether it's expired)."""
    return TOKEN_PATH.exists()


# ---------- PKCE / OAuth ----------

def _gen_pkce() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) per RFC 7636."""
    verifier = secrets.token_urlsafe(64)[:128]
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=").decode()
    )
    return verifier, challenge


def get_auth_url(redirect_uri: str) -> str:
    """Build the TikTok Login Kit consent URL.

    Stores the PKCE verifier keyed by `state` so the callback can use it.
    """
    state = secrets.token_urlsafe(16)
    verifier, challenge = _gen_pkce()
    _oauth_pending[state] = verifier

    params = {
        "client_key": _client_key(),
        "scope": ",".join(SCOPES),
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    qs = "&".join(f"{k}={requests.utils.quote(v, safe='')}" for k, v in params.items())
    return f"{AUTH_URL}?{qs}"


def exchange_code(code: str, redirect_uri: str, state: str) -> None:
    """Exchange auth code for access/refresh tokens, save to disk."""
    verifier = _oauth_pending.pop(state, None)
    if not verifier:
        raise RuntimeError("OAuth state が一致しません(セッション切れ?)")

    resp = requests.post(
        TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": _client_key(),
            "client_secret": _client_secret(),
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        },
        timeout=30,
    )
    payload = resp.json()
    if "access_token" not in payload:
        raise RuntimeError(f"TikTokトークン取得失敗: {payload}")

    payload["_obtained_at"] = int(time.time())
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    log.info("TikTok 認証成功、トークン保存: scope=%s", payload.get("scope"))


def _load_token() -> dict:
    if not TOKEN_PATH.exists():
        raise RuntimeError("TikTok 未認証。先に /tiktok/auth で認証してください。")
    return json.loads(TOKEN_PATH.read_text())


def _refresh_if_needed(tok: dict) -> dict:
    """Refresh the access token if it's near expiry. Returns updated token dict."""
    obtained = tok.get("_obtained_at", 0)
    expires_in = tok.get("expires_in", 0)
    # Refresh 5 min before actual expiry
    if obtained + expires_in - 300 > time.time():
        return tok

    refresh_token = tok.get("refresh_token")
    if not refresh_token:
        raise RuntimeError("リフレッシュトークン無し、再認証が必要")

    log.info("TikTok アクセストークンをリフレッシュ中")
    resp = requests.post(
        TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": _client_key(),
            "client_secret": _client_secret(),
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=30,
    )
    payload = resp.json()
    if "access_token" not in payload:
        raise RuntimeError(f"TikTok リフレッシュ失敗: {payload}")
    payload["_obtained_at"] = int(time.time())
    TOKEN_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


def _access_token() -> str:
    tok = _refresh_if_needed(_load_token())
    return tok["access_token"]


# ---------- API calls ----------

def get_user_info() -> dict:
    """Fetch the authenticated user's basic info (for UI display)."""
    resp = requests.get(
        USER_INFO_URL,
        headers={"Authorization": f"Bearer {_access_token()}"},
        params={"fields": "open_id,union_id,display_name,avatar_url"},
        timeout=15,
    )
    return resp.json().get("data", {}).get("user", {})


def upload_video_to_inbox(video_path: str | Path, progress_callback=None) -> dict:
    """Upload a video to the authenticated user's TikTok Inbox (drafts).

    The user must then open the TikTok mobile app and manually publish
    the draft. This avoids the harder `video.publish` scope review.

    Returns:
        dict with `publish_id` and `status` (e.g. "PROCESSING_UPLOAD").
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"動画が見つかりません: {video_path}")

    video_size = video_path.stat().st_size
    if video_size == 0:
        raise RuntimeError("動画ファイルが空です")

    # TikTok requires chunk_size in [5MB, 64MB]. For single-chunk upload the
    # whole file goes as one chunk (when size ≤ 64MB). Shorts are typically
    # well under this. Multi-chunk is added later if needed.
    if video_size > 64 * 1024 * 1024:
        raise RuntimeError(
            f"動画サイズが64MBを超えています ({video_size} bytes)。"
            "現在の実装は単一チャンクアップロードのみ対応。",
        )

    chunk_size = video_size
    total_chunk_count = 1

    log.info(
        "TikTok Inbox アップロード開始: %s (%d bytes)", video_path.name, video_size,
    )
    if progress_callback:
        progress_callback(0, 3)

    # Step 1: init upload
    init_resp = requests.post(
        INBOX_INIT_URL,
        headers={
            "Authorization": f"Bearer {_access_token()}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        json={
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": video_size,
                "chunk_size": chunk_size,
                "total_chunk_count": total_chunk_count,
            },
        },
        timeout=30,
    )
    init_data = init_resp.json()
    if init_resp.status_code != 200 or "error" in init_data and init_data["error"].get("code") not in ("ok", None, ""):
        raise RuntimeError(f"TikTok Inbox init 失敗: {init_data}")
    data = init_data.get("data", {})
    publish_id = data.get("publish_id")
    upload_url = data.get("upload_url")
    if not publish_id or not upload_url:
        raise RuntimeError(f"TikTok init レスポンス不正: {init_data}")
    log.info("publish_id=%s, upload_url取得", publish_id)
    if progress_callback:
        progress_callback(1, 3)

    # Step 2: upload bytes via PUT to the returned URL
    with open(video_path, "rb") as f:
        body = f.read()
    put_resp = requests.put(
        upload_url,
        headers={
            "Content-Range": f"bytes 0-{video_size - 1}/{video_size}",
            "Content-Type": "video/mp4",
        },
        data=body,
        timeout=300,
    )
    if put_resp.status_code not in (200, 201):
        raise RuntimeError(
            f"TikTok 動画アップロード失敗: HTTP {put_resp.status_code} {put_resp.text[:200]}",
        )
    log.info("動画ファイルアップロード完了")
    if progress_callback:
        progress_callback(2, 3)

    # Step 3: optional status check (eventually consistent — first poll may say PROCESSING)
    status = "PROCESSING_UPLOAD"
    try:
        status_resp = requests.post(
            STATUS_URL,
            headers={
                "Authorization": f"Bearer {_access_token()}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json={"publish_id": publish_id},
            timeout=15,
        )
        status_data = status_resp.json()
        status = status_data.get("data", {}).get("status", status)
    except Exception as e:
        log.warning("ステータス取得に失敗 (動画自体は送信済み): %s", e)

    if progress_callback:
        progress_callback(3, 3)

    return {"publish_id": publish_id, "status": status}
