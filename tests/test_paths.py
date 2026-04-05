"""Tests for path utility functions."""

from unittest.mock import patch

from app.utils.paths import safe_dirname


class TestSafeDirname:
    def test_normal_title(self):
        assert safe_dirname("テスト怪談") == "テスト怪談"

    def test_removes_special_chars(self):
        result = safe_dirname("怪談/test?story*")
        assert "/" not in result
        assert "?" not in result
        assert "*" not in result

    def test_spaces_to_underscores(self):
        assert safe_dirname("hello world") == "hello_world"

    def test_truncates_long_name(self):
        long_name = "あ" * 100
        result = safe_dirname(long_name, max_len=50)
        assert len(result) <= 50

    def test_empty_string_returns_untitled(self):
        assert safe_dirname("") == "untitled"

    def test_special_chars_only_returns_untitled(self):
        assert safe_dirname("!@#$%^&*()") == "untitled"

    def test_unicode_preserved(self):
        result = safe_dirname("ＵＦＯに関する記録")
        assert "ＵＦＯ" in result

    def test_strips_whitespace(self):
        assert safe_dirname("  test  ") == "test"

    def test_custom_max_len(self):
        result = safe_dirname("abcdefghij", max_len=5)
        assert len(result) == 5
