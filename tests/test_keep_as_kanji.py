"""「漢字のまま残す語」がパイプライン全体で守られることのテスト。

ユーザー報告: keep_as_kanji に語を入れても効かず、AI校正(Gemini)にも
その情報が渡っていない。原因は 3 つあり、それぞれを検証する。
  1. MeCab が複数トークンに割る語 (不気味 = 不+気味) は surface 一致しない
  2. 同じ語が compound_replacements にもあると、そちらが先に効いて潰す
  3. AI校正プロンプトに keep_as_kanji が載っていない
"""

from unittest.mock import patch

import pytest

import app.config as config_module
from app.services.text_processor import (
    _format_keep_kanji_block,
    _kept_kanji_in,
    _protect_keep_as_kanji,
    _render_proofread_prompt,
    build_narration_source,
)


def _mecab_available() -> bool:
    try:
        import MeCab  # noqa: F401
    except ImportError:
        return False
    return True


@pytest.fixture
def user_config(tmp_path, monkeypatch):
    """ユーザー設定を一時ファイルに差し替えるフィクスチャ。"""
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr(config_module, "CONFIG_PATH", config_path)

    def _set(**values):
        config_module.save_config(values)

    return _set


class TestProtectKeepAsKanji:
    """MeCab 前の退避が、複合語を壊さずに対象語だけ守ること。"""

    def test_multi_token_word_is_protected(self):
        """MeCab が 不+気味 に割る語もプレースホルダで守られる。"""
        text, holders = _protect_keep_as_kanji("とても不気味だった", {"不気味"})
        assert "不気味" not in text
        assert list(holders.values()) == ["不気味"]
        # 復元すると元に戻る
        for key, original in holders.items():
            text = text.replace(key, original)
        assert text == "とても不気味だった"

    def test_single_kanji_guarded_on_both_sides(self):
        """単漢字は複合語の一部を食わない (話 が 会話 を壊さない)。"""
        text, holders = _protect_keep_as_kanji("静かな会話", {"話"})
        assert text == "静かな会話"
        assert holders == {}

    def test_single_kanji_fires_when_standalone(self):
        """前後が漢字でなければ単漢字でも守られる。"""
        text, holders = _protect_keep_as_kanji("その話をした", {"話"})
        assert "話" not in text
        assert list(holders.values()) == ["話"]

    def test_multi_kanji_guarded_on_preceding_side(self):
        """複数字は直前が漢字なら発火しない (接頭側の複合語を壊さない)。"""
        text, holders = _protect_keep_as_kanji("大不気味", {"不気味"})
        assert text == "大不気味"
        assert holders == {}

    def test_longer_word_wins(self):
        """長い語から順に処理するので 不気味 が 気味 に横取りされない。"""
        text, holders = _protect_keep_as_kanji("不気味な話", {"気味", "不気味"})
        assert "不気味" in holders.values()
        assert "気味" not in text


@pytest.mark.skipif(not _mecab_available(), reason="MeCab not installed")
class TestKeepAsKanjiEndToEnd:
    def test_multi_token_word_survives_conversion(self, user_config):
        """不気味 を keep_as_kanji に入れると、ひらがな化されず漢字で残る。"""
        from app.services.text_processor import _mecab_to_hiragana

        user_config(keep_as_kanji=["不気味"])
        assert "不気味" in _mecab_to_hiragana("とても不気味だった")

    def test_keep_as_kanji_beats_compound_replacement(self, user_config):
        """同じ語が両方の辞書にあるとき keep_as_kanji を優先する。

        複合語置換は MeCab より前に走るので、優先順位を決めないと
        keep_as_kanji が一度も発火しない。
        """
        from app.services.text_processor import _mecab_to_hiragana

        user_config(
            keep_as_kanji=["不気味"],
            compound_replacements={"不気味": "ぶきみ"},
        )
        result = _mecab_to_hiragana("とても不気味だった")
        assert "不気味" in result
        assert "ぶきみ" not in result

    def test_compound_replacement_still_applies_without_conflict(self, user_config):
        """競合しない複合語置換はこれまで通り効く。"""
        from app.services.text_processor import _mecab_to_hiragana

        user_config(
            keep_as_kanji=["不気味"],
            compound_replacements={"お爺さん": "おじいさん"},
        )
        assert "おじいさん" in _mecab_to_hiragana("お爺さんが立っていた")


class TestProofreadPromptInjection:
    """AI校正に keep_as_kanji が伝わること。"""

    def test_kept_kanji_in_lists_only_present_words(self, user_config):
        user_config(keep_as_kanji=["不気味", "母", "梁"])
        assert _kept_kanji_in("とても不気味な母だった") == ["不気味", "母"]

    def test_kept_kanji_in_dedupes_and_sorts_long_first(self, user_config):
        user_config(keep_as_kanji=["気味", "不気味"])
        result = _kept_kanji_in("不気味 不気味 気味")
        assert result[0] == "不気味"
        assert result.count("不気味") == 1

    def test_block_is_empty_without_words(self):
        assert _format_keep_kanji_block([]) == ""

    def test_block_lists_each_word(self):
        block = _format_keep_kanji_block(["不気味", "母"])
        assert "漢字のまま残す語" in block
        assert "- 不気味" in block
        assert "- 母" in block

    def test_render_fills_placeholder(self):
        template = "原文:{raw}\n読み:{processed}\n{keep_kanji}"
        out = _render_proofread_prompt(template, raw="R", processed="P", keep_kanji="K")
        assert out == "原文:R\n読み:P\nK"

    def test_render_prepends_when_placeholder_missing(self):
        """{keep_kanji} の無い旧テンプレートでも keep 情報が届く。"""
        template = "原文:{raw}\n読み:{processed}"
        out = _render_proofread_prompt(template, raw="R", processed="P", keep_kanji="KEEP\n")
        assert out.startswith("KEEP\n")
        assert "原文:R" in out

    def test_render_tolerates_braces_in_prompt(self):
        """プロンプト本文に { } があっても KeyError にならない (format 不使用)。"""
        template = "注意: {未定義} は無視\n{raw}/{processed}"
        out = _render_proofread_prompt(template, raw="R", processed="P", keep_kanji="")
        assert "{未定義}" in out
        assert "R/P" in out


class TestProofreadGuard:
    """AI が keep_as_kanji をひらがな化したら結果を捨てること。"""

    def _run(self, ai_output, user_config):
        user_config(
            keep_as_kanji=["不気味"],
            ai_proofread_prompt="{raw}|{processed}|{keep_kanji}",
            text_model="gemini-2.5-flash",
        )

        class _Resp:
            text = ai_output

        with patch(
            "app.services.text_processor._gemini_generate_with_retry",
            return_value=_Resp(),
        ), patch("app.services.text_processor.get_gemini_text", return_value=object()):
            from app.services.text_processor import _ai_proofread

            return _ai_proofread("とても不気味なはなし", "とても不気味な話")

    def test_rejects_output_that_hiraganizes_kept_word(self, user_config):
        result = self._run("とてもぶきみなはなし", user_config)
        assert result == "とても不気味なはなし"

    def test_accepts_output_that_keeps_the_word(self, user_config):
        result = self._run("とても不気味なばなし", user_config)
        assert result == "とても不気味なばなし"


@pytest.mark.skipif(not _mecab_available(), reason="MeCab not installed")
class TestNarrationSourceKeepsKeptWords:
    def test_kept_word_stays_kanji_in_narration_source(self, user_config):
        user_config(keep_as_kanji=["不気味"])
        assert "不気味" in build_narration_source("とても不気味だった")
