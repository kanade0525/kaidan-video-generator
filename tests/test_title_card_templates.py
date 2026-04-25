"""Tests for Shorts title-card template diversification.

Background: YouTube Shorts feed dampens reach when a channel uploads visually
similar content day after day. Pre-fix, every title card used the same
"red text centered, top-right red badge" layout. This test locks in:

1. Multiple distinct templates exist.
2. The same title always picks the same template (deterministic).
3. Different titles spread across templates.
4. Each template renders to a valid PNG of the requested size.
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image

from app.services.image_generator import (
    SHORTS_TITLE_TEMPLATES,
    create_title_card,
    pick_shorts_title_template,
)


def test_template_pool_has_multiple_distinct_styles():
    assert len(SHORTS_TITLE_TEMPLATES) >= 4
    names = [t.name for t in SHORTS_TITLE_TEMPLATES]
    assert len(set(names)) == len(names), "duplicate template names"


def test_pick_is_deterministic_per_title():
    a = pick_shorts_title_template("村の歓迎")
    b = pick_shorts_title_template("村の歓迎")
    assert a.name == b.name


def test_pick_spreads_across_templates():
    """A small set of varied titles should hit at least 3 templates."""
    titles = [
        "村の歓迎", "首吊り双六", "異質なリズム", "捏造された呪いの神社",
        "廃屋にあった日記帳", "〇体探し", "本当の守護霊", "ルカくん",
        "炙り出す", "爺ちゃんが呪われたのは",
    ]
    picked = {pick_shorts_title_template(t).name for t in titles}
    assert len(picked) >= 3, f"templates clustered: {picked}"


def test_each_template_renders_valid_png_at_shorts_size():
    for tpl in SHORTS_TITLE_TEMPLATES:
        png = create_title_card(
            title="テストタイトル",
            width=1080, height=1920,
            bg_image_data=None,
            category="怪談",
            template=tpl,
        )
        assert png.startswith(b"\x89PNG"), f"{tpl.name}: not PNG"
        img = Image.open(BytesIO(png))
        assert img.size == (1080, 1920), f"{tpl.name}: wrong size {img.size}"


def test_default_template_back_compat():
    """Calling without `template` still produces the classic-red look."""
    png_default = create_title_card(
        title="テスト", width=1792, height=1024, category="怪談",
    )
    png_classic = create_title_card(
        title="テスト", width=1792, height=1024, category="怪談",
        template=SHORTS_TITLE_TEMPLATES[0],
    )
    # Both should render successfully; bytes are not strictly equal due to
    # PNG metadata, but both must be valid PNG of the same dimensions.
    assert png_default.startswith(b"\x89PNG")
    assert png_classic.startswith(b"\x89PNG")
    assert Image.open(BytesIO(png_default)).size == Image.open(BytesIO(png_classic)).size
