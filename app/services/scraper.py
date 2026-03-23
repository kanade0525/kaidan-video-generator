from __future__ import annotations

import requests
from bs4 import BeautifulSoup

from app.pipeline.retry import with_retry
from app.utils.log import get_logger

log = get_logger("kaidan.scraper")

BASE_URL = "https://hhs.parasite.jp/hhslibrary"
RSS_URL = f"{BASE_URL}/?feed=rss2"
REST_API = f"{BASE_URL}/?rest_route=/wp/v2"

CONTENT_SELECTORS = [
    "div.entry-content",
    "div.story-content",
    "div.content",
    "article",
]


@with_retry(max_attempts=3, base_delay=2.0)
def fetch_story_content(url: str) -> str:
    """Fetch story text content from a URL."""
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.content, "html.parser")

    for selector in CONTENT_SELECTORS:
        elem = soup.select_one(selector)
        if elem and len(elem.get_text(strip=True)) > 50:
            return elem.get_text("\n", strip=True)

    body = soup.find("body")
    return body.get_text("\n", strip=True) if body else ""


@with_retry(max_attempts=3, base_delay=2.0)
def fetch_rss_stories() -> list[dict]:
    """Fetch latest stories from RSS feed."""
    r = requests.get(RSS_URL, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.content, "xml")
    items = []
    for item in soup.find_all("item"):
        items.append({
            "url": item.link.text.strip(),
            "title": item.title.text.strip(),
            "pub_date": item.pubDate.text.strip() if item.pubDate else "",
        })
    return items


@with_retry(max_attempts=3, base_delay=2.0)
def fetch_all_stories_rest(page: int = 1, per_page: int = 100) -> tuple[list[dict], int]:
    """Fetch stories via WP REST API. Returns (stories, total_pages)."""
    r = requests.get(
        f"{REST_API}/posts&per_page={per_page}&page={page}", timeout=30
    )
    r.raise_for_status()
    total_pages = int(r.headers.get("X-WP-TotalPages", 1))

    # Get category map
    cats_r = requests.get(f"{REST_API}/categories&per_page=100", timeout=30)
    cat_map = {}
    if cats_r.status_code == 200:
        cat_map = {c["id"]: c["name"] for c in cats_r.json()}

    stories = []
    for p in r.json():
        stories.append({
            "url": p["link"],
            "title": p["title"]["rendered"],
            "pub_date": p.get("date", ""),
            "categories": [cat_map.get(c, str(c)) for c in p.get("categories", [])],
        })
    return stories, total_pages
