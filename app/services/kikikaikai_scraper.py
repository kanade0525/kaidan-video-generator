from __future__ import annotations

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


@with_retry(max_attempts=3, base_delay=2.0)
def _fetch_tag_page(tag: str, page: int = 1) -> tuple[list[dict], bool]:
    """Fetch a single page of stories for a tag. Returns (stories, has_next_page)."""
    if page == 1:
        url = f"{BASE_URL}/tags/{tag}/"
    else:
        url = f"{BASE_URL}/tags/{tag}/page/{page}/"

    r = requests.get(url, timeout=30)
    if r.status_code == 404:
        return [], False
    r.raise_for_status()
    soup = BeautifulSoup(r.content, "html.parser")

    stories: list[dict] = []
    # Each story is an <a> linking to /{story_id} containing <h3> title
    for link in soup.select(f'a[href^="{BASE_URL}/"]'):
        href = link["href"].rstrip("/")
        # Story URLs are BASE_URL/{numeric_id}
        path = href.replace(BASE_URL, "").strip("/")
        if not path.isdigit():
            continue

        h3 = link.select_one("h3")
        if not h3:
            continue

        title = h3.get_text(strip=True)
        author_el = link.select_one("p.author")
        author = ""
        if author_el:
            author = author_el.get_text(strip=True).replace("投稿者：", "")

        category_el = link.select_one("span.category")
        category = category_el.get_text(strip=True) if category_el else ""

        stories.append({
            "url": href,
            "title": title,
            "author": author,
            "category": category,
            "story_id": path,
        })

    # Check for next page
    has_next = bool(soup.select(f'a[href*="/tags/{tag}/page/{page + 1}"]'))

    return stories, has_next


def fetch_stories_from_tag(tag: str, max_pages: int = 5) -> list[dict]:
    """Crawl tag pages and return story metadata list."""
    delay = cfg_get("shorts_scrape_delay")
    all_stories: list[dict] = []

    for page in range(1, max_pages + 1):
        stories, has_next = _fetch_tag_page(tag, page)
        all_stories.extend(stories)
        log.info("[kikikaikai] tag=%s page=%d: %d stories", tag, page, len(stories))
        if not has_next:
            break
        time.sleep(delay)

    return all_stories


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

    # Story text: extract from article/main content area
    # Try multiple selectors for the story body
    text = ""
    for selector in [
        "article",
        ".post-content",
        ".entry-content",
        ".story-content",
    ]:
        elem = soup.select_one(selector)
        if elem and len(elem.get_text(strip=True)) > 50:
            # Remove script/style tags
            for tag in elem.find_all(["script", "style", "ins"]):
                tag.decompose()
            text = elem.get_text("\n", strip=True)
            break

    # Fallback: get all <p> tags from main content
    if not text:
        paragraphs = []
        for p in soup.find_all("p"):
            p_text = p.get_text(strip=True)
            # Skip short non-content paragraphs
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
