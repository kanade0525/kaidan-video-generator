"""Tests for _collect_bundled_story_ids.

過去の詰め合わせ manifest を走査して story_id → 含めた詰め合わせ名リストを
返すヘルパー。選択UIで「使用済み」マーカーを出すために使う。
"""

from __future__ import annotations

import json

from app.ui.pages.bundle import _collect_bundled_story_ids


def _write_manifest(root, name, story_ids):
    bdir = root / name
    bdir.mkdir(parents=True)
    (bdir / "manifest.json").write_text(
        json.dumps(
            {
                "name": name,
                "stories": [{"id": sid, "title": f"t{sid}"} for sid in story_ids],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return bdir


def test_empty_when_no_bundles_dir(tmp_path):
    assert _collect_bundled_story_ids(tmp_path / "missing") == {}


def test_collects_story_ids_across_bundles(tmp_path):
    _write_manifest(tmp_path, "詰め合わせA", [1, 2, 3])
    _write_manifest(tmp_path, "詰め合わせB", [3, 4])

    result = _collect_bundled_story_ids(tmp_path)

    assert result[1] == ["詰め合わせA"]
    assert result[4] == ["詰め合わせB"]
    # 複数の詰め合わせに含まれる story はすべての詰め合わせ名を持つ
    assert sorted(result[3]) == ["詰め合わせA", "詰め合わせB"]


def test_ignores_dirs_without_manifest_and_bad_json(tmp_path):
    (tmp_path / "no_manifest").mkdir()
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "manifest.json").write_text("{ not json", encoding="utf-8")
    _write_manifest(tmp_path, "ok", [10])

    result = _collect_bundled_story_ids(tmp_path)

    assert result == {10: ["ok"]}


def test_skips_story_entries_without_id(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "manifest.json").write_text(
        json.dumps({"name": "b", "stories": [{"title": "x"}, {"id": 5, "title": "y"}]}),
        encoding="utf-8",
    )

    result = _collect_bundled_story_ids(tmp_path)

    assert result == {5: ["b"]}
