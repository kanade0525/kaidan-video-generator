"""Tests for subtitle text splitting and SRT generation."""

import json
import wave
from pathlib import Path
from unittest.mock import patch

import pytest

from app.utils.ffmpeg import _split_subtitle_text, generate_srt


class TestSplitSubtitleText:
    """Test _split_subtitle_text splits long text into readable segments."""

    def test_short_text_unchanged(self):
        text = "これはテストです。"
        result = _split_subtitle_text(text, max_chars=40)
        assert result == [text]

    def test_splits_at_sentence_boundary(self):
        text = "これはテストです。ふたつめのぶんです。"
        result = _split_subtitle_text(text, max_chars=15)
        assert len(result) == 2
        assert result[0] == "これはテストです。"
        assert result[1] == "ふたつめのぶんです。"

    def test_splits_at_comma_when_no_sentence_boundary(self):
        text = "ながいぶんしょう、ここできれる、もうひとつ"
        result = _split_subtitle_text(text, max_chars=15)
        assert len(result) >= 2
        # Each segment should be under max_chars
        for seg in result:
            assert len(seg) <= 15

    def test_force_splits_very_long_text(self):
        text = "あ" * 100  # No natural break points
        result = _split_subtitle_text(text, max_chars=40)
        assert len(result) == 3  # 40 + 40 + 20
        assert all(len(seg) <= 40 for seg in result)
        assert "".join(result) == text

    def test_preserves_all_text(self):
        text = (
            "がくせいじだいにつるんでいたメンバーが5、6にんいて、"
            "そつぎょうごバラバラになりあつまるきかいがすくなくなっていた。"
            "そんなあるひ、4にんほどであつまることになり、"
            "ばしょわごとうのへやにきまっていた。"
        )
        result = _split_subtitle_text(text, max_chars=40)
        assert "".join(result) == text
        assert all(len(seg) <= 40 for seg in result)

    def test_realistic_chunk_200_chars(self):
        """A real 200-char chunk should split into 5+ readable segments."""
        text = (
            "がくせいじだいにつるんでいたメンバーが5、6にんいて、"
            "そつぎょうごバラバラになりあつまるきかいがすくなくなっていた。"
            "そんなあるひ、4にんほどであつまることになり、"
            "ばしょわごとうのへやにきまっていた。"
            "3にんごうりゅうし、ごとうのすむマンションにとうちゃくし、"
            "たてもののまえにつくとゆうじんのひとりがたちどまった。"
        )
        result = _split_subtitle_text(text, max_chars=40)
        assert len(result) >= 4
        assert all(len(seg) <= 40 for seg in result)
        assert "".join(result) == text

    def test_splits_at_closing_bracket(self):
        text = "「どうした？ここだろ？」「ああ…ここか…」「いこうぜ！」"
        result = _split_subtitle_text(text, max_chars=20)
        assert len(result) >= 2
        assert "".join(result) == text

    def test_empty_text(self):
        result = _split_subtitle_text("", max_chars=40)
        assert result == [""]

    def test_exact_max_chars(self):
        text = "あ" * 40
        result = _split_subtitle_text(text, max_chars=40)
        assert result == [text]


class TestGenerateSrt:
    """Test generate_srt with subtitle splitting."""

    @pytest.fixture
    def audio_dir(self, tmp_path):
        """Create dummy audio files with known durations."""
        adir = tmp_path / "audio"
        adir.mkdir()
        return adir

    def _create_wav(self, path: Path, duration_sec: float):
        """Create a silent WAV file with specified duration."""
        sample_rate = 24000
        n_frames = int(sample_rate * duration_sec)
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(b"\x00\x00" * n_frames)

    def test_generates_srt_with_split_subtitles(self, tmp_path, audio_dir):
        chunks = [
            "これはテストです。ふたつめのぶんです。みっつめです。",
        ]
        self._create_wav(audio_dir / "narration_0000.wav", 10.0)

        srt_path = tmp_path / "subtitles.srt"
        generate_srt(chunks, audio_dir, srt_path, max_subtitle_chars=20)

        content = srt_path.read_text(encoding="utf-8")
        entries = [e.strip() for e in content.strip().split("\n\n") if e.strip()]
        # Should have multiple entries from the single chunk
        assert len(entries) >= 2

    def test_leading_silence_offset(self, tmp_path, audio_dir):
        chunks = ["テスト"]
        self._create_wav(audio_dir / "narration_0000.wav", 5.0)

        srt_path = tmp_path / "subtitles.srt"
        generate_srt(chunks, audio_dir, srt_path, leading_silence=3.5)

        content = srt_path.read_text(encoding="utf-8")
        # First entry should start at 3.5s
        assert "00:00:03,500" in content

    def test_preserves_total_duration(self, tmp_path, audio_dir):
        """Total SRT duration should match total audio duration."""
        chunks = [
            "いちばんめのちゃんくです。ながめのぶんしょうです。",
            "にばんめのちゃんくです。",
        ]
        self._create_wav(audio_dir / "narration_0000.wav", 10.0)
        self._create_wav(audio_dir / "narration_0001.wav", 5.0)

        srt_path = tmp_path / "subtitles.srt"
        generate_srt(chunks, audio_dir, srt_path, leading_silence=0.0,
                     max_subtitle_chars=20)

        content = srt_path.read_text(encoding="utf-8")
        # Last entry should end at 15.0s (10 + 5)
        assert "00:00:15,000" in content
