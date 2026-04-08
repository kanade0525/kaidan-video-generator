"""Tests for shorts config keys."""

from app.config import _DEFAULTS


class TestShortsConfigDefaults:
    def test_shorts_leading_silence(self):
        assert _DEFAULTS["shorts_leading_silence"] == 0.5

    def test_shorts_trailing_silence(self):
        assert _DEFAULTS["shorts_trailing_silence"] == 0.5

    def test_shorts_num_scenes(self):
        assert _DEFAULTS["shorts_num_scenes"] == 2

    def test_shorts_image_size(self):
        assert _DEFAULTS["shorts_image_size"] == "1024x1792"

    def test_shorts_image_aspect_ratio(self):
        assert _DEFAULTS["shorts_image_aspect_ratio"] == "9:16"

    def test_shorts_max_char_count(self):
        assert _DEFAULTS["shorts_max_char_count"] == 880

    def test_shorts_target_char_count(self):
        assert _DEFAULTS["shorts_target_char_count"] == 440

    def test_shorts_vhs_enabled(self):
        assert _DEFAULTS["shorts_vhs_enabled"] is True

    def test_shorts_youtube_title_template_has_title_placeholder(self):
        assert "{title}" in _DEFAULTS["shorts_youtube_title_template"]

    def test_shorts_youtube_description_has_title(self):
        assert "{title}" in _DEFAULTS["shorts_youtube_description_template"]

    def test_shorts_youtube_description_has_hashtags(self):
        assert "#Shorts" in _DEFAULTS["shorts_youtube_description_template"]

    def test_shorts_speed(self):
        assert _DEFAULTS["shorts_speed"] == 1.15

    def test_shorts_youtube_tags(self):
        tags = _DEFAULTS["shorts_youtube_tags"]
        assert "Shorts" in tags
        assert "怪談" in tags
