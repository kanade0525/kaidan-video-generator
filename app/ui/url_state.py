"""URL query parameter helpers for preserving UI filter state across reloads."""

from __future__ import annotations

from urllib.parse import quote


def build_query_string(params: dict[str, str | int | None]) -> str:
    """Build a URL query string from non-empty params.

    >>> build_query_string({"stage": "voice_generated", "keyword": ""})
    'stage=voice_generated'
    >>> build_query_string({})
    ''
    """
    filtered = {k: str(v) for k, v in params.items() if v is not None and str(v).strip()}
    return "&".join(f"{k}={quote(str(v))}" for k, v in filtered.items())


def build_results_url(stage: str = "", keyword: str = "", story_id: int | None = None) -> str:
    """Build /results URL with current filter state."""
    params: dict[str, str | int | None] = {}
    if stage:
        params["stage"] = stage
    if keyword and keyword.strip():
        params["keyword"] = keyword.strip()
    if story_id:
        params["id"] = story_id
    qs = build_query_string(params)
    return f"/results?{qs}" if qs else "/results"


def build_stories_url(stage: str = "", category: str = "", page: int = 0) -> str:
    """Build /stories URL with current filter state."""
    params: dict[str, str | int | None] = {}
    if stage:
        params["stage"] = stage
    if category:
        params["category"] = category
    if page > 0:
        params["page"] = page
    qs = build_query_string(params)
    return f"/stories?{qs}" if qs else "/stories"
