"""Tests for config module."""

from unittest.mock import patch

from app.config import _DEFAULTS, load_config, save_config


class TestLoadConfig:
    def test_returns_defaults_when_no_file(self, tmp_path):
        fake_path = tmp_path / "nonexistent.toml"
        with patch("app.config.CONFIG_PATH", fake_path):
            config = load_config()
        assert config == _DEFAULTS

    def test_user_config_overrides_defaults(self, tmp_path):
        config_path = tmp_path / "config.toml"
        config_path.write_text('speaker_id = 99\nspeed = 1.5\n')
        with patch("app.config.CONFIG_PATH", config_path):
            config = load_config()
        assert config["speaker_id"] == 99
        assert config["speed"] == 1.5
        # Other defaults preserved
        assert config["fps"] == _DEFAULTS["fps"]

    def test_defaults_has_expected_keys(self):
        required = ["speaker_id", "fps", "bgm_path", "youtube_tags"]
        for key in required:
            assert key in _DEFAULTS


class TestSaveConfig:
    def test_round_trip(self, tmp_path):
        config_path = tmp_path / "data" / "config.toml"
        with patch("app.config.CONFIG_PATH", config_path):
            save_config({"speaker_id": 42, "speed": 1.2, "bgm_path": "test.mp3"})
            config = load_config()
        assert config["speaker_id"] == 42
        assert config["speed"] == 1.2
        assert config["bgm_path"] == "test.mp3"

    def test_saves_bool(self, tmp_path):
        config_path = tmp_path / "data" / "config.toml"
        with patch("app.config.CONFIG_PATH", config_path):
            save_config({"image_enhance_prompt": True})
            config = load_config()
        assert config["image_enhance_prompt"] is True

    def test_saves_multiline_string(self, tmp_path):
        config_path = tmp_path / "data" / "config.toml"
        text = "line1\nline2\nline3"
        with patch("app.config.CONFIG_PATH", config_path):
            save_config({"text_prompt": text})
            config = load_config()
        assert config["text_prompt"] == text

    def test_creates_parent_dir(self, tmp_path):
        config_path = tmp_path / "deep" / "nested" / "config.toml"
        with patch("app.config.CONFIG_PATH", config_path):
            save_config({"fps": 30})
        assert config_path.exists()

    def test_saves_list(self, tmp_path):
        config_path = tmp_path / "data" / "config.toml"
        with patch("app.config.CONFIG_PATH", config_path):
            save_config({"keep_as_kanji": ["母", "葉"]})
            config = load_config()
        assert config["keep_as_kanji"] == ["母", "葉"]

    def test_saves_dict(self, tmp_path):
        config_path = tmp_path / "data" / "config.toml"
        with patch("app.config.CONFIG_PATH", config_path):
            save_config({"reading_overrides": {"私": "わたし", "所々": "ところどころ"}})
            config = load_config()
        assert config["reading_overrides"] == {"私": "わたし", "所々": "ところどころ"}

    def test_saves_dict_with_quotes(self, tmp_path):
        config_path = tmp_path / "data" / "config.toml"
        with patch("app.config.CONFIG_PATH", config_path):
            save_config({"reading_overrides": {'a"b': 'c"d'}})
            config = load_config()
        assert config["reading_overrides"] == {'a"b': 'c"d'}
