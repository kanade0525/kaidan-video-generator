"""タイトル読み上げの読み間違い回帰テスト。

報告された読み間違いはここに1件ずつ足していく。二度と再発させないため。

背景: VOICEVOX は辞書に無い漢字を無音で読み飛ばす。一方でタイトルを一律
ひらがな化すると別の誤読が生まれる (箱根→ワコネ 等)。そのため
「モーラが落ちているときだけ」ひらがな版に差し替える。
"""

from unittest.mock import patch

import pytest

import app.config as config_module
from app.services.voice_generator import generate_title_audio, repair_dropped_title_reading


@pytest.fixture
def user_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "CONFIG_PATH", tmp_path / "config.toml")
    config_module.save_config({})
    return None


def _fake_query(table):
    """テキスト → VOICEVOX の kana を引く audio_query の差し替え。"""
    def _q(text, speaker_id):
        if text not in table:
            raise AssertionError(f"未定義の入力: {text!r}")
        return {"kana": table[text]}
    return _q


# ── 報告された読み間違い ────────────────────────────────────────
# (タイトル, VOICEVOXが原題を読んだ結果, ひらがな化した原稿, その読み, 期待する採用文)
REPORTED_MISREADINGS = [
    pytest.param(
        "彼に憑く部屋",
        "カ'レニ、_ク'/ヘヤ'",          # 「憑」が落ちて「つ」が消える
        "かれについくへや",
        "カ'レニ/ツ'_ク/ヘヤ'",
        "かれについくへや",
        id="彼に憑く部屋: 憑が読み飛ばされる",
    ),
]


class TestReportedMisreadings:
    @pytest.mark.parametrize(
        "title,raw_kana,hiragana,hira_kana,expected", REPORTED_MISREADINGS,
    )
    def test_dropped_reading_falls_back_to_hiragana(
        self, title, raw_kana, hiragana, hira_kana, expected, user_config,
    ):
        table = {title: raw_kana, hiragana: hira_kana}
        with patch(
            "app.services.voice_generator._audio_query", side_effect=_fake_query(table),
        ), patch(
            "app.services.text_processor._mecab_to_hiragana", return_value=hiragana,
        ):
            assert repair_dropped_title_reading(title) == expected


# ── ひらがな化すると悪化するので、触ってはいけないタイトル ──────────────
# (タイトル, 原題の読み, ひらがな化した原稿, その読み)
MUST_NOT_CHANGE = [
    pytest.param(
        "箱根の落ち武者", "ハコネノオチムシャ", "わこねのおちむしゃ", "ワコネノオチムシャ",
        id="箱根: 助詞は→わ の誤爆を避ける",
    ),
    pytest.param(
        "白い彼岸花", "シロイヒガンバナ", "しろいひがんはな", "シロイヒガンハナ",
        id="彼岸花: 連濁が失われる",
    ),
    pytest.param(
        "お兄ちゃん", "オニ'イチャン", "おあにちゃん", "オアニチャン",
        id="お兄ちゃん: 分割誤りで オアニ になる",
    ),
    pytest.param(
        "真昼間の怪", "マッピルマノカイ", "まひるまのかい", "マヒルマノカイ",
        id="真昼間: 促音が失われる",
    ),
]


class TestTitlesThatMustStayAsWritten:
    @pytest.mark.parametrize("title,raw_kana,hiragana,hira_kana", MUST_NOT_CHANGE)
    def test_same_length_reading_is_left_alone(
        self, title, raw_kana, hiragana, hira_kana, user_config,
    ):
        """モーラ数が同じなら読み落としではないので、原題のまま読ませる。"""
        table = {title: raw_kana, hiragana: hira_kana}
        with patch(
            "app.services.voice_generator._audio_query", side_effect=_fake_query(table),
        ), patch(
            "app.services.text_processor._mecab_to_hiragana", return_value=hiragana,
        ):
            assert repair_dropped_title_reading(title) == title


class TestFuriganaWins:
    def test_manual_furigana_skips_the_check(self, tmp_path, user_config):
        """手動のふりがなが最優先。点検も差し替えもしない。"""
        spoken = {}

        def _tts(text, **kwargs):
            spoken["text"] = text
            return b"wav"

        with patch("app.services.voice_generator.text_to_speech", side_effect=_tts), \
             patch(
                 "app.services.voice_generator.repair_dropped_title_reading",
             ) as repair:
            generate_title_audio("彼に憑く部屋", tmp_path / "t.wav", "かれについくへや")
        assert spoken["text"] == "かれについくへや"
        repair.assert_not_called()

    def test_without_furigana_the_check_runs(self, tmp_path, user_config):
        spoken = {}

        def _tts(text, **kwargs):
            spoken["text"] = text
            return b"wav"

        with patch("app.services.voice_generator.text_to_speech", side_effect=_tts), \
             patch(
                 "app.services.voice_generator.repair_dropped_title_reading",
                 return_value="かれについくへや",
             ):
            generate_title_audio("彼に憑く部屋", tmp_path / "t.wav")
        assert spoken["text"] == "かれについくへや"


class TestFailureIsSafe:
    def test_api_error_keeps_original_title(self, user_config):
        """点検に失敗しても読み上げ自体は止めない。"""
        with patch(
            "app.services.voice_generator._audio_query",
            side_effect=RuntimeError("voicevox down"),
        ):
            assert repair_dropped_title_reading("彼に憑く部屋") == "彼に憑く部屋"

    def test_mecab_unavailable_keeps_original_title(self, user_config):
        with patch(
            "app.services.voice_generator._audio_query", return_value={"kana": "カ'レニ"},
        ), patch(
            "app.services.text_processor._mecab_to_hiragana", return_value=None,
        ):
            assert repair_dropped_title_reading("彼に憑く部屋") == "彼に憑く部屋"

    def test_kana_only_title_is_untouched(self, user_config):
        """既にひらがなのタイトルは変換しても同じなので触らない。"""
        with patch(
            "app.services.voice_generator._audio_query", return_value={"kana": "アア"},
        ), patch(
            "app.services.text_processor._mecab_to_hiragana", return_value="ああ",
        ):
            assert repair_dropped_title_reading("ああ") == "ああ"
