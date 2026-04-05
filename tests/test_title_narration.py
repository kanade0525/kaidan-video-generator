"""Tests for title narration feature: image filtering and video assembly args."""

import json

import pytest

from app.pipeline.stages import TITLE_CARD_FILENAME, load_scene_images


@pytest.fixture
def img_dir(tmp_path):
    """Create a temporary image directory with title card and scene images."""
    d = tmp_path / "images"
    d.mkdir()
    (d / "000_title_card.png").write_bytes(b"fake-title")
    (d / "scene_000.png").write_bytes(b"fake-scene-0")
    (d / "scene_001.png").write_bytes(b"fake-scene-1")
    (d / "scene_002.png").write_bytes(b"fake-scene-2")
    return d


class TestLoadSceneImages:
    """Tests for load_scene_images: title card must be excluded from slideshow."""

    def test_glob_excludes_title_card(self, img_dir, tmp_path):
        """Without slideshow.json, title card is excluded from glob results."""
        config_path = tmp_path / "slideshow.json"  # does not exist
        images, durations = load_scene_images(img_dir, config_path)

        filenames = [p.name for p in images]
        assert TITLE_CARD_FILENAME not in filenames
        assert len(images) == 3
        assert "scene_000.png" in filenames
        assert "scene_001.png" in filenames
        assert "scene_002.png" in filenames
        assert durations is None

    def test_slideshow_config_excludes_title_card(self, img_dir, tmp_path):
        """With slideshow.json containing title card, it is excluded."""
        config_path = tmp_path / "slideshow.json"
        config = [
            {"file": "000_title_card.png", "duration": 5},
            {"file": "scene_000.png", "duration": 3},
            {"file": "scene_001.png", "duration": 0},
            {"file": "scene_002.png", "duration": 4},
        ]
        config_path.write_text(json.dumps(config))

        images, durations = load_scene_images(img_dir, config_path)

        filenames = [p.name for p in images]
        assert TITLE_CARD_FILENAME not in filenames
        assert len(images) == 3
        assert durations == [3, 0, 4]

    def test_slideshow_config_without_title_card(self, img_dir, tmp_path):
        """slideshow.json without title card entry works fine."""
        config_path = tmp_path / "slideshow.json"
        config = [
            {"file": "scene_000.png", "duration": 2},
            {"file": "scene_001.png", "duration": 3},
        ]
        config_path.write_text(json.dumps(config))

        images, durations = load_scene_images(img_dir, config_path)

        assert len(images) == 2
        assert durations == [2, 3]

    def test_empty_slideshow_config_falls_back_to_glob(self, img_dir, tmp_path):
        """Empty slideshow.json falls back to glob (excluding title card)."""
        config_path = tmp_path / "slideshow.json"
        config_path.write_text("[]")

        images, durations = load_scene_images(img_dir, config_path)

        filenames = [p.name for p in images]
        assert TITLE_CARD_FILENAME not in filenames
        assert len(images) == 3
        assert durations is None

    def test_no_scene_images_returns_empty(self, tmp_path):
        """When only title card exists, returns empty list."""
        d = tmp_path / "images"
        d.mkdir()
        (d / "000_title_card.png").write_bytes(b"fake")
        config_path = tmp_path / "slideshow.json"

        images, durations = load_scene_images(d, config_path)

        assert images == []
        assert durations is None

    def test_missing_image_in_config_skipped(self, img_dir, tmp_path):
        """Config entries for missing files are skipped."""
        config_path = tmp_path / "slideshow.json"
        config = [
            {"file": "scene_000.png", "duration": 2},
            {"file": "scene_999.png", "duration": 5},  # doesn't exist
        ]
        config_path.write_text(json.dumps(config))

        images, durations = load_scene_images(img_dir, config_path)

        assert len(images) == 1
        assert images[0].name == "scene_000.png"
        assert durations == [2]

    def test_images_sorted_alphabetically(self, tmp_path):
        """Glob fallback returns images in sorted order."""
        d = tmp_path / "images"
        d.mkdir()
        (d / "scene_002.png").write_bytes(b"fake")
        (d / "scene_000.png").write_bytes(b"fake")
        (d / "scene_001.png").write_bytes(b"fake")
        config_path = tmp_path / "slideshow.json"

        images, _ = load_scene_images(d, config_path)

        assert [p.name for p in images] == [
            "scene_000.png",
            "scene_001.png",
            "scene_002.png",
        ]


class TestTitleCardFilename:
    def test_constant_value(self):
        assert TITLE_CARD_FILENAME == "000_title_card.png"
