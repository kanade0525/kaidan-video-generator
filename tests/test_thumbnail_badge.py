"""サムネイル左上のバッジがカテゴリに追従することのテスト。

ユーザー指摘: 「怪談と人怖は違うのでバッジだけは切り替える必要がある」。
以前は全作品に「怪談朗読」を貼っていた。
"""

from unittest.mock import patch

import pytest

import app.config as config_module
from app.services.image_generator import (
    _badge_instruction,
    generate_shorts_thumbnail,
    thumbnail_badge,
)


@pytest.fixture
def user_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "CONFIG_PATH", tmp_path / "config.toml")

    def _set(**values):
        config_module.save_config(values)

    return _set


class TestThumbnailBadge:
    @pytest.mark.parametrize("category", [
        "怪談", "人怖", "不思議な話", "その他の話", "厳せん話特集",
    ])
    def test_category_is_used_verbatim(self, category, user_config):
        user_config()
        assert thumbnail_badge(category) == category

    def test_override_replaces_long_category(self, user_config):
        """バッジに収まらない長いカテゴリは差し替える。"""
        user_config()
        assert thumbnail_badge("合掌コンクール応募作品") == "ホラホリ図書館"

    def test_no_category_means_no_badge(self, user_config):
        user_config()
        assert thumbnail_badge("") == ""
        assert thumbnail_badge(None) == ""
        assert thumbnail_badge("   ") == ""

    def test_overrides_are_configurable(self, user_config):
        user_config(shorts_badge_overrides={"人怖": "人間の怖い話"})
        assert thumbnail_badge("人怖") == "人間の怖い話"
        assert thumbnail_badge("怪談") == "怪談"


class TestBadgeInstruction:
    def test_badge_text_appears_in_instruction(self):
        assert "人怖" in _badge_instruction("人怖")
        assert "Top-LEFT" in _badge_instruction("人怖")

    def test_empty_badge_tells_model_to_omit_it(self):
        """カテゴリなしの作品に「カテゴリなし」と描かせない。"""
        text = _badge_instruction("")
        assert "Do NOT draw any stamp" in text
        assert "カテゴリなし" not in text


class TestPromptWiring:
    def _prompt_for(self, category, user_config):
        user_config(
            shorts_thumbnail_prompt="T={title} S={subject} B={badge_instruction}",
            shorts_image_model="gemini-3.1-flash-image",
        )
        captured = {}

        def _fake(prompt, **kwargs):
            captured["prompt"] = prompt
            return b"png"

        with patch("app.services.image_generator.generate_image_ai", side_effect=_fake), \
             patch(
                 "app.services.image_generator.thumbnail_subject",
                 return_value="a dark corridor",
             ):
            generate_shorts_thumbnail("テスト怪談", "本文", category)
        return captured["prompt"]

    def test_category_badge_reaches_the_prompt(self, user_config):
        assert "人怖" in self._prompt_for("人怖", user_config)

    def test_uncategorised_story_gets_no_badge(self, user_config):
        prompt = self._prompt_for("", user_config)
        assert "Do NOT draw any stamp" in prompt

    def test_title_and_subject_still_injected(self, user_config):
        prompt = self._prompt_for("怪談", user_config)
        assert "テスト怪談" in prompt
        assert "a dark corridor" in prompt


class TestStagePassesCategory:
    def test_missing_category_is_not_defaulted_to_kaidan(self, tmp_path, monkeypatch):
        """カテゴリ未設定を "怪談" で埋めない。"""
        import app.utils.paths as paths
        from app.models import Story
        from app.pipeline.stages import do_images_short

        monkeypatch.setattr(paths, "OUTPUT_BASE", tmp_path / "out")
        story = Story(id=1, title="無分類の話", content_type="short", categories=[])
        paths.raw_content_path(story.title, "short").write_text("本文", encoding="utf-8")

        captured = {}

        def _fake(text, title, output_dir, category="怪談", **kw):
            captured["category"] = category
            return []

        monkeypatch.setattr(
            "app.services.image_generator.generate_images_for_story", _fake,
        )
        do_images_short(story)
        assert captured["category"] == ""

    def test_category_is_forwarded(self, tmp_path, monkeypatch):
        import app.utils.paths as paths
        from app.models import Story
        from app.pipeline.stages import do_images_short

        monkeypatch.setattr(paths, "OUTPUT_BASE", tmp_path / "out2")
        story = Story(id=1, title="人怖の話", content_type="short", categories=["人怖"])
        paths.raw_content_path(story.title, "short").write_text("本文", encoding="utf-8")

        captured = {}

        def _fake(text, title, output_dir, category="怪談", **kw):
            captured["category"] = category
            return []

        monkeypatch.setattr(
            "app.services.image_generator.generate_images_for_story", _fake,
        )
        do_images_short(story)
        assert captured["category"] == "人怖"
