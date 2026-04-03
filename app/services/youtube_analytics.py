"""YouTube Analytics API service for channel performance tracking."""

from __future__ import annotations

from datetime import date, timedelta

from app.utils.log import get_logger

log = get_logger("kaidan.analytics")


def _get_analytics_service():
    """Build YouTube Analytics API service using existing credentials."""
    from googleapiclient.discovery import build

    from app.services.youtube_uploader import _get_credentials

    creds = _get_credentials()
    return build("youtubeAnalytics", "v2", credentials=creds)


def _get_youtube_service():
    """Build YouTube Data API v3 service using existing credentials."""
    from googleapiclient.discovery import build

    from app.services.youtube_uploader import _get_credentials

    creds = _get_credentials()
    return build("youtube", "v3", credentials=creds)


def get_channel_info() -> dict:
    """Get basic channel info: title, subscriber count, video count, view count."""
    yt = _get_youtube_service()
    resp = yt.channels().list(part="snippet,statistics", mine=True).execute()
    items = resp.get("items", [])
    if not items:
        return {}
    ch = items[0]
    stats = ch.get("statistics", {})
    return {
        "channel_id": ch["id"],
        "title": ch["snippet"]["title"],
        "thumbnail": ch["snippet"]["thumbnails"].get("default", {}).get("url", ""),
        "subscriber_count": int(stats.get("subscriberCount", 0)),
        "video_count": int(stats.get("videoCount", 0)),
        "view_count": int(stats.get("viewCount", 0)),
    }


def get_channel_analytics(
    start_date: date | None = None,
    end_date: date | None = None,
    metrics: str = "views,estimatedMinutesWatched,subscribersGained,subscribersLost,likes,comments,shares",
    dimensions: str = "day",
) -> list[dict]:
    """Fetch daily channel analytics for a date range.

    Returns list of dicts with date + metric values.
    """
    if end_date is None:
        end_date = date.today() - timedelta(days=1)
    if start_date is None:
        start_date = end_date - timedelta(days=28)

    svc = _get_analytics_service()
    resp = svc.reports().query(
        ids="channel==MINE",
        startDate=start_date.isoformat(),
        endDate=end_date.isoformat(),
        metrics=metrics,
        dimensions=dimensions,
        sort="day",
    ).execute()

    headers = [h["name"] for h in resp.get("columnHeaders", [])]
    rows = resp.get("rows", [])
    return [dict(zip(headers, row)) for row in rows]


def get_video_analytics(
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[dict]:
    """Fetch per-video analytics: views, watch time, CTR, avg view duration.

    Returns list of dicts sorted by views descending.
    """
    if end_date is None:
        end_date = date.today() - timedelta(days=1)
    if start_date is None:
        start_date = end_date - timedelta(days=28)

    svc = _get_analytics_service()
    resp = svc.reports().query(
        ids="channel==MINE",
        startDate=start_date.isoformat(),
        endDate=end_date.isoformat(),
        metrics="views,estimatedMinutesWatched,averageViewDuration,likes,comments,shares",
        dimensions="video",
        sort="-views",
        maxResults=50,
    ).execute()

    headers = [h["name"] for h in resp.get("columnHeaders", [])]
    rows = resp.get("rows", [])
    video_stats = [dict(zip(headers, row)) for row in rows]

    # Enrich with video titles
    video_ids = [v["video"] for v in video_stats]
    titles = _get_video_titles(video_ids) if video_ids else {}
    for v in video_stats:
        v["title"] = titles.get(v["video"], v["video"])

    return video_stats


def _get_video_titles(video_ids: list[str]) -> dict[str, str]:
    """Batch fetch video titles from YouTube Data API."""
    yt = _get_youtube_service()
    titles = {}
    # API allows max 50 IDs per request
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i : i + 50]
        resp = yt.videos().list(
            part="snippet", id=",".join(chunk)
        ).execute()
        for item in resp.get("items", []):
            titles[item["id"]] = item["snippet"]["title"]
    return titles


def get_traffic_sources(
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[dict]:
    """Fetch traffic source breakdown."""
    if end_date is None:
        end_date = date.today() - timedelta(days=1)
    if start_date is None:
        start_date = end_date - timedelta(days=28)

    svc = _get_analytics_service()
    resp = svc.reports().query(
        ids="channel==MINE",
        startDate=start_date.isoformat(),
        endDate=end_date.isoformat(),
        metrics="views,estimatedMinutesWatched",
        dimensions="insightTrafficSourceType",
        sort="-views",
    ).execute()

    headers = [h["name"] for h in resp.get("columnHeaders", [])]
    rows = resp.get("rows", [])
    return [dict(zip(headers, row)) for row in rows]


TRAFFIC_SOURCE_LABELS = {
    "ADVERTISING": "広告",
    "ANNOTATION": "アノテーション",
    "CAMPAIGN_CARD": "カード",
    "END_SCREEN": "終了画面",
    "EXT_URL": "外部サイト",
    "HASHTAGS": "ハッシュタグ",
    "NOTIFICATION": "通知",
    "NO_LINK_EMBEDDED": "埋め込み",
    "NO_LINK_OTHER": "その他",
    "PLAYLIST": "プレイリスト",
    "PROMOTED": "プロモーション",
    "RELATED_VIDEO": "関連動画",
    "SHORTS": "ショート",
    "SUBSCRIBER": "チャンネル登録者",
    "YT_CHANNEL": "チャンネルページ",
    "YT_OTHER_PAGE": "その他YouTube",
    "YT_SEARCH": "YouTube検索",
}


def get_demographics(
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict:
    """Fetch viewer demographics (age group + gender)."""
    if end_date is None:
        end_date = date.today() - timedelta(days=1)
    if start_date is None:
        start_date = end_date - timedelta(days=28)

    svc = _get_analytics_service()
    resp = svc.reports().query(
        ids="channel==MINE",
        startDate=start_date.isoformat(),
        endDate=end_date.isoformat(),
        metrics="viewerPercentage",
        dimensions="ageGroup,gender",
        sort="ageGroup,gender",
    ).execute()

    headers = [h["name"] for h in resp.get("columnHeaders", [])]
    rows = resp.get("rows", [])
    return [dict(zip(headers, row)) for row in rows]


def get_geography(
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[dict]:
    """Fetch viewer geography (country)."""
    if end_date is None:
        end_date = date.today() - timedelta(days=1)
    if start_date is None:
        start_date = end_date - timedelta(days=28)

    svc = _get_analytics_service()
    resp = svc.reports().query(
        ids="channel==MINE",
        startDate=start_date.isoformat(),
        endDate=end_date.isoformat(),
        metrics="views,estimatedMinutesWatched",
        dimensions="country",
        sort="-views",
        maxResults=20,
    ).execute()

    headers = [h["name"] for h in resp.get("columnHeaders", [])]
    rows = resp.get("rows", [])
    return [dict(zip(headers, row)) for row in rows]
