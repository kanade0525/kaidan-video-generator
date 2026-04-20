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

    def test_compound_otousan(self):
        """複合語置換: お父さん → おとうさん（MeCab分割で 父→ちち になるのを防ぐ）。"""
        result = _mecab_to_hiragana("お父さんは走った")
        assert "おとうさん" in result
        assert "おちち" not in result

    def test_compound_okaasan(self):
        """複合語置換: お母さん → おかあさん（MeCab分割で 母→はは になるのを防ぐ）。"""
        result = _mecab_to_hiragana("お母さんは優しい")
        assert "おかあさん" in result
        assert "おはは" not in result

    def test_keep_haha_as_kanji(self):
        """母は漢字のまま保持（VOICEVOXが "はは" を "ワワ" と誤読するため）。"""
        result = _mecab_to_hiragana("3人で母を見た")
        assert "母" in result
        assert "はは" not in result

    def test_keep_hanashi_as_kanji(self):
        """話は漢字のまま保持（「こわいはなし」等で VOICEVOX が はなし→ワナシ と誤読）。"""
        result = _mecab_to_hiragana("怖い話")
        assert "話" in result
        assert "はなし" not in result

    def test_keep_hana_kanji(self):
        """花/鼻は漢字のまま保持（VOICEVOX が はな→ワナ と誤読）。"""
        assert "花" in _mecab_to_hiragana("きれいな花")
        assert "鼻" in _mecab_to_hiragana("赤い鼻")

    def test_keep_body_parts_kanji(self):
        """肌/羽など、VOICEVOX が は→ワ と誤読する1-2モーラ名詞を漢字保持。"""
        assert "肌" in _mecab_to_hiragana("きれいな肌")
        assert "羽" in _mecab_to_hiragana("長い羽")
        assert "箱" in _mecab_to_hiragana("この箱")

    def test_hanasu_verb_form_still_hiragana(self):
        """話す(動詞)などの活用形はひらがなに変換される（surface が 話 単独ではないため）。"""
        # MeCabは「話す」を1トークンで返すため、_KEEP_AS_KANJI(話)には合致せず
        # 通常の漢字→ひらがな変換が適用される
        assert "はなす" in _mecab_to_hiragana("彼は話す")
        assert "はなした" in _mecab_to_hiragana("彼が話した")

    def test_compound_kanji_still_converted(self):
        """会話/昔話/花火 などの複合語も1トークンで処理されひらがな変換される。"""
        assert "かいわ" in _mecab_to_hiragana("静かな会話")
        assert "むかしばなし" in _mecab_to_hiragana("昔話を聞く")
        assert "はなび" in _mecab_to_hiragana("夏の花火")

    def test_noun_nioi_after_no(self):
        """の臭い は名詞(におい)として読む。MeCab が後続文脈で形容詞に倒れるのを防ぐ。"""
        result = _mecab_to_hiragana("石油ヒーターの臭いと、機械音")
        assert "におい" in result
        assert "くさい" not in result

    def test_counter_span_preserved_kanji(self):
        """数字+カウンター漢字は漢字のまま保持（VOICEVOXが正しい促音/連濁で読むため）。"""
        # 一泊二日: MeCab分解だと いち+はく+ふた+か になり誤読
        result = _mecab_to_hiragana("一泊二日の旅")
        assert "一泊二日" in result, f"expected 一泊二日 preserved, got: {result}"

    def test_counter_span_with_arabic_digits(self):
        """アラビア数字+カウンター漢字も保持される。"""
        result = _mecab_to_hiragana("3人は1泊2日で")
        assert "3人" in result
        assert "1泊2日" in result

    def test_counter_nichikan_preserved(self):
        """日間 (duration counter) も 数詞+日間 として保持される。"""
        result = _mecab_to_hiragana("3日間ずっと")
        assert "3日間" in result

    def test_counter_nensei_preserved(self):
        """年生 (学年) も 数詞+年生 として保持され MeCab で生→なま と誤変換されない。"""
        result = _mecab_to_hiragana("6年生の子供")
        assert "6年生" in result
        assert "なま" not in result

    def test_non_counter_sei_still_converted(self):
        """カウンター文脈にない 生 は従来通り変換される（なま/い）。"""
        assert "なま" in _mecab_to_hiragana("生卵")
        assert "うまれる" in _mecab_to_hiragana("生まれる")

    def test_non_counter_kanji_still_converted(self):
        """カウンター文脈にない同じ漢字は従来通り変換される。"""
        # その日 の 日 は名詞(単独)なので ひ に変換
        result = _mecab_to_hiragana("その日のこと")
        assert "ひ" in result
        assert "日" not in result
        # 月が綺麗 の 月 も名詞
        result = _mecab_to_hiragana("月が綺麗")
        assert "つき" in result
        assert "月" not in result

    def test_itsunomanika_idiom(self):
        """「いつの間にか」は慣用句で 間→ま 固定（MeCab は間→あいだと誤読）。"""
        result = _mecab_to_hiragana("いつの間にか眠っていた")
        assert "いつのまにか" in result
        assert "いつのあいだ" not in result

    def test_shiranu_maani_idiom(self):
        """「知らない間に」「知らぬ間に」も 間→ま 固定。"""
        assert "しらないまに" in _mecab_to_hiragana("知らない間に消えた")
        assert "しらぬまに" in _mecab_to_hiragana("知らぬ間に")

    def test_atto_iu_ma_idiom(self):
        """「あっという間」は 間→ま 固定。"""
        result = _mecab_to_hiragana("あっという間に終わった")
        assert "あっというま" in result

    def test_adjective_kusai_preserved(self):
        """形容詞用法の 臭い はくさい として読まれる（MeCab が正しく判別する）。"""
        result = _mecab_to_hiragana("魚が臭い")
        assert "くさい" in result

    def test_user_config_merges_with_defaults(self, tmp_path, monkeypatch):
        """UIから追加した辞書エントリがデフォルトとマージされて適用される。"""
        import app.config as config_module
        config_path = tmp_path / "config.toml"
        monkeypatch.setattr(config_module, "CONFIG_PATH", config_path)
        config_module.save_config({
            "reading_overrides": {"某": "ぼう"},
            "compound_replacements": {"お爺さん": "おじいさん"},
            "keep_as_kanji": ["葉"],
        })
        # User-added reading override applied
        assert "ぼう" in _mecab_to_hiragana("某氏が来た")
        # User-added compound replacement applied
        assert "おじいさん" in _mecab_to_hiragana("お爺さんは優しい")
        # User-added keep-as-kanji applied
        result = _mecab_to_hiragana("葉が落ちた")
        assert "葉" in result
        # Default 母→漢字保持 still works
        assert "母" in _mecab_to_hiragana("母を見た")
        # Default 私→わたし still works
        assert "わたし" in _mecab_to_hiragana("私は行く")

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
