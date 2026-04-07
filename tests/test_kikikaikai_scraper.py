"""Tests for kikikaikai scraper (unit tests with mocked HTTP)."""

from unittest.mock import MagicMock, patch

import pytest

from app.services import kikikaikai_scraper


SAMPLE_TAG_PAGE_HTML = """
<html><body>
<a href="https://kikikaikai.kusuguru.co.jp/12345">
    <span class="category">短編</span>
    <h3>テスト怪談その1</h3>
    <p class="author">投稿者：太郎</p>
</a>
<a href="https://kikikaikai.kusuguru.co.jp/12346">
    <span class="category">長編</span>
    <h3>テスト怪談その2</h3>
    <p class="author">投稿者：花子</p>
</a>
<a href="https://kikikaikai.kusuguru.co.jp/tags/shinrei/page/2">2</a>
</body></html>
"""

SAMPLE_TAG_PAGE_LAST_HTML = """
<html><body>
<a href="https://kikikaikai.kusuguru.co.jp/12347">
    <span class="category">短編</span>
    <h3>最後の話</h3>
    <p class="author">投稿者：次郎</p>
</a>
</body></html>
"""

SAMPLE_STORY_HTML = """
<html><body>
<h1>テスト怪談その1</h1>
<a href="/member-info?user=1234">投稿者：太郎 (42)</a>
<a href="/tags/shinrei">心霊</a>
<a href="/tags/obon">お盆</a>
<article>
  <p>これはテスト用の怪談です。ある日のこと、私は古い家に住んでいました。</p>
  <p>その家では毎晩不思議なことが起きていたのです。</p>
</article>
</body></html>
"""

SAMPLE_TAGS_HTML = """
<html><body>
<h3>心霊</h3>
<a href="https://kikikaikai.kusuguru.co.jp/tags/shinrei">心霊</a>
<a href="https://kikikaikai.kusuguru.co.jp/tags/obon">お盆</a>
<h3>場所</h3>
<a href="https://kikikaikai.kusuguru.co.jp/tags/school">学校</a>
</body></html>
"""


def _mock_response(html: str, status_code: int = 200):
    """Create a mock requests response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = html.encode("utf-8")
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    return resp


class TestFetchTagList:
    @patch("app.services.kikikaikai_scraper.requests.get")
    def test_fetch_tag_list(self, mock_get):
        mock_get.return_value = _mock_response(SAMPLE_TAGS_HTML)

        tags = kikikaikai_scraper.fetch_tag_list()
        assert len(tags) >= 3
        slugs = [t["slug"] for t in tags]
        assert "shinrei" in slugs
        assert "obon" in slugs
        assert "school" in slugs

    @patch("app.services.kikikaikai_scraper.requests.get")
    def test_deduplicates(self, mock_get):
        html = """
        <html><body>
        <a href="https://kikikaikai.kusuguru.co.jp/tags/shinrei">心霊</a>
        <a href="https://kikikaikai.kusuguru.co.jp/tags/shinrei">心霊</a>
        </body></html>
        """
        mock_get.return_value = _mock_response(html)
        tags = kikikaikai_scraper.fetch_tag_list()
        slugs = [t["slug"] for t in tags]
        assert slugs.count("shinrei") == 1


class TestFetchTagPage:
    @patch("app.services.kikikaikai_scraper.requests.get")
    def test_extracts_stories(self, mock_get):
        mock_get.return_value = _mock_response(SAMPLE_TAG_PAGE_HTML)

        stories, has_next = kikikaikai_scraper._fetch_tag_page("shinrei", page=1)
        assert len(stories) == 2
        assert stories[0]["title"] == "テスト怪談その1"
        assert stories[0]["author"] == "太郎"
        assert stories[0]["story_id"] == "12345"
        assert stories[1]["title"] == "テスト怪談その2"

    @patch("app.services.kikikaikai_scraper.requests.get")
    def test_has_next_page(self, mock_get):
        mock_get.return_value = _mock_response(SAMPLE_TAG_PAGE_HTML)
        _, has_next = kikikaikai_scraper._fetch_tag_page("shinrei", page=1)
        assert has_next is True

    @patch("app.services.kikikaikai_scraper.requests.get")
    def test_no_next_page(self, mock_get):
        mock_get.return_value = _mock_response(SAMPLE_TAG_PAGE_LAST_HTML)
        stories, has_next = kikikaikai_scraper._fetch_tag_page("shinrei", page=2)
        assert len(stories) == 1
        assert has_next is False

    @patch("app.services.kikikaikai_scraper.requests.get")
    def test_404_returns_empty(self, mock_get):
        resp = _mock_response("", status_code=404)
        resp.status_code = 404
        resp.raise_for_status = MagicMock()
        mock_get.return_value = resp
        stories, has_next = kikikaikai_scraper._fetch_tag_page("nonexistent", page=1)
        assert stories == []
        assert has_next is False


class TestFetchStoryContent:
    @patch("app.services.kikikaikai_scraper.requests.get")
    def test_extracts_text(self, mock_get):
        mock_get.return_value = _mock_response(SAMPLE_STORY_HTML)

        text, metadata = kikikaikai_scraper.fetch_story_content(
            "https://kikikaikai.kusuguru.co.jp/12345"
        )
        assert "テスト用の怪談" in text
        assert "不思議なこと" in text

    @patch("app.services.kikikaikai_scraper.requests.get")
    def test_extracts_author(self, mock_get):
        mock_get.return_value = _mock_response(SAMPLE_STORY_HTML)

        _, metadata = kikikaikai_scraper.fetch_story_content(
            "https://kikikaikai.kusuguru.co.jp/12345"
        )
        assert metadata["author"] == "太郎"

    @patch("app.services.kikikaikai_scraper.requests.get")
    def test_extracts_tags(self, mock_get):
        mock_get.return_value = _mock_response(SAMPLE_STORY_HTML)

        _, metadata = kikikaikai_scraper.fetch_story_content(
            "https://kikikaikai.kusuguru.co.jp/12345"
        )
        assert "心霊" in metadata["tags"]
        assert "お盆" in metadata["tags"]

    @patch("app.services.kikikaikai_scraper.requests.get")
    def test_char_count(self, mock_get):
        mock_get.return_value = _mock_response(SAMPLE_STORY_HTML)

        text, metadata = kikikaikai_scraper.fetch_story_content(
            "https://kikikaikai.kusuguru.co.jp/12345"
        )
        assert metadata["char_count"] == len(text)
        assert metadata["char_count"] > 0


class TestFetchStoriesFromTag:
    @patch("app.services.kikikaikai_scraper._fetch_tag_page")
    @patch("app.services.kikikaikai_scraper.cfg_get", return_value=0)
    def test_single_page(self, mock_cfg, mock_fetch):
        mock_fetch.return_value = ([{"title": "Test", "url": "https://a.com"}], False)

        stories = kikikaikai_scraper.fetch_stories_from_tag("shinrei", max_pages=5)
        assert len(stories) == 1
        mock_fetch.assert_called_once()

    @patch("app.services.kikikaikai_scraper._fetch_tag_page")
    @patch("app.services.kikikaikai_scraper.cfg_get", return_value=0)
    def test_multi_page(self, mock_cfg, mock_fetch):
        mock_fetch.side_effect = [
            ([{"title": f"Story{i}"} for i in range(10)], True),
            ([{"title": f"Story{i}"} for i in range(10, 15)], False),
        ]

        stories = kikikaikai_scraper.fetch_stories_from_tag("shinrei", max_pages=5)
        assert len(stories) == 15
        assert mock_fetch.call_count == 2

    @patch("app.services.kikikaikai_scraper._fetch_tag_page")
    @patch("app.services.kikikaikai_scraper.cfg_get", return_value=0)
    def test_respects_max_pages(self, mock_cfg, mock_fetch):
        mock_fetch.return_value = ([{"title": "Story"}], True)

        stories = kikikaikai_scraper.fetch_stories_from_tag("shinrei", max_pages=2)
        assert mock_fetch.call_count == 2
