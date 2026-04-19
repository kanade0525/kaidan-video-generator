"""Tests for MeCab-based hiragana conversion in text_processor."""

import pytest

from app.services.text_processor import _katakana_to_hiragana, _mecab_to_hiragana


def _mecab_available() -> bool:
    try:
        import MeCab  # noqa: F401
    except ImportError:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _mecab_available(), reason="MeCab not installed"
)


class TestKatakanaToHiragana:
    def test_basic(self):
        assert _katakana_to_hiragana("アイウエオ") == "あいうえお"

    def test_mixed(self):
        assert _katakana_to_hiragana("コンニチハ") == "こんにちは"

    def test_non_katakana_preserved(self):
        assert _katakana_to_hiragana("ABC123あ") == "ABC123あ"


class TestMecabToHiragana:
    def test_simple_kanji(self):
        """漢字→ひらがな変換の基本動作。"""
        result = _mecab_to_hiragana("今日")
        assert result == "きょう"

    def test_particle_wa(self):
        """助詞「は」→「わ」に変換（非助詞の「は」は残る）。"""
        result = _mecab_to_hiragana("彼女は走った")
        assert "かのじょわ" in result

    def test_particle_e(self):
        """助詞「へ」→「え」に変換。"""
        result = _mecab_to_hiragana("学校へ行く")
        assert "がっこうえ" in result

    def test_particle_wo_preserved(self):
        """助詞「を」はそのまま保持。"""
        result = _mecab_to_hiragana("本を読む")
        assert "を" in result

    def test_tokorodokoro_reading(self):
        """所々 → ところどころ（しょどころにならない）。"""
        result = _mecab_to_hiragana("所々に手形があった")
        assert "ところどころ" in result
        assert "しょどころ" not in result

    def test_jouhanshin_reading(self):
        """上半身 → じょうはんしん（じょうわんしんにならない）。"""
        result = _mecab_to_hiragana("上半身が見えた")
        assert "じょうはんしん" in result
        assert "じょうわんしん" not in result

    def test_no_kanji_remaining(self):
        """変換後に漢字が残らない。"""
        text = "彼女は暗い穴を覗き込んだ。所々に上半身の影が見えた。"
        result = _mecab_to_hiragana(text)
        import re
        remaining = re.findall(r"[一-龯]", result)
        assert remaining == [], f"未変換の漢字: {remaining}"

    def test_non_particle_ha_preserved(self):
        """非助詞の「は」は変えない（例: 「はなし」の「は」）。"""
        result = _mecab_to_hiragana("はなし")
        assert result == "はなし"

    def test_katakana_preserved(self):
        """カタカナはそのまま。"""
        result = _mecab_to_hiragana("カーテンを開けた")
        assert "カーテン" in result

    def test_punctuation_preserved(self):
        """句読点・記号は保持。"""
        result = _mecab_to_hiragana("彼は、走った。")
        assert "、" in result
        assert "。" in result

    def test_reading_override_watashi(self):
        """読み上書き辞書: 私 → わたし（MeCabデフォルトの「わたくし」を上書き）。"""
        result = _mecab_to_hiragana("私は走った")
        assert "わたし" in result
        assert "わたくし" not in result

    def test_inflected_verb_reading(self):
        """活用形の動詞はsurface形の読みを使う（レンマ形ではない）。"""
        # 覚ました → さました (not さまする)
        assert _mecab_to_hiragana("覚ました") == "さました"
        # 閉ざされる → とざされる (not とざすれる)
        assert _mecab_to_hiragana("閉ざされる") == "とざされる"
        # 積もった → つもった (not つもるた)
        assert _mecab_to_hiragana("積もった") == "つもった"
        # 寝ていた → ねていた (not ねるているた)
        assert _mecab_to_hiragana("寝ていた") == "ねていた"
        # 珍しくない → めずらしくない (not めずらしいない)
        assert _mecab_to_hiragana("珍しくない") == "めずらしくない"
