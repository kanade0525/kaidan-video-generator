"""Tests for shorts-related path utilities."""

from pathlib import Path
from unittest.mock import patch

from app.utils.paths import (
    OUTPUT_BASE,
    audio_dir,
    chunks_path,
    images_dir,
    meta_path,
    narration_path,
    processed_text_path,
    raw_content_path,
    safe_dirname,
    story_dir,
    video_path,
)


class TestStoryDirContentType:
    def test_long_default(self, tmp_path):
        with patch("app.utils.paths.OUTPUT_BASE", tmp_path):
            d = story_dir("テスト")
            assert "shorts" not in str(d)
            assert d.exists()

    def test_short_uses_shorts_subdir(self, tmp_path):
        with patch("app.utils.paths.OUTPUT_BASE", tmp_path):
            d = story_dir("テスト", content_type="short")
            assert "shorts" in str(d)
            assert d.exists()
            assert d == tmp_path / "shorts" / safe_dirname("テスト")

    def test_long_explicit(self, tmp_path):
        with patch("app.utils.paths.OUTPUT_BASE", tmp_path):
            d = story_dir("テスト", content_type="long")
            assert "shorts" not in str(d)


class TestPathFunctionsContentType:
    """Verify that all path functions propagate content_type correctly."""

    def test_raw_content_path_short(self, tmp_path):
        with patch("app.utils.paths.OUTPUT_BASE", tmp_path):
            p = raw_content_path("test", content_type="short")
            assert "shorts" in str(p)
            assert p.name == "raw_content.txt"

    def test_meta_path_short(self, tmp_path):
        with patch("app.utils.paths.OUTPUT_BASE", tmp_path):
            p = meta_path("test", content_type="short")
            assert "shorts" in str(p)
            assert p.name == "meta.json"

    def test_processed_text_path_short(self, tmp_path):
        with patch("app.utils.paths.OUTPUT_BASE", tmp_path):
            p = processed_text_path("test", content_type="short")
            assert "shorts" in str(p)

    def test_chunks_path_short(self, tmp_path):
        with patch("app.utils.paths.OUTPUT_BASE", tmp_path):
            p = chunks_path("test", content_type="short")
            assert "shorts" in str(p)

    def test_audio_dir_short(self, tmp_path):
        with patch("app.utils.paths.OUTPUT_BASE", tmp_path):
            d = audio_dir("test", content_type="short")
            assert "shorts" in str(d)
            assert d.exists()

    def test_narration_path_short(self, tmp_path):
        with patch("app.utils.paths.OUTPUT_BASE", tmp_path):
            p = narration_path("test", content_type="short")
            assert "shorts" in str(p)

    def test_images_dir_short(self, tmp_path):
        with patch("app.utils.paths.OUTPUT_BASE", tmp_path):
            d = images_dir("test", content_type="short")
            assert "shorts" in str(d)
            assert d.exists()

    def test_video_path_short(self, tmp_path):
        with patch("app.utils.paths.OUTPUT_BASE", tmp_path):
            p = video_path("test", content_type="short")
            assert "shorts" in str(p)
            assert p.suffix == ".mp4"

    def test_long_paths_unchanged(self, tmp_path):
        with patch("app.utils.paths.OUTPUT_BASE", tmp_path):
            p = raw_content_path("test", content_type="long")
            assert "shorts" not in str(p)


class TestShortLongIsolation:
    """Ensure short and long stories have completely separate output dirs."""

    def test_same_title_different_dirs(self, tmp_path):
        with patch("app.utils.paths.OUTPUT_BASE", tmp_path):
            long_dir = story_dir("same_title", content_type="long")
            short_dir = story_dir("same_title", content_type="short")
            assert long_dir != short_dir
            assert long_dir.exists()
            assert short_dir.exists()
