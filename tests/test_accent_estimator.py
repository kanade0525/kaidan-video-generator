"""Tests for accent_estimator module (Issue #31)."""

from unittest.mock import patch

import pytest

from app.services.accent_estimator import apply_accent_override, estimate_phrases


def _pyopenjtalk_available() -> bool:
    try:
        import pyopenjtalk  # noqa: F401
    except ImportError:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _pyopenjtalk_available(), reason="pyopenjtalk not installed"
)


class TestEstimatePhrases:
    def test_returns_list_of_tuples(self):
        result = estimate_phrases("3人で話し合いを続ける")
        assert result is not None
        assert isinstance(result, list)
        for phrase in result:
            assert isinstance(phrase, tuple)
            assert len(phrase) == 2
            mora_count, accent = phrase
            assert isinstance(mora_count, int)
            assert isinstance(accent, int)
            assert 0 <= accent <= mora_count

    def test_empty_text_returns_empty_or_none(self):
        result = estimate_phrases("")
        # Either empty list or None is acceptable
        assert result is None or result == []

    def test_mora_counts_sum_matches_text(self):
        """Total mora count across phrases should be reasonable."""
        text = "話し合い"
        result = estimate_phrases(text)
        if result:
            total = sum(m for m, _ in result)
            # 話し合い = 5 moras (ハナシアイ)
            assert total == 5


class TestApplyAccentOverride:
    def test_override_when_mora_matches(self):
        query = {
            "accent_phrases": [
                {"moras": [{"text": "ハ"}, {"text": "ナ"}, {"text": "シ"}], "accent": 3},
            ]
        }
        # Mock estimator to return different accent with matching mora count
        with patch("app.services.accent_estimator.estimate_phrases") as mock:
            mock.return_value = [(3, 1)]
            result = apply_accent_override(query, "話")
        assert result["accent_phrases"][0]["accent"] == 1

    def test_skip_when_mora_mismatch(self):
        query = {
            "accent_phrases": [
                {"moras": [{"text": "ハ"}, {"text": "ナ"}, {"text": "シ"}], "accent": 3},
            ]
        }
        # Estimator says 5 moras but VOICEVOX has 3 → skip override
        with patch("app.services.accent_estimator.estimate_phrases") as mock:
            mock.return_value = [(5, 2)]
            result = apply_accent_override(query, "話")
        assert result["accent_phrases"][0]["accent"] == 3  # unchanged

    def test_no_estimates_returns_unchanged(self):
        query = {"accent_phrases": [{"moras": [{"text": "ア"}], "accent": 0}]}
        with patch("app.services.accent_estimator.estimate_phrases") as mock:
            mock.return_value = None
            result = apply_accent_override(query, "a")
        assert result == query

    def test_empty_query(self):
        """apply_accent_override on empty query returns safely."""
        query = {"accent_phrases": []}
        result = apply_accent_override(query, "")
        assert result == query


class TestIntegrationSmoke:
    """End-to-end smoke test: run real estimate_phrases on problem sentences."""

    def test_basic_sentence(self):
        result = estimate_phrases("3人で話し合いを続ける")
        # Should get at least one phrase back
        assert result is not None
        assert len(result) >= 1

    def test_marine_fallback_to_openjtalk(self):
        """Even if marine is unavailable, openjtalk fallback should work."""
        result = estimate_phrases("話し合い")
        assert result is not None
        assert len(result) >= 1
