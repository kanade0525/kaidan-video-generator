"""Tests for Shorts visual style profile diversification.

Goal: every Short pulls from a different visual aesthetic (VHS surveillance,
traditional Japanese house, ukiyo-e, ink wash, polaroid, etc.) so consecutive
uploads don't all collapse to the same dark/foggy/VHS look.

This test locks in:
1. The pool has multiple profiles with distinct names and style suffixes.
2. `pick_shorts_visual_style` is deterministic per title.
3. Different titles spread across profiles.
4. Not every profile applies VHS post-fx (so the chosen aesthetic shows through).
5. `generate_image_ai` honors `style_override` instead of the global config.
"""

from __future__ import annotations

from unittest.mock import patch

from app.services import image_generator as ig
from app.services.image_generator import (
    SHORTS_VISUAL_STYLES,
    pick_shorts_visual_style,
)


def test_profile_pool_has_multiple_distinct_styles():
    assert len(SHORTS_VISUAL_STYLES) >= 6
    names = [p.name for p in SHORTS_VISUAL_STYLES]
    assert len(set(names)) == len(names), "duplicate profile names"
    # style suffixes should not all be identical
    suffixes = {p.style_suffix for p in SHORTS_VISUAL_STYLES}
    assert len(suffixes) == len(SHORTS_VISUAL_STYLES)


def test_pick_is_deterministic_per_title():
    a = pick_shorts_visual_style("村の歓迎")
    b = pick_shorts_visual_style("村の歓迎")
    assert a.name == b.name


def test_pick_spreads_across_profiles():
    """A small set of varied titles should hit at least 4 distinct profiles."""
    titles = [
        "村の歓迎", "首吊り双六", "異質なリズム", "捏造された呪いの神社",
        "廃屋にあった日記帳", "〇体探し", "本当の守護霊", "ルカくん",
        "炙り出す", "爺ちゃんが呪われたのは", "深夜の電話", "鏡の中の女",
    ]
    picked = {pick_shorts_visual_style(t).name for t in titles}
    assert len(picked) >= 4, f"profiles clustered: {picked}"


def test_some_profiles_skip_vhs_postfx():
    """At least one non-VHS profile must exist — otherwise everything green."""
    apply_vhs_flags = [p.apply_vhs for p in SHORTS_VISUAL_STYLES]
    assert any(not f for f in apply_vhs_flags), "every profile applies VHS"
    assert any(f for f in apply_vhs_flags), "VHS profile removed entirely"


def test_style_override_replaces_global_config():
    """generate_image_ai uses style_override over cfg_get('image_style')."""
    captured: dict[str, str] = {}

    def fake_imagen(prompt: str, model: str, aspect_ratio: str | None = None) -> bytes:
        captured["prompt"] = prompt
        return b"\x89PNG\r\n\x1a\n"  # minimal valid header

    cfg_values = {
        "image_model": "imagen-4-generate-001",
        "image_style": "GLOBAL_STYLE_DO_NOT_USE",
    }

    def fake_cfg_get(key: str):
        return cfg_values.get(key, "")

    with patch.object(ig, "_generate_imagen", side_effect=fake_imagen), \
         patch.object(ig, "cfg_get", side_effect=fake_cfg_get):
        ig.generate_image_ai(
            "scene description", style_override="OVERRIDE_STYLE_USED"
        )

    assert "OVERRIDE_STYLE_USED" in captured["prompt"]
    assert "GLOBAL_STYLE_DO_NOT_USE" not in captured["prompt"]


def test_style_override_falls_back_to_global_when_none():
    captured: dict[str, str] = {}

    def fake_imagen(prompt: str, model: str, aspect_ratio: str | None = None) -> bytes:
        captured["prompt"] = prompt
        return b"\x89PNG\r\n\x1a\n"

    cfg_values = {
        "image_model": "imagen-4-generate-001",
        "image_style": "GLOBAL_FALLBACK_STYLE",
    }

    def fake_cfg_get(key: str):
        return cfg_values.get(key, "")

    with patch.object(ig, "_generate_imagen", side_effect=fake_imagen), \
         patch.object(ig, "cfg_get", side_effect=fake_cfg_get):
        ig.generate_image_ai("scene description")

    assert "GLOBAL_FALLBACK_STYLE" in captured["prompt"]
