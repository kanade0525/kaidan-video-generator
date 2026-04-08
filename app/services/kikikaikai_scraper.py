from __future__ import annotations

import re
import time

import requests
from bs4 import BeautifulSoup

from app.config import get as cfg_get
from app.pipeline.retry import with_retry
from app.utils.log import get_logger

log = get_logger("kaidan.kikikaikai")

BASE_URL = "https://kikikaikai.kusuguru.co.jp"
TAGS_URL = f"{BASE_URL}/tags"


@with_retry(max_attempts=3, base_delay=2.0)
def fetch_tag_list() -> list[dict]:
    """Fetch all available tags from the tag index page."""
    r = requests.get(TAGS_URL, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.content, "html.parser")

    tags: list[dict] = []
    for link in soup.select(f'a[href^="{BASE_URL}/tags/"]'):
        href = link["href"]
        slug = href.rstrip("/").split("/tags/")[-1]
        if not slug or "/" in slug:
            continue
        name = link.get_text(strip=True)
        if name:
            tags.append({"slug": slug, "name": name, "url": href})

    # Deduplicate by slug
    seen: set[str] = set()
    unique: list[dict] = []
    for t in tags:
        if t["slug"] not in seen:
            seen.add(t["slug"])
            unique.append(t)
    return unique


_STORY_TEXT_RE = re.compile(
    r"^(?:\d+)?(?:短編|長編)(.+?)投稿者：(.+?)(\d+)$"
)


def _parse_story_link(link, href: str) -> dict | None:
    """Parse a story link element into a story dict.

    Handles both structured HTML (tag pages with <h3>) and flat text
    (category pages where JS-rendered DOM is flattened).
    """
    href = href.rstrip("/")
    match = re.search(r"/(\d+)/?$", href)
    if not match:
        return None
    story_id = match.group(1)

    # Normalize relative URLs
    if not href.startswith("http"):
        href = f"{BASE_URL}/{story_id}"

    # Try structured HTML first (tag pages)
    h3 = link.select_one("h3")
    if h3:
        title = h3.get_text(strip=True)
        author_el = link.select_one("p.author")
        author = ""
        if author_el:
            author = author_el.get_text(strip=True).replace("投稿者：", "")
        category_el = link.select_one("span.category")
        category = category_el.get_text(strip=True) if category_el else ""
        return {
            "url": href,
            "title": title,
            "author": author,
            "category": category,
            "story_id": story_id,
        }

    # Fallback: parse flat text (category pages)
    # Pattern: "短編タイトル投稿者：著者名数字" or "4短編タイトル投稿者：著者名数字"
    raw_text = link.get_text(strip=True)
    if not raw_text or "投稿者：" not in raw_text:
        return None

    m = _STORY_TEXT_RE.match(raw_text)
    if m:
        title = m.group(1).strip()
        author = m.group(2).strip()
        return {
            "url": href,
            "title": title,
            "author": author,
            "category": "短編",
            "story_id": story_id,
        }

    return None


@with_retry(max_attempts=3, base_delay=2.0)
def _fetch_listing_page(base_path: str, page: int = 1) -> tuple[list[dict], bool]:
    """Fetch a single page of stories from a listing URL.

    Args:
        base_path: Path relative to BASE_URL, e.g. "/tags/shinrei" or "/category/scary_story_s"
    """
    base_path = base_path.rstrip("/")
    if page == 1:
        url = f"{BASE_URL}{base_path}/"
    else:
        url = f"{BASE_URL}{base_path}/page/{page}/"

    r = requests.get(url, timeout=30)
    if r.status_code == 404:
        return [], False
    r.raise_for_status()
    soup = BeautifulSoup(r.content, "html.parser")

    stories: list[dict] = []
    seen_ids: set[str] = set()

    for link in soup.find_all("a", href=True):
        href = link["href"]
        # Normalize relative URLs
        if href.startswith("/") and not href.startswith("//"):
            href = BASE_URL + href

        story = _parse_story_link(link, href)
        if story and story["story_id"] not in seen_ids:
            seen_ids.add(story["story_id"])
            stories.append(story)

    # Check for next page
    next_page_pattern = f"{base_path}/page/{page + 1}"
    has_next = bool(soup.find("a", href=re.compile(re.escape(next_page_pattern))))

    return stories, has_next


def _fetch_pages(base_path: str, max_pages: int = 5, label: str = "") -> list[dict]:
    """Crawl listing pages and return story metadata list."""
    delay = cfg_get("shorts_scrape_delay")
    all_stories: list[dict] = []

    for page in range(1, max_pages + 1):
        stories, has_next = _fetch_listing_page(base_path, page)
        all_stories.extend(stories)
        log.info("[kikikaikai] %s page=%d: %d stories", label or base_path, page, len(stories))
        if not has_next:
            break
        if page < max_pages:
            time.sleep(delay)

    return all_stories


def fetch_stories_from_tag(tag: str, max_pages: int = 5) -> list[dict]:
    """Crawl tag pages and return story metadata list."""
    return _fetch_pages(f"/tags/{tag}", max_pages, label=f"tag={tag}")


def fetch_stories_from_category(category: str, max_pages: int = 5) -> list[dict]:
    """Crawl category pages and return story metadata list.

    Example: fetch_stories_from_category("scary_story_s") for short stories.
    """
    return _fetch_pages(f"/category/{category}", max_pages, label=f"category={category}")


@with_retry(max_attempts=3, base_delay=2.0)
def fetch_story_content(url: str) -> tuple[str, dict]:
    """Fetch full story text and metadata from an individual story page.

    Returns (text, metadata) where metadata includes author, tags, char_count.
    """
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.content, "html.parser")

    # Title
    h1 = soup.select_one("h1")
    title = h1.get_text(strip=True) if h1 else ""

    # Author
    author_link = soup.select_one('a[href*="member-info"]')
    author = ""
    if author_link:
        text = author_link.get_text(strip=True)
        author = text.replace("投稿者：", "").strip()
        # Remove trailing number in parentheses e.g. "s (47)" -> "s"
        if "(" in author:
            author = author[:author.rfind("(")].strip()

    # Tags
    tag_links = soup.select(f'a[href*="/tags/"]')
    tags = []
    for tl in tag_links:
        tag_text = tl.get_text(strip=True).lstrip("#")
        if tag_text:
            tags.append(tag_text)
    tags = list(dict.fromkeys(tags))  # Deduplicate preserving order

    # Story text: extract from div.main-text (the actual story body)
    text = ""
    main_text = soup.select_one("div.main-text")
    if main_text:
        for tag in main_text.find_all(["script", "style", "ins"]):
            tag.decompose()
        text = main_text.get_text("\n", strip=True)
    else:
        # Fallback: try broader selectors
        for selector in ["article", ".post-content", ".entry-content"]:
            elem = soup.select_one(selector)
            if elem and len(elem.get_text(strip=True)) > 50:
                for tag in elem.find_all(["script", "style", "ins"]):
                    tag.decompose()
                text = elem.get_text("\n", strip=True)
                break

    # Fallback: get all <p> tags
    if not text:
        paragraphs = []
        for p in soup.find_all("p"):
            p_text = p.get_text(strip=True)
            if len(p_text) > 10 and "googletag" not in p_text:
                paragraphs.append(p_text)
        text = "\n".join(paragraphs)

    char_count = len(text)

    metadata = {
        "title": title,
        "author": author,
        "tags": tags,
        "char_count": char_count,
    }

    return text, metadata
