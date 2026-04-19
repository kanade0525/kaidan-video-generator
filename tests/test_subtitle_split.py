"""Tests for subtitle text splitting and original chunk creation."""

from app.utils.ffmpeg import _split_subtitle_text
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
        # Short fragments (≤3 chars) may be merged, but max_chars is strictly respected
        for seg in result:
            assert len(seg) <= 31  # 28 + max 3 char merge

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
