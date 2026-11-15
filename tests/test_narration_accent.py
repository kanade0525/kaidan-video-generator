"""アクセント改善 (漢字混じり原稿の検証採用) のテスト。

ひらがなだけの原稿は VOICEVOX が語境界を推定できずアクセントが平板になる。
読みを矯正した語だけをかなにした「漢字混じり原稿」を渡すと自然になるが、
読みが変わってしまっては本末転倒なので、audio_query のモーラ列が一致した
チャンクだけ採用する。ここではその判定ロジックを検証する。
"""

from unittest.mock import patch

import pytest

import app.config as config_module
from app.services.voice_generator import (
    _maybe_use_accent_source,
    _mora_signature,
    normalize_speech_text,
)


def _mecab_available() -> bool:
    try:
        import MeCab  # noqa: F401
    except ImportError:
        return False
    return True


@pytest.fixture
def user_config(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr(config_module, "CONFIG_PATH", config_path)

    def _set(**values):
        config_module.save_config(values)

    return _set


class TestNormalizeSpeechText:
    def test_newline_becomes_period_by_default(self, user_config):
        user_config(voice_newline_mode="period")
        assert normalize_speech_text("あ\nい") == "あ。い"

    def test_no_double_period_when_line_already_ends_with_one(self, user_config):
        """行末が「。」なら区切りを足さない (「。。」で不自然に間が空いていた)。"""
        user_config(voice_newline_mode="period")
        assert normalize_speech_text("あ。\nい。") == "あ。い。"

    def test_collapses_existing_duplicate_punctuation(self, user_config):
        user_config(voice_newline_mode="period")
        assert normalize_speech_text("あ、。い") == "あ。い"

    def test_comma_mode(self, user_config):
        user_config(voice_newline_mode="comma")
        assert normalize_speech_text("あ\nい") == "あ、い"

    def test_none_mode_joins_directly(self, user_config):
        user_config(voice_newline_mode="none")
        assert normalize_speech_text("あ\nい") == "あい"

    def test_blank_lines_dropped(self, user_config):
        user_config(voice_newline_mode="period")
        assert normalize_speech_text("あ\n\n\nい") == "あ。い"

    def test_carriage_returns_handled(self, user_config):
        user_config(voice_newline_mode="period")
        assert normalize_speech_text("あ\r\nい") == "あ。い"

    def test_explicit_mode_overrides_config(self, user_config):
        user_config(voice_newline_mode="period")
        assert normalize_speech_text("あ\nい", newline_mode="none") == "あい"


class TestMoraSignature:
    def test_strips_accent_and_phrase_marks(self):
        """アクセント位置・句切り・ポーズの違いは無視される。"""
        assert _mora_signature("コンニチワ'/セカイ、") == "コンニチワセカイ"

    def test_same_moras_different_accent_match(self):
        assert _mora_signature("ハシ'ガ") == _mora_signature("ハ'シガ")

    def test_different_moras_do_not_match(self):
        assert _mora_signature("ハシ'ガ") != _mora_signature("ハシ'ゴ")

    def test_devoicing_mark_ignored(self):
        assert _mora_signature("デ_ス'") == "デス"

    def test_empty_is_safe(self):
        assert _mora_signature("") == ""
        assert _mora_signature(None) == ""


class TestMaybeUseAccentSource:
    """読みが一致したときだけ漢字混じり原稿を採用する。"""

    HIRA_QUERY = {"kana": "キノ'ウ/デキ'ゴト", "marker": "hiragana"}

    def _run(self, alt_kana, user_config, **cfg):
        user_config(voice_accent_mode="verified_kanji", **cfg)
        alt_query = {"kana": alt_kana, "marker": "kanji"}
        with patch(
            "app.services.voice_generator._audio_query", return_value=alt_query,
        ):
            return _maybe_use_accent_source(
                dict(self.HIRA_QUERY), "きのうのできごと", "昨日の出来事", 1,
            )

    def test_adopts_when_moras_match(self, user_config):
        """アクセント位置だけ違う = 読みは同じ → 漢字版を採用。"""
        result = self._run("キ'ノウ/デキゴ'ト", user_config)
        assert result["marker"] == "kanji"

    def test_rejects_when_moras_differ(self, user_config):
        """1モーラでも違えば読みが変わっている → ひらがな版を維持。"""
        result = self._run("サクジツ/デキ'ゴト", user_config)
        assert result["marker"] == "hiragana"

    def test_falls_back_on_api_error(self, user_config):
        """照合中の例外でナレーション生成を止めない。"""
        user_config(voice_accent_mode="verified_kanji")
        with patch(
            "app.services.voice_generator._audio_query",
            side_effect=RuntimeError("boom"),
        ):
            result = _maybe_use_accent_source(
                dict(self.HIRA_QUERY), "きのう", "昨日", 1,
            )
        assert result["marker"] == "hiragana"

    def test_empty_source_is_ignored(self, user_config):
        user_config(voice_accent_mode="verified_kanji")
        result = _maybe_use_accent_source(dict(self.HIRA_QUERY), "きのう", "   ", 1)
        assert result["marker"] == "hiragana"


class TestAccentModeSwitch:
    def test_hiragana_mode_never_queries_alternative(self, user_config):
        """従来モードでは漢字混じり原稿を一切使わない。"""
        from app.services.voice_generator import text_to_speech

        user_config(voice_accent_mode="hiragana")
        with patch(
            "app.services.voice_generator._audio_query", return_value={"kana": "ア"},
        ) as q, patch(
            "app.services.voice_generator._synthesize", return_value=b"wav",
        ):
            text_to_speech("あ", accent_source="亜")
        assert q.call_count == 1

    def test_verified_mode_queries_both(self, user_config):
        from app.services.voice_generator import text_to_speech

        user_config(voice_accent_mode="verified_kanji")
        with patch(
            "app.services.voice_generator._audio_query", return_value={"kana": "ア"},
        ) as q, patch(
            "app.services.voice_generator._synthesize", return_value=b"wav",
        ):
            text_to_speech("あ", accent_source="亜")
        # ひらがな原稿 + 文ごとの照合 2回 + 採用後の最終確認
        assert q.call_count > 1


class TestQueryParams:
    def test_pause_fields_applied_when_present(self, user_config):
        from app.services.voice_generator import _apply_query_params

        user_config(
            pre_phoneme_length=0.4,
            post_phoneme_length=0.6,
            pause_length_scale=1.5,
        )
        query = {
            "prePhonemeLength": 0.1,
            "postPhonemeLength": 0.1,
            "pauseLengthScale": 1.0,
        }
        _apply_query_params(query)
        assert query["prePhonemeLength"] == 0.4
        assert query["postPhonemeLength"] == 0.6
        assert query["pauseLengthScale"] == 1.5

    def test_missing_fields_are_not_added(self, user_config):
        """古い VOICEVOX エンジンに存在しないフィールドは足さない。"""
        from app.services.voice_generator import _apply_query_params

        user_config(pause_length_scale=2.0)
        query = {"prePhonemeLength": 0.1}
        _apply_query_params(query)
        assert "pauseLengthScale" not in query


@pytest.mark.skipif(not _mecab_available(), reason="MeCab not installed")
class TestBuildNarrationSource:
    def test_keeps_kanji_that_mecab_reads_correctly(self, user_config):
        from app.services.text_processor import build_narration_source

        user_config()
        result = build_narration_source("昨日の出来事")
        assert "昨日" in result
        assert "出来事" in result

    def test_converts_words_with_reading_override(self, user_config):
        """辞書で読みを直した語はかなにする (漢字だと誤読されるから)。"""
        from app.services.text_processor import build_narration_source

        user_config(reading_overrides={"私": "わたし"})
        assert "わたし" in build_narration_source("私は行く")

    def test_keeps_particles_as_written(self, user_config):
        """漢字混じり文なら助詞の は/へ は VOICEVOX が正しく読む。"""
        from app.services.text_processor import build_narration_source

        user_config()
        result = build_narration_source("学校へ行く")
        assert "へ" in result
        assert "え行く" not in result

    def test_preserves_newlines(self, user_config):
        from app.services.text_processor import build_narration_source

        user_config()
        assert build_narration_source("昨日\n今日").count("\n") == 1

    def test_same_reading_as_hiragana_version(self, user_config):
        """漢字混じり原稿を再変換すると、ひらがな原稿と一致する。

        VOICEVOX 側の検証に頼る前段のサニティチェック。
        """
        from app.services.text_processor import _mecab_to_hiragana, build_narration_source

        user_config()
        text = "昨日の出来事は本当に怖かった。"
        assert _mecab_to_hiragana(build_narration_source(text)) == _mecab_to_hiragana(text)


class TestMergeVerifiedSentences:
    """文ごとに読みを照合して、一致した文だけ漢字混じりにする。"""

    def _query_by_text(self, table):
        """テキスト→kana の対応表で audio_query を差し替える。"""
        def _fake(text, speaker_id):
            return {"kana": table[text]}
        return _fake

    def test_keeps_only_matching_sentences(self, user_config):
        from app.services.voice_generator import merge_verified_sentences

        user_config()
        table = {
            "きのうのできごと。": "キノ'ウノデキゴト",
            "昨日の出来事。": "キノウ'ノデキ'ゴト",   # 読み同じ → 採用
            "つらかった。": "ツラ'カッタ",
            "辛かった。": "カラ'カッタ",             # 読み違う → 不採用
        }
        with patch(
            "app.services.voice_generator._audio_query",
            side_effect=self._query_by_text(table),
        ):
            result = merge_verified_sentences(
                "きのうのできごと。つらかった。",
                "昨日の出来事。辛かった。",
                1,
            )
        assert result == "昨日の出来事。つらかった。"

    def test_returns_none_when_sentence_counts_differ(self, user_config):
        from app.services.voice_generator import merge_verified_sentences

        user_config()
        assert merge_verified_sentences("あ。い。", "亜。", 1) is None

    def test_identical_sentences_skip_the_api(self, user_config):
        """かなだけの文は照合不要なので audio_query を呼ばない。"""
        from app.services.voice_generator import merge_verified_sentences

        user_config()
        with patch("app.services.voice_generator._audio_query") as q:
            result = merge_verified_sentences("ああ。", "ああ。", 1)
        assert result == "ああ。"
        q.assert_not_called()

    def test_empty_input_returns_none(self, user_config):
        from app.services.voice_generator import merge_verified_sentences

        user_config()
        assert merge_verified_sentences("", "", 1) is None


class TestSentenceLevelAdoption:
    """チャンク全体では不一致でも、一致した文だけは漢字混じりで読ませる。"""

    def test_partial_adoption_is_used(self, user_config):
        user_config(voice_accent_mode="verified_kanji")
        table = {
            "きのうのできごと。つらかった。": "キノ'ウノデキゴトツラカッタ",
            "きのうのできごと。": "キノ'ウノデキゴト",
            "昨日の出来事。": "キノウ'ノデキ'ゴト",
            "つらかった。": "ツラ'カッタ",
            "辛かった。": "カラ'カッタ",
            # 部分採用した最終原稿。全体の読みはひらがな版と一致する
            "昨日の出来事。つらかった。": "キノウ'ノデキ'ゴト/ツラ'カッタ",
        }

        def _fake(text, speaker_id):
            return {"kana": table[text], "text": text}

        with patch("app.services.voice_generator._audio_query", side_effect=_fake):
            result = _maybe_use_accent_source(
                {"kana": "キノ'ウノデキゴトツラカッタ"},
                "きのうのできごと。つらかった。",
                "昨日の出来事。辛かった。",
                1,
            )
        assert result["text"] == "昨日の出来事。つらかった。"

    def test_no_adoption_keeps_original_query(self, user_config):
        """どの文も採用できないときは問い合わせ直さず元の結果を返す。"""
        user_config(voice_accent_mode="verified_kanji")

        def _fake(text, speaker_id):
            return {"kana": "サクジツ" if text == "昨日。" else "キノ'ウ", "marker": text}

        with patch("app.services.voice_generator._audio_query", side_effect=_fake):
            result = _maybe_use_accent_source(
                {"kana": "キノ'ウ", "marker": "hiragana"}, "きのう。", "昨日。", 1,
            )
        assert result["marker"] == "hiragana"

    def test_final_check_rejects_changed_reading(self, user_config):
        """文単位では一致しても、つなぎ直して読みが変わったら差し戻す。"""
        user_config(voice_accent_mode="verified_kanji")
        table = {
            # 文単位ではどちらも一致する
            "きのう。": "キノ'ウ",
            "昨日。": "キノウ'",
            "できごと。": "デキ'ゴト",
            "出来事。": "デキゴ'ト",
            # ところが結合すると別の読みになる (境界での再解析)
            "昨日。出来事。": "サクジツデキゴト",
        }

        def _fake(text, speaker_id):
            return {"kana": table[text], "marker": text}

        with patch("app.services.voice_generator._audio_query", side_effect=_fake):
            result = _maybe_use_accent_source(
                {"kana": "キノ'ウデキ'ゴト", "marker": "hiragana"},
                "きのう。できごと。",
                "昨日。出来事。",
                1,
            )
        assert result["marker"] == "hiragana"
