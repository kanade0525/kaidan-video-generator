"""Tests for Shorts visual style profile diversification.

Brand rule (locked): every Short renders in the channel's VHS aesthetic.
What changes per Short is the *scene content* (corridor / outdoor / object /
distant figure / etc.), not the rendering style. Like Ghibli: many stories,
one look.

This test locks in:
1. The pool has multiple profiles with distinct names and aesthetic_focus.
2. Every profile applies VHS post-processing (`apply_vhs=True`).
3. Every profile shares the same `style_suffix` (the brand's VHS suffix).
4. `pick_shorts_visual_style` is deterministic per title.
5. Different titles spread across profiles' aesthetic_focus.
6. `generate_image_ai` honors `style_override` instead of the global config.
"""

from __future__ import annotations

from unittest.mock import patch

from app.services import image_generator as ig
from app.services.image_generator import (
    SHORTS_VISUAL_STYLES,
    pick_shorts_visual_style,
)


def test_profile_pool_has_multiple_distinct_focuses():
    assert len(SHORTS_VISUAL_STYLES) >= 4
    names = [p.name for p in SHORTS_VISUAL_STYLES]
    assert len(set(names)) == len(names), "duplicate profile names"
    # The diversification axis is aesthetic_focus, not style_suffix.
    focuses = {p.aesthetic_focus for p in SHORTS_VISUAL_STYLES}
    assert len(focuses) == len(SHORTS_VISUAL_STYLES), "duplicate aesthetic_focus"


def test_every_profile_keeps_vhs_brand():
    """Brand rule: VHS post-fx is mandatory on every Short."""
    for p in SHORTS_VISUAL_STYLES:
        assert p.apply_vhs is True, f"{p.name} broke VHS brand rule"


def test_every_profile_uses_same_style_suffix():
    """Brand rule: rendering style (suffix sent to image AI) is unified."""
    suffixes = {p.style_suffix for p in SHORTS_VISUAL_STYLES}
    assert len(suffixes) == 1, f"style_suffix diverged: {suffixes}"


def test_pick_is_deterministic_per_title():
    a = pick_shorts_visual_style("村の歓迎")
    b = pick_shorts_visual_style("村の歓迎")
    assert a.name == b.name


def test_pick_spreads_across_profiles():
    """A small set of varied titles should hit at least 3 distinct profiles."""
    titles = [
        "村の歓迎", "首吊り双六", "異質なリズム", "捏造された呪いの神社",
        "廃屋にあった日記帳", "〇体探し", "本当の守護霊", "ルカくん",
        "炙り出す", "爺ちゃんが呪われたのは", "深夜の電話", "鏡の中の女",
    ]
    picked = {pick_shorts_visual_style(t).name for t in titles}
    assert len(picked) >= 3, f"profiles clustered: {picked}"


def test_style_override_replaces_global_config():
    """generate_image_ai uses style_override over cfg_get('image_style')."""
    captured: dict[str, str] = {}

    def fake_imagen(prompt: str, model: str, aspect_ratio: str | None = None) -> bytes:
        captured["prompt"] = prompt
        return b"\x89PNG\r\n\x1a\n"

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
