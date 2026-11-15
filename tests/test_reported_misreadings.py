"""報告された本文の読み間違い回帰テスト。

ユーザーが音声を聴いて見つけた誤読を、1件ずつここに固定していく。
二度と再発させないため。新しい報告が来たら REPORTED に足すこと。
"""

import pytest

from app.services.text_processor import _mecab_to_hiragana


def _mecab_available() -> bool:
    try:
        import MeCab  # noqa: F401
    except ImportError:
        return False
    return True


pytestmark = pytest.mark.skipif(not _mecab_available(), reason="MeCab not installed")


# (原文, 読みに必ず含まれるべき文字列, 含まれてはいけない文字列, 出典)
REPORTED = [
    pytest.param(
        "集中治療室に入れと言われた", "はいれ", "しつにいれ",
        id="入れと: 命令形は はいれ (いれ ではない)",
    ),
    pytest.param(
        "部屋に入れと言われた", "はいれ", "やにいれ",
        id="入れと: 場所+に入れ は はいれ",
    ),
]


@pytest.mark.parametrize("text,must_contain,must_not_contain", REPORTED)
def test_reported_misreading_is_fixed(text, must_contain, must_not_contain):
    result = _mecab_to_hiragana(text)
    assert must_contain in result, f"{text} → {result}"
    assert must_not_contain not in result, f"{text} → {result}"


# 上の修正で壊してはいけない、他動詞「入れる」の読み
NOT_REGRESSED = [
    pytest.param("中に入れる", "いれる", id="入れる: 他動詞は いれる のまま"),
    pytest.param("ポケットに入れた", "いれた", id="入れた: 他動詞は いれた のまま"),
]


@pytest.mark.parametrize("text,must_contain", NOT_REGRESSED)
def test_transitive_reading_not_broken(text, must_contain):
    assert must_contain in _mecab_to_hiragana(text)
