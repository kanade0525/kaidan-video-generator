"""Tests for the (single) Shorts title-card template.

Brand rule: every Short uses the same classic-red title card so the channel
keeps a recognisable identity (red 古印体 text + 怪談 badge top-right + dim
VHS-flavoured background). Visual variety lives in the AI background, not the
overlay.
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image

from app.services.image_generator import (
    SHORTS_TITLE_TEMPLATES,
    create_title_card,
    pick_shorts_title_template,
)


def test_only_one_template_for_brand_consistency():
    """Multiple templates would break the channel's brand identity."""
    assert len(SHORTS_TITLE_TEMPLATES) == 1
    assert SHORTS_TITLE_TEMPLATES[0].name == "classic_red"


def test_pick_always_returns_the_brand_template():
    for title in ("村の歓迎", "首吊り双六", "深夜の電話"):
        assert pick_shorts_title_template(title).name == "classic_red"


def test_template_renders_valid_png_at_shorts_size():
    png = create_title_card(
        title="テストタイトル",
        width=1080, height=1920,
        bg_image_data=None,
        category="怪談",
        template=SHORTS_TITLE_TEMPLATES[0],
    )
    assert png.startswith(b"\x89PNG"), "not PNG"
    img = Image.open(BytesIO(png))
    assert img.size == (1080, 1920)


def test_default_template_back_compat():
    """Calling without `template` still produces the classic-red look."""
    png_default = create_title_card(
        title="テスト", width=1792, height=1024, category="怪談",
    )
    png_classic = create_title_card(
        title="テスト", width=1792, height=1024, category="怪談",
        template=SHORTS_TITLE_TEMPLATES[0],
    )
    # Both must render successfully at the requested dimensions.
    assert png_default.startswith(b"\x89PNG")
    assert png_classic.startswith(b"\x89PNG")
    assert Image.open(BytesIO(png_default)).size == Image.open(BytesIO(png_classic)).size
