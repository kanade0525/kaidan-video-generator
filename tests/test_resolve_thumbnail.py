"""Tests for resolve_thumbnail_path (custom_thumbnail > title card priority).

長編/ショートの YouTube サムネは custom_thumbnail.* を最優先し、無ければ
タイトルカード、どちらも無ければ None。UI と do_youtube_upload で共有される。
"""

import pytest

from app.pipeline import stages


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    sdir = tmp_path / "story"
    idir = sdir / "images"
    idir.mkdir(parents=True)
    monkeypatch.setattr(stages, "story_dir", lambda title, ct: sdir)
    monkeypatch.setattr(stages, "images_dir", lambda title, ct: idir)
    return sdir, idir


def test_none_when_nothing_exists(dirs):
    assert stages.resolve_thumbnail_path("t", "long") is None


def test_title_card_used_when_no_custom(dirs):
    sdir, idir = dirs
    card = idir / stages.title_card_filename("long")
    card.write_bytes(b"x")
    assert stages.resolve_thumbnail_path("t", "long") == card


def test_custom_overrides_title_card(dirs):
    sdir, idir = dirs
    (idir / stages.title_card_filename("long")).write_bytes(b"x")
    custom = sdir / "custom_thumbnail.png"
    custom.write_bytes(b"y")
    assert stages.resolve_thumbnail_path("t", "long") == custom


def test_custom_jpg_recognized(dirs):
    sdir, _ = dirs
    custom = sdir / "custom_thumbnail.jpg"
    custom.write_bytes(b"y")
    assert stages.resolve_thumbnail_path("t", "long") == custom


def test_png_preferred_over_jpg(dirs):
    sdir, _ = dirs
    (sdir / "custom_thumbnail.jpg").write_bytes(b"j")
    png = sdir / "custom_thumbnail.png"
    png.write_bytes(b"p")
    assert stages.resolve_thumbnail_path("t", "long") == png


def test_short_uses_short_title_card(dirs):
    sdir, idir = dirs
    card = idir / stages.title_card_filename("short")
    card.write_bytes(b"x")
    assert stages.resolve_thumbnail_path("t", "short") == card
