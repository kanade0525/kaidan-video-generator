"""Tests for category color mapping."""

from app.ui.category_colors import _PALETTE, _PINNED_COLORS, category_color


class TestCategoryColor:
    def test_kaidan_is_pinned_red(self):
        assert category_color("怪談") == "red-7"
        assert _PINNED_COLORS["怪談"].startswith("red")

    def test_same_input_same_output(self):
        assert category_color("短編") == category_color("短編")
        assert category_color("shinrei") == category_color("shinrei")

    def test_non_pinned_returns_palette_color(self):
        color = category_color("短編")
        assert color in _PALETTE

    def test_empty_category_returns_valid_color(self):
        color = category_color("")
        assert color in _PALETTE

    def test_different_categories_likely_different_colors(self):
        # Not guaranteed but palette has 12 entries so small sample should differ.
        samples = ["短編", "長編", "shinrei", "obon", "都市伝説"]
        colors = {category_color(s) for s in samples}
        assert len(colors) >= 3
