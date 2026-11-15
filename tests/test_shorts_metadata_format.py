"""Shorts のタイトル・説明文がチャンネルの定型フォーマットに従うことのテスト。

ユーザー報告: 「タイトルとか説明文もいままでのフォーマットを準拠していない」。
原因は LLM 生成メタデータを常に優先していたこと。LLM は釣りタイトルを書くが、
挨拶・引用元・BGMクレジット・タグ構成を一切踏襲しない。
"""

from unittest.mock import MagicMock, patch

import pytest

import app.config as config_module
from app.models import Story


@pytest.fixture
def user_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "CONFIG_PATH", tmp_path / "config.toml")

    def _set(**values):
        config_module.save_config(values)

    return _set


@pytest.fixture
def short_story(tmp_path, monkeypatch):
    import app.utils.paths as paths

    monkeypatch.setattr(paths, "OUTPUT_BASE", tmp_path / "output")
    story = Story(
        id=1, title="夏休みの真夜中で", url="https://hhs.example/?p=1",
        content_type="short", source="hhs", author="",
    )
    paths.raw_content_path(story.title, "short").write_text("本文", encoding="utf-8")
    paths.video_path(story.title, "short").write_bytes(b"mp4")
    return story


def _run_upload(story, monkeypatch):
    """アップロード処理を走らせ、YouTube に渡された引数を返す。"""
    from app.pipeline import stages

    captured = {}

    def _fake_upload(**kwargs):
        captured.update(kwargs)
        return {
            "video_id": "vid1", "title": kwargs["title"],
            "url": "", "privacy_status": "private",
        }

    monkeypatch.setattr(
        "app.services.youtube_uploader.is_authenticated", lambda: True,
    )
    monkeypatch.setattr(
        "app.services.youtube_uploader.upload_video", _fake_upload,
    )
    monkeypatch.setattr(
        "app.services.youtube_uploader.resolve_publish_time", lambda: None,
    )
    monkeypatch.setattr("app.utils.ffmpeg.get_audio_duration", lambda p: 60.0)
    monkeypatch.setattr(
        "app.services.voice_generator.get_speaker_name", lambda: "ナレーター",
    )
    monkeypatch.setattr(stages.db, "mark_uploaded", MagicMock())
    stages.do_youtube_upload_short(story)
    return captured


class TestTemplateIsDefault:
    def test_default_config_uses_template(self):
        assert config_module._DEFAULTS["shorts_metadata_source"] == "template"

    def test_title_follows_template(self, short_story, user_config, monkeypatch):
        user_config()
        captured = _run_upload(short_story, monkeypatch)
        assert captured["title"] == "【怪談朗読】夏休みの真夜中で｜怖い話 #Shorts"

    def test_llm_is_not_called(self, short_story, user_config, monkeypatch):
        """既定ではメタデータ生成のために Gemini を呼ばない (課金も止まらない)。"""
        user_config()
        with patch(
            "app.services.text_processor.generate_shorts_metadata",
        ) as gen:
            _run_upload(short_story, monkeypatch)
        gen.assert_not_called()

    def test_description_has_channel_boilerplate(self, short_story, user_config, monkeypatch):
        user_config()
        desc = _run_upload(short_story, monkeypatch)["description"]
        assert "百鬼朗読へようこそ" in desc
        assert "チャンネル登録・高評価" in desc

    def test_description_credits_bgm(self, short_story, user_config, monkeypatch):
        """DOVA-SYNDROME の規約上 BGM クレジットは必須。"""
        user_config()
        desc = _run_upload(short_story, monkeypatch)["description"]
        assert "Where the Light Never Speaks" in desc
        assert "dova-s.jp" in desc

    def test_source_credit_appears_exactly_once(self, short_story, user_config, monkeypatch):
        """テンプレートに出典が入っているので、追記すると二重になる。"""
        user_config()
        desc = _run_upload(short_story, monkeypatch)["description"]
        assert desc.count("引用元") == 1

    def test_hhs_tag_present(self, short_story, user_config, monkeypatch):
        user_config()
        captured = _run_upload(short_story, monkeypatch)
        assert "ホラホリ" in captured["description"]
        assert "ホラホリ" in captured["tags"]

    def test_kikikaikai_uses_its_own_template(self, short_story, user_config, monkeypatch):
        user_config()
        short_story.source = "kikikaikai"
        short_story.author = "投稿者A"
        desc = _run_upload(short_story, monkeypatch)["description"]
        assert "奇々怪々" in desc
        assert "投稿者A" in desc
        assert desc.count("引用元") == 1


class TestLlmModeStillCredits:
    """LLM を選んだ場合でも、出典と BGM クレジットは必ず付く。"""

    def _llm_desc(self, story, user_config, monkeypatch):
        user_config(shorts_metadata_source="llm")
        with patch(
            "app.services.text_processor.generate_shorts_metadata",
            return_value={
                "title": "😱【怖い話】釣りタイトル…",
                "description": "あらすじ\n\n#怪談 #Shorts",
            },
        ):
            return _run_upload(story, monkeypatch)

    def test_llm_title_used(self, short_story, user_config, monkeypatch):
        captured = self._llm_desc(short_story, user_config, monkeypatch)
        assert captured["title"] == "😱【怖い話】釣りタイトル…"

    def test_llm_description_gets_source_credit(self, short_story, user_config, monkeypatch):
        desc = self._llm_desc(short_story, user_config, monkeypatch)["description"]
        assert "引用元: HHS図書館" in desc
        assert desc.count("引用元") == 1

    def test_llm_description_gets_bgm_credit(self, short_story, user_config, monkeypatch):
        desc = self._llm_desc(short_story, user_config, monkeypatch)["description"]
        assert "Where the Light Never Speaks" in desc
        assert "dova-s.jp" in desc

    def test_llm_failure_falls_back_to_template(self, short_story, user_config, monkeypatch):
        user_config(shorts_metadata_source="llm")
        with patch(
            "app.services.text_processor.generate_shorts_metadata",
            side_effect=RuntimeError("429 RESOURCE_EXHAUSTED"),
        ):
            captured = _run_upload(short_story, monkeypatch)
        assert captured["title"] == "【怪談朗読】夏休みの真夜中で｜怖い話 #Shorts"
        assert "百鬼朗読へようこそ" in captured["description"]
        assert captured["description"].count("引用元") == 1
