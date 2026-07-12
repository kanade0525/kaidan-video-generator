"""Tests for save_processed_text (音声タブ/テキストタブ共通の保存処理)。

編集した処理後テキストを保存し、音声用 chunks.json と字幕用
original_chunks.json を同数で再生成する（do_text と同じ挙動）。
"""

import json

import pytest

from app.models import Story
from app.ui.pages import results


@pytest.fixture
def story_files(tmp_path, monkeypatch):
    proc = tmp_path / "processed_text.txt"
    chunks = tmp_path / "chunks.json"
    orig = tmp_path / "original_chunks.json"
    raw = tmp_path / "raw_content.txt"
    raw.write_text(
        "鏡に自分の姿を映すものじゃないよ。良くない事が起こるからね。", encoding="utf-8",
    )
    monkeypatch.setattr(results, "processed_text_path", lambda t, ct: proc)
    monkeypatch.setattr(results, "chunks_path", lambda t, ct: chunks)
    monkeypatch.setattr(results, "original_chunks_path", lambda t, ct: orig)
    monkeypatch.setattr(results, "raw_content_path", lambda t, ct: raw)
    return proc, chunks, orig


def test_writes_text_and_regenerates_both_chunk_files(story_files):
    proc, chunks, orig = story_files
    story = Story(id=1, title="t", content_type="long")
    new_text = "かがみにじぶんのすがたをうつすものじゃないよ。よくないことがおこるからね。"

    n = results.save_processed_text(story, new_text)

    assert proc.read_text(encoding="utf-8") == new_text
    ch = json.loads(chunks.read_text(encoding="utf-8"))
    oc = json.loads(orig.read_text(encoding="utf-8"))
    assert n >= 1
    assert len(ch) == n
    # 字幕(原文)チャンクは音声チャンクと同数(1:1対応)でなければ字幕がズレる
    assert len(oc) == n


def test_skips_original_chunks_when_no_raw(tmp_path, monkeypatch):
    proc = tmp_path / "p.txt"
    chunks = tmp_path / "c.json"
    orig = tmp_path / "o.json"
    monkeypatch.setattr(results, "processed_text_path", lambda t, ct: proc)
    monkeypatch.setattr(results, "chunks_path", lambda t, ct: chunks)
    monkeypatch.setattr(results, "original_chunks_path", lambda t, ct: orig)
    monkeypatch.setattr(results, "raw_content_path", lambda t, ct: tmp_path / "missing.txt")
    story = Story(id=1, title="t", content_type="long")

    n = results.save_processed_text(story, "あいうえお。かきくけこ。")

    assert proc.exists() and chunks.exists()
    assert n >= 1
    assert not orig.exists()  # 原文が無ければ original_chunks は作らない
