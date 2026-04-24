"""Tests for narration text preparation.

Current behavior (2026-04 inversion): kanji is kept as-is by default.
VOICEVOX reads kanji correctly in context; blanket hiragana conversion
caused more harm than good (particle collisions, counter mis-reads).

Only these produce hiragana output:
- Furigana annotations 「漢字（ふりがな）」
- `_DEFAULT_COMPOUND_REPLACEMENTS` string replacements applied before tokenizing
- `_DEFAULT_READING_OVERRIDES` per-token overrides
- Particles は→わ, へ→え (MeCab POS-tagged)
"""

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


class TestKeepKanjiByDefault:
    """Default behavior: kanji surfaces pass through untouched."""

    def test_plain_kanji_word_kept(self):
        assert _mecab_to_hiragana("今日") == "今日"

    def test_mixed_sentence_keeps_kanji(self):
        result = _mecab_to_hiragana("彼女は暗い穴を覗き込んだ。")
        # kanji remain
        assert "彼女" in result
        assert "穴" in result
        assert "覗" in result
        # punctuation preserved
        assert "。" in result

    def test_inflected_verbs_kept_as_kanji(self):
        """活用形動詞の表層はそのまま保持される（VOICEVOXが文脈で正読）。"""
        assert "覚まし" in _mecab_to_hiragana("覚ました")
        assert "閉ざさ" in _mecab_to_hiragana("閉ざされる")
        assert "積もっ" in _mecab_to_hiragana("積もった")

    def test_compound_kanji_kept(self):
        """複合語も原形のまま（会話・花火 等）。"""
        assert "会話" in _mecab_to_hiragana("静かな会話")
        assert "花火" in _mecab_to_hiragana("夏の花火")


class TestParticleConversion:
    def test_particle_wa(self):
        """助詞「は」→「わ」。"""
        assert "彼女わ" in _mecab_to_hiragana("彼女は走った")

    def test_particle_e(self):
        """助詞「へ」→「え」。"""
        assert "学校え" in _mecab_to_hiragana("学校へ行く")

    def test_particle_wo_preserved(self):
        """助詞「を」はそのまま。"""
        assert "を" in _mecab_to_hiragana("本を読む")

    def test_non_particle_ha_preserved(self):
        """非助詞の「は」（例: 「はなし」の「は」）は変換しない。"""
        assert _mecab_to_hiragana("はなし") == "はなし"


class TestReadingOverrides:
    def test_watashi_override(self):
        """私 → わたし（MeCabデフォルトの「わたくし」を避けつつ、ひらがな形で出す）。"""
        result = _mecab_to_hiragana("私は走った")
        assert "わたし" in result
        assert "わたくし" not in result


class TestCompoundReplacements:
    def test_otousan(self):
        """お父さん → おとうさん (MeCab分割で 父→ちち になるのを回避)。"""
        assert "おとうさん" in _mecab_to_hiragana("お父さんは走った")

    def test_okaasan(self):
        """お母さん → おかあさん。"""
        assert "おかあさん" in _mecab_to_hiragana("お母さんは優しい")

    def test_no_nioi_noun(self):
        """「の臭い」は名詞として におい 固定。"""
        assert "におい" in _mecab_to_hiragana("石油ヒーターの臭いと、機械音")

    def test_itsunomanika_idiom(self):
        """「いつの間にか」は 間→ま 固定。"""
        assert "いつのまにか" in _mecab_to_hiragana("いつの間にか眠っていた")

    def test_counter_kurai(self):
        """カウンター+位 は「〜くらい」に展開。"""
        assert "くらい" in _mecab_to_hiragana("4０代位の女の人")
        assert "くらい" in _mecab_to_hiragana("5時位に帰る")


class TestFurigana:
    def test_basic_annotation(self):
        """「漢字（ふりがな）」はふりがなが採用される。"""
        assert "うどんげ" in _mecab_to_hiragana("優曇華（うどんげ）の花")

    def test_okurigana_dedup(self):
        """送り仮名の重複を除去: 掴（つかみ）み取って → つかみ取って"""
        result = _mecab_to_hiragana("花を掴（つかみ）み取って")
        assert "つかみみ" not in result
        assert "つかみ取って" in result

    def test_propagates_to_later_occurrences(self):
        """同一漢字の2回目以降にもふりがな読みが伝播。"""
        result = _mecab_to_hiragana(
            "優曇華（うどんげ）の花というものがある。優曇華の花は伝説上の植物だ。"
        )
        assert result.count("うどんげ") >= 2


class TestKatakanaAndPunctuation:
    def test_katakana_preserved(self):
        assert "カーテン" in _mecab_to_hiragana("カーテンを開けた")

    def test_punctuation_preserved(self):
        result = _mecab_to_hiragana("彼は、走った。")
        assert "、" in result
        assert "。" in result


class TestUserConfigOverlay:
    def test_user_overrides_merge_with_defaults(self, tmp_path, monkeypatch):
        """UIで追加した辞書エントリがデフォルトとマージされる。"""
        import app.config as config_module
        config_path = tmp_path / "config.toml"
        monkeypatch.setattr(config_module, "CONFIG_PATH", config_path)
        config_module.save_config({
            "reading_overrides": {"犬": "わんちゃん"},
            "compound_replacements": {"お爺さん": "おじいさん"},
        })
        # User-added override applied when surface matches
        assert "わんちゃん" in _mecab_to_hiragana("犬が来た")
        # User-added compound replacement applied
        assert "おじいさん" in _mecab_to_hiragana("お爺さんは優しい")
        # Default overrides still apply
        assert "わたし" in _mecab_to_hiragana("私は行く")
