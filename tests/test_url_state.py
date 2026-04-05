"""Tests for URL state preservation logic."""

from app.ui.url_state import build_query_string, build_results_url, build_stories_url


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
    def test_no_params(self):
        assert build_results_url() == "/results"

    def test_stage_only(self):
        url = build_results_url(stage="voice_generated")
        assert url == "/results?stage=voice_generated"

    def test_keyword_only(self):
        url = build_results_url(keyword="怪談")
        assert "/results?" in url
        assert "keyword=" in url

    def test_story_id_only(self):
        url = build_results_url(story_id=42)
        assert url == "/results?id=42"

    def test_all_params(self):
        url = build_results_url(stage="scraped", keyword="test", story_id=10)
        assert "stage=scraped" in url
        assert "keyword=test" in url
        assert "id=10" in url

    def test_empty_keyword_ignored(self):
        url = build_results_url(stage="scraped", keyword="")
        assert "keyword" not in url

    def test_zero_story_id_ignored(self):
        url = build_results_url(story_id=0)
        assert url == "/results"

    def test_none_story_id_ignored(self):
        url = build_results_url(story_id=None)
        assert url == "/results"

    def test_whitespace_keyword_ignored(self):
        url = build_results_url(keyword="   ")
        assert url == "/results"


class TestBuildStoriesUrl:
    def test_no_params(self):
        assert build_stories_url() == "/stories"

    def test_stage_only(self):
        url = build_stories_url(stage="pending")
        assert url == "/stories?stage=pending"

    def test_category_only(self):
        url = build_stories_url(category="怪談")
        assert "category=" in url

    def test_page_only(self):
        url = build_stories_url(page=2)
        assert url == "/stories?page=2"

    def test_page_zero_ignored(self):
        url = build_stories_url(page=0)
        assert url == "/stories"

    def test_all_params(self):
        url = build_stories_url(stage="scraped", category="怪談", page=3)
        assert "stage=scraped" in url
        assert "category=" in url
        assert "page=3" in url
