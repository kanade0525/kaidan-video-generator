"""Tests for build_inline_diff (character-level diff for校正プレビュー表示)."""

from app.services.text_processor import build_inline_diff


def _reconstruct_before(segs):
    return "".join(t for op, t in segs if op != "insert")


def _reconstruct_after(segs):
    return "".join(t for op, t in segs if op != "delete")


class TestBuildInlineDiff:
    def test_no_change_is_all_equal(self):
        segs = build_inline_diff("かたかな", "かたかな")
        assert segs == [("equal", "かたかな")]

    def test_pure_replacement(self):
        # かめい → かな (校正で誤読を直したケース)
        segs = build_inline_diff("かたかめい", "かたかな")
        # 変更部分は delete + insert で表現され、ラウンドトリップが保たれる
        ops = [op for op, _ in segs]
        assert "delete" in ops and "insert" in ops
        assert _reconstruct_before(segs) == "かたかめい"
        assert _reconstruct_after(segs) == "かたかな"

    def test_insertion(self):
        segs = build_inline_diff("abc", "axbc")
        assert _reconstruct_before(segs) == "abc"
        assert _reconstruct_after(segs) == "axbc"
        assert any(op == "insert" and "x" in t for op, t in segs)

    def test_reconstruct_roundtrip(self):
        before = "きんひたいわいちまんえん。"
        after = "きんがくわいちまんえん。"
        segs = build_inline_diff(before, after)
        assert _reconstruct_before(segs) == before
        assert _reconstruct_after(segs) == after

    def test_empty_to_text(self):
        segs = build_inline_diff("", "あたらしい")
        assert segs == [("insert", "あたらしい")]

    def test_text_to_empty(self):
        segs = build_inline_diff("けされる", "")
        assert segs == [("delete", "けされる")]

    def test_multiline_roundtrip(self):
        before = "いちぎょうめ\nにぎょうめ\nさんぎょうめ"
        after = "いちぎょうめ\nにぎょうめだよ\nさんぎょうめ"
        segs = build_inline_diff(before, after)
        assert _reconstruct_before(segs) == before
        assert _reconstruct_after(segs) == after
        # 追加された「だよ」が insert として現れる
        assert any(op == "insert" and "だよ" in t for op, t in segs)
