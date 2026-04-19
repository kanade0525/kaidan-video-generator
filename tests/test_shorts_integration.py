"""Integration tests for shorts pipeline: DB -> stage dispatch -> file routing.

These tests verify the end-to-end flow without hitting external APIs
(VOICEVOX, Gemini, YouTube) by mocking service-level functions.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app import database as db
from app.models import Story
from app.pipeline.stages import (
    STAGE_FUNCTIONS,
    do_scrape_short,
    do_text,
    do_voice_short,
)
from app.utils.paths import (
    audio_dir,
    chunks_path,
    meta_path,
    narration_path,
    processed_text_path,
    raw_content_path,
    story_dir,
    video_path,
)


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path):
    """Use a fresh temp DB for each test."""
    test_db = tmp_path / "test.db"
    with patch.object(db, "DB_PATH", test_db):
        if hasattr(db._local, "conn"):
            db._local.conn = None
        db.init_db()
        yield
        if hasattr(db._local, "conn") and db._local.conn:
            db._local.conn.close()
            db._local.conn = None


@pytest.fixture
def output_base(tmp_path):
    """Provide a temp output directory."""
    base = tmp_path / "output"
    base.mkdir()
    with patch("app.utils.paths.OUTPUT_BASE", base):
        yield base


class TestShortStoryDBFlow:
    """Test that a short story flows through DB correctly."""

    def test_add_short_and_query(self):
        s = db.add_story(
            url="https://kikikaikai.kusuguru.co.jp/12345",
            title="テスト怪談",
            content_type="short",
            author="太郎",
            char_count=200,
        )
        assert s.content_type == "short"

        # Should appear in short queries only
        shorts = db.get_stories_at_stage("pending", content_type="short")
        assert len(shorts) == 1
        assert shorts[0].id == s.id

        longs = db.get_stories_at_stage("pending", content_type="long")
        assert len(longs) == 0

    def test_stage_progression(self):
        s = db.add_story(
            url="https://kikikaikai.kusuguru.co.jp/99999",
            title="進行テスト",
            content_type="short",
        )
        # Simulate pipeline progression
        for stage in ["scraped", "text_processed", "voice_generated", "images_generated", "video_complete"]:
            db.update_stage(s.id, stage)
            story = db.get_story_by_id(s.id)
            assert story.stage == stage

        # No report_submitted for shorts
        db.update_stage(s.id, "youtube_uploaded")
        story = db.get_story_by_id(s.id)
        assert story.stage == "youtube_uploaded"


class TestShortFileRouting:
    """Test that short story files go to output/shorts/ directory."""

    def test_short_story_dir(self, output_base):
        d = story_dir("テスト", content_type="short")
        assert "shorts" in str(d)
        assert d.is_dir()

    def test_long_story_dir(self, output_base):
        d = story_dir("テスト", content_type="long")
        assert "shorts" not in str(d)

    def test_isolation(self, output_base):
        """Short and long with same title don't interfere."""
        short_raw = raw_content_path("same", content_type="short")
        long_raw = raw_content_path("same", content_type="long")
        assert short_raw != long_raw

        short_raw.write_text("short content")
        long_raw.write_text("long content")

        assert short_raw.read_text() == "short content"
        assert long_raw.read_text() == "long content"


class TestScrapeShortStage:
    """Test do_scrape_short with mocked HTTP."""

    def test_scrape_saves_files(self, output_base):
        story = Story(
            id=1, url="https://kikikaikai.kusuguru.co.jp/12345",
            title="スクレイプテスト", content_type="short", author="太郎",
        )

        mock_content = "これはテスト用のコンテンツです。十分に長い文章にします。"
        mock_metadata = {
            "title": "スクレイプテスト",
            "author": "太郎",
            "tags": ["心霊"],
            "char_count": len(mock_content),
        }

        with patch("app.services.kikikaikai_scraper.fetch_story_content") as mock_fetch:
            mock_fetch.return_value = (mock_content, mock_metadata)
            do_scrape_short(story)

        # Verify files
        raw = raw_content_path("スクレイプテスト", "short")
        assert raw.exists()
        assert raw.read_text() == mock_content

        meta = meta_path("スクレイプテスト", "short")
        assert meta.exists()
        meta_data = json.loads(meta.read_text())
        assert meta_data["author"] == "太郎"
        assert meta_data["source"] == "kikikaikai"


class TestTextStageShared:
    """Test that do_text works for both content types."""

    def test_text_stage_with_short_story(self, output_base):
        story = Story(id=1, title="テキストテスト", content_type="short")

        # Write raw content
        raw = raw_content_path("テキストテスト", "short")
        raw.write_text("これはテスト用の原文です。")

        with patch("app.services.text_processor.process_text", return_value="これわてすとようのげんぶんです。"):
            with patch("app.services.text_processor.split_into_chunks", return_value=["これわてすとようの", "げんぶんです。"]):
                do_text(story)

        # Verify output went to shorts dir
        processed = processed_text_path("テキストテスト", "short")
        assert processed.exists()
        assert "shorts" in str(processed)

        chunks = chunks_path("テキストテスト", "short")
        assert chunks.exists()
        chunk_data = json.loads(chunks.read_text())
        assert len(chunk_data) == 2


class TestStageDispatch:
    """Test that the stage function registry dispatches correctly."""

    def test_short_scrape_dispatches_to_do_scrape_short(self):
        func = STAGE_FUNCTIONS[("short", "scraped")]
        assert func.__name__ == "do_scrape_short"

    def test_short_text_dispatches_to_shared_do_text(self):
        func = STAGE_FUNCTIONS[("short", "text_processed")]
        assert func.__name__ == "do_text"

    def test_short_voice_dispatches_to_do_voice_short(self):
        func = STAGE_FUNCTIONS[("short", "voice_generated")]
        assert func.__name__ == "do_voice_short"

    def test_long_scrape_dispatches_to_do_scrape(self):
        func = STAGE_FUNCTIONS[("long", "scraped")]
        assert func.__name__ == "do_scrape"


class TestVoiceShortDurationValidation:
    """Test that do_voice_short tolerates long stories (YouTube Shorts
    制限は後段 upload で弾く。voice は TikTok 等のために常に完走させる)."""

    def test_over_180_seconds_does_not_raise(self, output_base):
        """180s超過でも voice ステージは正常完了（警告ログのみ）。"""
        story = Story(id=1, title="長すぎる話", content_type="short")

        chunks = chunks_path("長すぎる話", "short")
        chunks.write_text(json.dumps(["chunk1", "chunk2"]))

        narr = narration_path("長すぎる話", "short")
        narr.write_bytes(b"dummy")

        with patch("app.services.voice_generator.generate_narration"):
            with patch("app.utils.ffmpeg.get_audio_duration", return_value=300.0):
                # Should not raise — voice生成は尺に関係なく完走
                do_voice_short(story)

    def test_accepts_under_180_seconds(self, output_base):
        story = Story(id=1, title="ちょうどよい話", content_type="short")

        chunks = chunks_path("ちょうどよい話", "short")
        chunks.write_text(json.dumps(["chunk1"]))

        narr = narration_path("ちょうどよい話", "short")
        narr.write_bytes(b"dummy")

        with patch("app.services.voice_generator.generate_narration"):
            with patch("app.utils.ffmpeg.get_audio_duration", return_value=90.0):
                do_voice_short(story)
