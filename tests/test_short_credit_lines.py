"""Tests for the Shorts credit-line builder.

The HHS図書館 source does not provide author info — when the source is "hhs"
the overlay must show 「ホラホリ図書館」 (not 奇々怪々) and must NOT show a
trailing empty 「作者: 」 line.
"""

from __future__ import annotations

from app.models import Story
from app.pipeline.stages import build_short_credit_lines


def _story(source: str, title: str = "テスト怪談", author: str = "") -> Story:
    s = Story(id=1, title=title, url="http://example.test/x", source=source, author=author)
    return s


def test_hhs_uses_horahori_and_omits_author():
    """HHS source → ホラホリ図書館, no author line (even if author is empty)."""
    lines = build_short_credit_lines(_story("hhs"), author="")
    assert lines == ["ホラホリ図書館", "「テスト怪談」"]


def test_hhs_omits_author_even_when_author_present():
    """If somehow an HHS story has an author, we still don't display it
    (HHS規約: ホラホリ図書館 表記が出典)."""
    lines = build_short_credit_lines(_story("hhs", author="名前あり"), author="名前あり")
    assert "作者" not in "".join(lines)
    assert "ホラホリ図書館" in lines


def test_kikikaikai_uses_kikikaikai_with_author():
    """Non-HHS source → 奇々怪々 + author line as before."""
    lines = build_short_credit_lines(
        _story("kikikaikai", title="幽霊の話", author="山田太郎"),
        author="山田太郎",
    )
    assert lines == ["奇々怪々", "「幽霊の話」", "作者: 山田太郎"]


def test_kikikaikai_with_empty_author_still_shows_label():
    """For kikikaikai we keep the historical 3-line shape even when author
    is empty — the bug being fixed is only the HHS case."""
    lines = build_short_credit_lines(_story("kikikaikai"), author="")
    assert lines == ["奇々怪々", "「テスト怪談」", "作者: "]
