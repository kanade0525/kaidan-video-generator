"""Tests for the Shorts image layout (post-merge of title BG + scene 0).

Brand rule: a Short produces exactly 2 PNGs:
- 000_title_card.png (composited from the first AI image + title overlay)
- scene_000.png (a second AI image, used during the narration slideshow)

This test locks in:
1. Shorts produce exactly those two filenames (not 3+ as before).
2. The first AI generation is reused for the title card BG (no separate
   `_generate_title_bg_prompt` call on the Shorts path).
3. Long-form is unaffected (still 1 title + 3 scenes by default).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from app.services import image_generator as ig


_SHORT_CFG = {
    "shorts_num_scenes": 2,
    "num_scenes": 3,
    "image_rate_limit": 0,
    "shorts_vhs_enabled": True,
    "shorts_image_aspect_ratio": "9:16",
}


def _fake_extract(text, title, num_scenes, model=None, style=None):
    return [f"prompt {i}" for i in range(num_scenes)]


def _fake_image(prompt, *, model=None, size=None, aspect_ratio=None, style_override=None):
    return b"\x89PNG\r\n\x1a\nfake-ai"


def _fake_titlecard(title, width, height, bg_image_data=None, category="怪談", template=None):
    return b"\x89PNG\r\n\x1a\nfake-titlecard"


def _fake_vhs(data):
    return data + b"_vhs"


def _fake_cfg(key):
    return _SHORT_CFG.get(key, "")


def _generate(content_type: str, calls: dict) -> list[str]:
    """Run generate_images_for_story under mocks; return sorted output filenames."""
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)

        def tracked_image(prompt, *, model=None, size=None, aspect_ratio=None, style_override=None):
            calls.setdefault("image_ai_prompts", []).append(prompt)
            return _fake_image(prompt)

        def tracked_title_bg(text, title, style=None):
            calls.setdefault("title_bg_prompt_calls", 0)
            calls["title_bg_prompt_calls"] += 1
            return "long-form title bg prompt"

        with patch.object(ig, "generate_image_ai", side_effect=tracked_image), \
             patch.object(ig, "extract_scene_prompts", side_effect=_fake_extract), \
             patch.object(ig, "create_title_card", side_effect=_fake_titlecard), \
             patch.object(ig, "_generate_title_bg_prompt", side_effect=tracked_title_bg), \
             patch.object(ig, "degrade_to_vhs", side_effect=_fake_vhs), \
             patch.object(ig, "cfg_get", side_effect=_fake_cfg):
            ig.generate_images_for_story(
                "story body", "テスト", out, content_type=content_type,
            )

        return sorted(p.name for p in out.glob("*.png"))


def test_short_produces_exactly_two_pngs():
    files = _generate("short", calls={})
    assert files == ["000_title_card.png", "scene_000.png"], files


def test_short_skips_separate_title_bg_prompt():
    """Shorts must not call _generate_title_bg_prompt — scene 0 is reused."""
    calls: dict = {}
    _generate("short", calls)
    assert calls.get("title_bg_prompt_calls", 0) == 0
    # Two AI image calls: scene 0 (title bg) + scene 1 (bare scene_000.png).
    assert len(calls["image_ai_prompts"]) == 2


def test_long_form_unchanged():
    files = _generate("long", calls={})
    assert files == [
        "000_title_card.png",
        "scene_000.png",
        "scene_001.png",
        "scene_002.png",
    ], files


def test_long_form_still_uses_separate_title_bg_prompt():
    calls: dict = {}
    _generate("long", calls)
    assert calls.get("title_bg_prompt_calls", 0) == 1
    # 1 title bg + 3 scenes = 4 image AI calls
    assert len(calls["image_ai_prompts"]) == 4
