"""Tests for HHS scraper noise-stripping behaviour."""

from unittest.mock import patch

from app.services import scraper


class _FakeResponse:
    def __init__(self, html: bytes):
        self.content = html
        self.status_code = 200

    def raise_for_status(self) -> None:
        pass


def _with_html(html: str):
    def _get(url, timeout=30):  # noqa: ARG001
        return _FakeResponse(html.encode("utf-8"))
    return _get


class TestFetchStoryContent:
    def test_strips_youtube_embed_credits(self):
        """figure.wp-block-embed-youtube と figcaption は除去される。"""
        html = """
        <html><body>
        <div class="entry-content">
          <p>本文の最初です。</p>
          <p>本文の最後です、そう信じている。</p>
          <figure class="wp-block-embed-youtube">
            <iframe src="https://www.youtube.com/embed/xxx"></iframe>
            <figcaption>朗読: ゲーデルの不完全ラジオ</figcaption>
          </figure>
          <figure class="wp-block-embed-youtube">
            <iframe src="https://www.youtube.com/embed/yyy"></iframe>
            <figcaption>朗読: 怪談朗読と午前二時</figcaption>
          </figure>
        </div>
        </body></html>
        """
        with patch("app.services.scraper.requests.get", side_effect=_with_html(html)):
            result = scraper.fetch_story_content("http://example.test/p=1")
        assert "本文の最後です、そう信じている。" in result
        assert "朗読:" not in result
        assert "ゲーデルの不完全ラジオ" not in result

    def test_strips_iframe_and_script(self):
        html = """
        <html><body>
        <div class="entry-content">
          <p>本文テキスト十分長い。本文テキスト十分長い。本文テキスト十分長い。</p>
          <iframe src="http://tracker"></iframe>
          <script>alert('xss')</script>
        </div>
        </body></html>
        """
        with patch("app.services.scraper.requests.get", side_effect=_with_html(html)):
            result = scraper.fetch_story_content("http://example.test/p=2")
        assert "alert" not in result
        assert "tracker" not in result
        assert "本文テキスト" in result

    def test_preserves_plain_body_when_no_embeds(self):
        html = """
        <html><body>
        <div class="entry-content">
          <p>ただの怪談本文。ただの怪談本文。ただの怪談本文。</p>
        </div>
        </body></html>
        """
        with patch("app.services.scraper.requests.get", side_effect=_with_html(html)):
            result = scraper.fetch_story_content("http://example.test/p=3")
        assert "ただの怪談本文。" in result
