"""Tests for subtitle text splitting, SRT generation, and original chunk creation."""

import json
import wave
from pathlib import Path
from unittest.mock import patch

import pytest

from app.utils.ffmpeg import _split_subtitle_text, generate_srt
from app.services.text_processor import split_into_n_chunks


class TestSplitSubtitleText:
    """Test _split_subtitle_text splits long text into readable segments."""

    def test_short_text_unchanged(self):
        text = "これはテストです。"
        result = _split_subtitle_text(text, max_chars=28)
        assert result == [text]

    def test_splits_at_sentence_boundary(self):
        text = "これはテストです。ふたつめの文です。"
        result = _split_subtitle_text(text, max_chars=12)
        assert len(result) == 2
        assert result[0] == "これはテストです。"
        assert result[1] == "ふたつめの文です。"

    def test_no_orphaned_punctuation(self):
        """A single 。 should never be its own entry."""
        text = "長い文章がここにあります。次の文です。"
        result = _split_subtitle_text(text, max_chars=15)
        for seg in result:
            # No segment should be just punctuation
            assert len(seg) > 1, f"Orphaned punctuation entry: {seg!r}"

    def test_splits_at_comma_when_no_sentence_boundary(self):
        text = "長い文の一部、ここで切れる、もうひとつ"
        result = _split_subtitle_text(text, max_chars=12)
        assert len(result) >= 2
        for seg in result:
            assert len(seg) <= 12

    def test_preserves_all_text(self):
        """All original text must appear in joined segments."""
        text = (
            "学生時代に連んでいたメンバーが5、6人いて、"
            "卒業後バラバラになり集まる機会が少なくなっていた。"
            "そんなある日、4人程で集まることになり、"
            "場所は後藤の部屋に決まっていた。"
        )
        result = _split_subtitle_text(text, max_chars=28)
        assert "".join(result) == text
        assert all(len(seg) <= 28 for seg in result)

    def test_kanji_text_realistic(self):
        """Real kanji text from raw_content.txt should split cleanly."""
        text = (
            "「どうした？ここだろ？」"
            "「ああ…ここか…」"
            "「行こうぜ！」"
            "立ち止まっている友人を連れてマンションの中に入っていった。"
        )
        result = _split_subtitle_text(text, max_chars=28)
        assert "".join(result) == text
        # Short fragments (≤5 chars) may be merged, allowing up to +6 tolerance
        for seg in result:
            assert len(seg) <= 34  # 28 + merge tolerance of 6

    def test_empty_text(self):
        result = _split_subtitle_text("", max_chars=28)
        assert result == [""]

    def test_no_mid_word_cut_on_force_split(self):
        """Force-split should try to find a nearby break point."""
        text = "あ" * 60  # No natural break points
        result = _split_subtitle_text(text, max_chars=28)
        # Should still split cleanly (no break points to find, so hard cut is OK)
        assert "".join(result) == text


class TestSplitIntoNChunks:
    """Test split_into_n_chunks for creating original text chunks."""

    def test_splits_into_exact_count(self):
        text = "一行目。二行目。三行目。四行目。五行目。六行目。"
        result = split_into_n_chunks(text, 3)
        assert len(result) == 3
        assert "".join(result) == text

    def test_single_chunk(self):
        text = "短い文。"
        result = split_into_n_chunks(text, 1)
        assert result == [text]

    def test_more_chunks_than_sentences(self):
        text = "一つだけ。"
        result = split_into_n_chunks(text, 4)
        assert len(result) == 4
        assert result[0] == text
        # Others should be empty
        assert all(r == "" for r in result[1:])

    def test_preserves_all_text(self):
        text = (
            "学生時代に連んでいたメンバーが5、6人いて、"
            "卒業後バラバラになり集まる機会が少なくなっていた。\n"
            "そんなある日、4人程で集まることになり、"
            "場所は後藤の部屋に決まっていた。\n"
            "3人合流し、後藤の住むマンションに到着した。\n"
            "友人の顔が一瞬しかめたのを私は見てしまった。\n"
        )
        for n in [2, 3, 4]:
            result = split_into_n_chunks(text, n)
            assert len(result) == n
            joined = "".join(result)
            # All content should be preserved
            assert "学生時代" in joined
            assert "見てしまった" in joined


class TestGenerateSrt:
    """Test generate_srt with subtitle splitting."""

    @pytest.fixture
    def audio_dir(self, tmp_path):
        adir = tmp_path / "audio"
        adir.mkdir()
        return adir

    def _create_wav(self, path: Path, duration_sec: float):
        sample_rate = 24000
        n_frames = int(sample_rate * duration_sec)
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(b"\x00\x00" * n_frames)

    def test_generates_srt_with_split_subtitles(self, tmp_path, audio_dir):
        chunks = ["これはテストです。ふたつめの文です。三つ目の文です。"]
        self._create_wav(audio_dir / "narration_0000.wav", 10.0)

        srt_path = tmp_path / "subtitles.srt"
        generate_srt(chunks, audio_dir, srt_path, max_subtitle_chars=20)

        content = srt_path.read_text(encoding="utf-8")
        entries = [e.strip() for e in content.strip().split("\n\n") if e.strip()]
        assert len(entries) >= 2

    def test_leading_silence_offset(self, tmp_path, audio_dir):
        chunks = ["テスト"]
        self._create_wav(audio_dir / "narration_0000.wav", 5.0)

        srt_path = tmp_path / "subtitles.srt"
        generate_srt(chunks, audio_dir, srt_path, leading_silence=3.5)

        content = srt_path.read_text(encoding="utf-8")
        assert "00:00:03,500" in content

    def test_preserves_total_duration(self, tmp_path, audio_dir):
        chunks = [
            "一番目のチャンクです。長めの文章です。",
            "二番目のチャンクです。",
        ]
        self._create_wav(audio_dir / "narration_0000.wav", 10.0)
        self._create_wav(audio_dir / "narration_0001.wav", 5.0)

        srt_path = tmp_path / "subtitles.srt"
        generate_srt(chunks, audio_dir, srt_path, leading_silence=0.0,
                     max_subtitle_chars=20)

        content = srt_path.read_text(encoding="utf-8")
        assert "00:00:15,000" in content
