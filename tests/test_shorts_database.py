"""Tests for shorts-related database extensions."""

import sqlite3
from unittest.mock import patch

import pytest

from app import database as db
from app.models import STAGES_SHORT


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path):
    """Use a fresh in-memory-like temp DB for each test."""
    test_db = tmp_path / "test.db"
    with patch.object(db, "DB_PATH", test_db):
        # Reset thread-local connection
        if hasattr(db._local, "conn"):
            db._local.conn = None
        db.init_db()
        yield
        if hasattr(db._local, "conn") and db._local.conn:
            db._local.conn.close()
            db._local.conn = None


class TestMigration:
    def test_content_type_column_exists(self):
        conn = db._get_conn()
        row = conn.execute(
            "SELECT content_type FROM stories LIMIT 0"
        ).description
        assert row is not None

    def test_author_column_exists(self):
        conn = db._get_conn()
        row = conn.execute("SELECT author FROM stories LIMIT 0").description
        assert row is not None

    def test_char_count_column_exists(self):
        conn = db._get_conn()
        row = conn.execute("SELECT char_count FROM stories LIMIT 0").description
        assert row is not None

    def test_content_type_index_exists(self):
        conn = db._get_conn()
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_content_type'"
        ).fetchall()
        assert len(rows) == 1


class TestAddStoryShort:
    def test_add_short_story(self):
        story = db.add_story(
            url="https://kikikaikai.kusuguru.co.jp/12345",
            title="テスト怪談",
            content_type="short",
            author="テスト太郎",
            char_count=300,
        )
        assert story is not None
        assert story.content_type == "short"
        assert story.author == "テスト太郎"
        assert story.char_count == 300

    def test_add_long_story_default(self):
        story = db.add_story(url="https://example.com/1", title="Long")
        assert story is not None
        assert story.content_type == "long"
        assert story.author == ""
        assert story.char_count is None

    def test_duplicate_url_returns_none(self):
        db.add_story(url="https://example.com/dup", content_type="short")
        result = db.add_story(url="https://example.com/dup", content_type="short")
        assert result is None


class TestGetStoriesContentType:
    def test_filter_by_short(self):
        db.add_story(url="https://a.com/1", title="Long1", content_type="long")
        db.add_story(url="https://b.com/2", title="Short1", content_type="short")
        db.add_story(url="https://c.com/3", title="Short2", content_type="short")

        shorts = db.get_stories(content_type="short")
        assert len(shorts) == 2
        assert all(s.content_type == "short" for s in shorts)

    def test_filter_by_long(self):
        db.add_story(url="https://a.com/1", title="Long1", content_type="long")
        db.add_story(url="https://b.com/2", title="Short1", content_type="short")

        longs = db.get_stories(content_type="long")
        assert len(longs) == 1
        assert longs[0].content_type == "long"

    def test_no_filter_returns_all(self):
        db.add_story(url="https://a.com/1", content_type="long")
        db.add_story(url="https://b.com/2", content_type="short")

        all_stories = db.get_stories()
        assert len(all_stories) == 2


class TestCountStoriesContentType:
    def test_count_short(self):
        db.add_story(url="https://a.com/1", content_type="long")
        db.add_story(url="https://b.com/2", content_type="short")
        db.add_story(url="https://c.com/3", content_type="short")

        assert db.count_stories(content_type="short") == 2
        assert db.count_stories(content_type="long") == 1
        assert db.count_stories() == 3


class TestGetStageCounts:
    def test_stage_counts_with_content_type(self):
        s1 = db.add_story(url="https://a.com/1", content_type="long")
        s2 = db.add_story(url="https://b.com/2", content_type="short")
        s3 = db.add_story(url="https://c.com/3", content_type="short")
        db.update_stage(s2.id, "scraped")

        counts_short = db.get_stage_counts(content_type="short")
        assert counts_short.get("pending", 0) == 1
        assert counts_short.get("scraped", 0) == 1

        counts_long = db.get_stage_counts(content_type="long")
        assert counts_long.get("pending", 0) == 1


class TestGetStoriesAtStage:
    def test_filter_by_content_type(self):
        db.add_story(url="https://a.com/1", content_type="long")
        db.add_story(url="https://b.com/2", content_type="short")

        long_pending = db.get_stories_at_stage("pending", content_type="long")
        assert len(long_pending) == 1
        assert long_pending[0].content_type == "long"

        short_pending = db.get_stories_at_stage("pending", content_type="short")
        assert len(short_pending) == 1
        assert short_pending[0].content_type == "short"


class TestMarkFailed:
    def test_mark_failed_short(self):
        s = db.add_story(url="https://a.com/1", content_type="short")
        db.update_stage(s.id, "scraped")
        db.mark_failed(s.id, "text_processed", "error", content_type="short")

        story = db.get_story_by_id(s.id)
        assert story.stage == "scraped"
        assert story.error == "error"

    def test_mark_failed_long_default(self):
        s = db.add_story(url="https://a.com/1", content_type="long")
        db.update_stage(s.id, "scraped")
        db.mark_failed(s.id, "text_processed", "error")

        story = db.get_story_by_id(s.id)
        assert story.stage == "scraped"


class TestSource:
    def test_long_default_source_hhs(self):
        story = db.add_story(url="https://hhs.parasite.jp/hhslibrary/?p=1", content_type="long")
        assert story.source == "hhs"

    def test_short_inferred_source_kikikaikai(self):
        story = db.add_story(
            url="https://kikikaikai.kusuguru.co.jp/12345", content_type="short",
        )
        assert story.source == "kikikaikai"

    def test_explicit_source_wins(self):
        story = db.add_story(
            url="https://example.com/custom", content_type="short", source="hhs",
        )
        assert story.source == "hhs"

    def test_source_column_persists(self):
        s = db.add_story(url="https://hhs.parasite.jp/a", content_type="long")
        fresh = db.get_story_by_id(s.id)
        assert fresh.source == "hhs"


class TestConvertToShort:
    def test_basic_conversion(self):
        s = db.add_story(
            url="https://hhs.parasite.jp/hhslibrary/?p=1",
            title="HHS Test", content_type="long",
        )
        db.update_stage(s.id, "scraped")
        db.update_stage(s.id, "text_processed")
        db.update_stage(s.id, "voice_generated")
        db.update_stage(s.id, "images_generated")
        db.update_stage(s.id, "video_complete")
        db.update_stage(s.id, "youtube_uploaded")
        db.set_youtube_video_id(s.id, "LONG_ID")

        db.convert_to_short(s.id)

        fresh = db.get_story_by_id(s.id)
        assert fresh.content_type == "short"
        assert fresh.stage == "voice_generated"
        assert fresh.source == "hhs"  # preserved
        assert fresh.youtube_video_id is None  # cleared for new short upload

    def test_later_stage_completions_cleared(self):
        s = db.add_story(url="https://hhs.parasite.jp/2", content_type="long")
        for stg in ["scraped", "text_processed", "voice_generated",
                    "images_generated", "video_complete", "youtube_uploaded"]:
            db.update_stage(s.id, stg)

        db.convert_to_short(s.id)

        conn = db._get_conn()
        remaining = conn.execute(
            "SELECT stage FROM stage_completions WHERE story_id = ?", (s.id,),
        ).fetchall()
        stages = {r["stage"] for r in remaining}
        # Up to and including voice_generated should remain
        assert "voice_generated" in stages
        # Later stages should be cleared
        assert "images_generated" not in stages
        assert "youtube_uploaded" not in stages

    def test_kikikaikai_source_preserved(self):
        """Converting an already-short kikikaikai story (rare case) still keeps source."""
        s = db.add_story(
            url="https://kikikaikai.kusuguru.co.jp/99",
            content_type="long",  # intentionally marked long to test conversion
            source="kikikaikai",
        )
        db.update_stage(s.id, "voice_generated")
        db.convert_to_short(s.id)

        fresh = db.get_story_by_id(s.id)
        assert fresh.source == "kikikaikai"
        assert fresh.content_type == "short"

    def test_artifacts_copied_to_short_dir(self, tmp_path, monkeypatch):
        """Long→Short migration copies reusable artifacts so the Shorts pipeline
        finds raw/processed/voice at the expected short paths (no re-generation)."""
        import app.utils.paths as paths
        monkeypatch.setattr(paths, "OUTPUT_BASE", tmp_path / "out")

        title = "テスト話"
        long_dir = paths.story_dir(title, "long")
        (long_dir / "raw_content.txt").write_text("RAW")
        (long_dir / "processed_text.txt").write_text("PROC")
        (long_dir / "chunks.json").write_text("[]")
        (long_dir / "original_chunks.json").write_text("[]")
        (long_dir / "narration_complete.wav").write_bytes(b"WAV")
        (long_dir / "audio").mkdir()
        (long_dir / "audio" / "narration_0000.wav").write_bytes(b"C0")
        (long_dir / "audio" / "narration_0001.wav").write_bytes(b"C1")

        s = db.add_story(url="https://hhs.parasite.jp/1", title=title, content_type="long")
        db.update_stage(s.id, "voice_generated")
        db.convert_to_short(s.id)

        short_dir = paths.story_dir(title, "short")
        assert (short_dir / "raw_content.txt").read_text() == "RAW"
        assert (short_dir / "processed_text.txt").read_text() == "PROC"
        assert (short_dir / "narration_complete.wav").read_bytes() == b"WAV"
        assert (short_dir / "audio" / "narration_0000.wav").read_bytes() == b"C0"
        assert (short_dir / "audio" / "narration_0001.wav").read_bytes() == b"C1"
        # Long-side originals must remain (for rollback / reference)
        assert (long_dir / "raw_content.txt").exists()
        assert (long_dir / "narration_complete.wav").exists()


class TestShortsStagesIncludeReport:
    """STAGES_SHORT must include report_submitted for HHS-sourced shorts."""

    def test_report_submitted_in_shorts_stages(self):
        assert "report_submitted" in STAGES_SHORT


class TestRecoverRunning:
    def test_recover_short_running(self):
        s = db.add_story(url="https://a.com/1", content_type="short")
        db.mark_running(s.id, "scraped")

        count = db.recover_running()
        assert count == 1

        story = db.get_story_by_id(s.id)
        assert story.stage == "pending"

    def test_recover_mixed(self):
        s1 = db.add_story(url="https://a.com/1", content_type="long")
        s2 = db.add_story(url="https://b.com/2", content_type="short")
        db.mark_running(s1.id, "scraped")
        db.mark_running(s2.id, "text_processed")

        count = db.recover_running()
        assert count == 2
