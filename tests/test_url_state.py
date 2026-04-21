"""Tests for URL state preservation logic."""

from app.ui.url_state import (
    build_query_string,
    build_results_url,
    build_stories_url,
    resolve_initial_story,
)


class TestBuildQueryString:
    def test_empty_params(self):
        assert build_query_string({}) == ""

    def test_filters_none(self):
        assert build_query_string({"stage": None, "keyword": None}) == ""

    def test_filters_empty_string(self):
        assert build_query_string({"stage": "", "keyword": ""}) == ""

    def test_filters_whitespace_only(self):
        assert build_query_string({"keyword": "  "}) == ""

    def test_single_param(self):
        assert build_query_string({"stage": "voice_generated"}) == "stage=voice_generated"

    def test_multiple_params(self):
        result = build_query_string({"stage": "scraped", "keyword": "test"})
        assert "stage=scraped" in result
        assert "keyword=test" in result

    def test_mixed_empty_and_set(self):
        result = build_query_string({"stage": "scraped", "keyword": "", "id": None})
        assert result == "stage=scraped"

    def test_int_value(self):
        assert build_query_string({"page": 3}) == "page=3"

    def test_zero_int_kept(self):
        # build_query_string keeps 0 — callers (build_stories_url) decide whether to pass it
        assert build_query_string({"page": 0}) == "page=0"

    def test_encodes_special_chars(self):
        result = build_query_string({"keyword": "怪談 test"})
        assert "keyword=" in result
        assert " " not in result.split("=", 1)[1]  # space should be encoded


class TestBuildResultsUrl:
    """Stage is intentionally excluded from URL to avoid stale filter after processing."""

    def test_no_params(self):
        assert build_results_url() == "/results"

    def test_keyword_only(self):
        url = build_results_url(keyword="怪談")
        assert "/results?" in url
        assert "keyword=" in url

    def test_story_id_only(self):
        url = build_results_url(story_id=42)
        assert url == "/results?id=42"

    def test_keyword_and_story_id(self):
        url = build_results_url(keyword="test", story_id=10)
        assert "keyword=test" in url
        assert "id=10" in url
        assert "stage" not in url

    def test_empty_keyword_ignored(self):
        url = build_results_url(keyword="")
        assert url == "/results"

    def test_zero_story_id_ignored(self):
        url = build_results_url(story_id=0)
        assert url == "/results"

    def test_none_story_id_ignored(self):
        url = build_results_url(story_id=None)
        assert url == "/results"

    def test_whitespace_keyword_ignored(self):
        url = build_results_url(keyword="   ")
        assert url == "/results"

    def test_category_only(self):
        url = build_results_url(category="怪談")
        assert "/results?" in url
        assert "category=" in url

    def test_all_params(self):
        url = build_results_url(keyword="test", story_id=10, category="人怖")
        assert "keyword=test" in url
        assert "id=10" in url
        assert "category=" in url

    def test_empty_category_ignored(self):
        url = build_results_url(category="")
        assert url == "/results"


class TestResolveInitialStory:
    """Story selection must survive page reload via URL ?id= param."""

    def test_id_in_options_returns_id(self):
        options = {10: "Story A", 20: "Story B", 30: "Story C"}
        assert resolve_initial_story(20, options) == 20

    def test_id_not_in_options_returns_none(self):
        options = {10: "Story A", 20: "Story B"}
        assert resolve_initial_story(99, options) is None

    def test_zero_id_returns_none(self):
        options = {10: "Story A"}
        assert resolve_initial_story(0, options) is None

    def test_none_id_returns_none(self):
        options = {10: "Story A"}
        assert resolve_initial_story(None, options) is None

    def test_empty_options_returns_none(self):
        assert resolve_initial_story(10, {}) is None

    def test_first_item_in_options(self):
        options = {1: "First"}
        assert resolve_initial_story(1, options) == 1


class TestBuildStoriesUrl:
    """Stage is intentionally excluded from URL to avoid stale filter after processing."""

    def test_no_params(self):
        assert build_stories_url() == "/stories"

    def test_category_only(self):
        url = build_stories_url(category="怪談")
        assert "category=" in url
        assert "stage" not in url

    def test_page_only(self):
        url = build_stories_url(page=2)
        assert url == "/stories?page=2"

    def test_page_zero_ignored(self):
        url = build_stories_url(page=0)
        assert url == "/stories"

    def test_category_and_page(self):
        url = build_stories_url(category="怪談", page=3)
        assert "category=" in url
        assert "page=3" in url
        assert "stage" not in url
