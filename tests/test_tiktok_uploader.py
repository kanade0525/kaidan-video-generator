"""Tests for TikTok Inbox uploader chunking logic.

The OAuth/credential flow is out of scope here — these tests focus on the
multi-chunk upload behavior (Content-Range headers, chunk sizing, progress
callbacks). All HTTP calls are mocked.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services import tiktok_uploader


@pytest.fixture
def fake_token(tmp_path, monkeypatch):
    """Write a fake token file and point TIKTOK_TOKEN_PATH at it."""
    tok_path = tmp_path / "tiktok_token.json"
    tok_path.write_text(
        '{"access_token": "fake_at", "refresh_token": "fake_rt",'
        ' "expires_in": 86400, "_obtained_at": 9999999999}',
    )
    monkeypatch.setattr(tiktok_uploader, "TOKEN_PATH", tok_path)
    monkeypatch.setenv("TIKTOK_CLIENT_KEY", "fake_key")
    monkeypatch.setenv("TIKTOK_CLIENT_SECRET", "fake_secret")
    return tok_path


def _make_video(path: Path, size: int) -> Path:
    """Write a file of exactly `size` bytes (deterministic content)."""
    # Pattern that lets us verify byte slices: position % 256.
    path.write_bytes(bytes((i % 256) for i in range(size)))
    return path


def _ok_init_response():
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "data": {"publish_id": "pub_xyz", "upload_url": "https://upload.tiktok.test/u/1"},
    }
    return resp


def _ok_put_response(status_code=201):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = ""
    return resp


def _ok_status_response():
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"data": {"status": "PROCESSING_UPLOAD"}}
    return resp


def test_small_video_uses_single_chunk(tmp_path, fake_token):
    """Files ≤ 64MB go as a single chunk (chunk_size = video_size)."""
    video = _make_video(tmp_path / "v.mp4", 2 * 1024 * 1024)  # 2MB

    put_calls = []
    init_called = {"json": None}

    def fake_post(url, **kw):
        if "init" in url:
            init_called["json"] = kw.get("json")
            return _ok_init_response()
        return _ok_status_response()

    def fake_put(url, **kw):
        put_calls.append({"headers": kw.get("headers"), "data_len": len(kw.get("data", b""))})
        return _ok_put_response()

    with patch.object(tiktok_uploader.requests, "post", side_effect=fake_post), \
         patch.object(tiktok_uploader.requests, "put", side_effect=fake_put):
        result = tiktok_uploader.upload_video_to_inbox(video)

    assert result["publish_id"] == "pub_xyz"
    # init payload
    src = init_called["json"]["source_info"]
    assert src["video_size"] == 2 * 1024 * 1024
    assert src["chunk_size"] == 2 * 1024 * 1024
    assert src["total_chunk_count"] == 1
    # one PUT covering the whole file
    assert len(put_calls) == 1
    assert put_calls[0]["data_len"] == 2 * 1024 * 1024
    assert put_calls[0]["headers"]["Content-Range"] == f"bytes 0-{2 * 1024 * 1024 - 1}/{2 * 1024 * 1024}"


def test_64mb_boundary_uses_single_chunk(tmp_path, fake_token):
    """Exactly 64MB should still fit in a single chunk."""
    size = 64 * 1024 * 1024
    video = _make_video(tmp_path / "v.mp4", size)

    init_seen = {}
    put_calls = []

    def fake_post(url, **kw):
        if "init" in url:
            init_seen.update(kw.get("json", {}))
            return _ok_init_response()
        return _ok_status_response()

    def fake_put(url, **kw):
        put_calls.append(kw)
        return _ok_put_response()

    with patch.object(tiktok_uploader.requests, "post", side_effect=fake_post), \
         patch.object(tiktok_uploader.requests, "put", side_effect=fake_put):
        tiktok_uploader.upload_video_to_inbox(video)

    assert init_seen["source_info"]["total_chunk_count"] == 1
    assert init_seen["source_info"]["chunk_size"] == size
    assert len(put_calls) == 1


def test_large_video_uses_multi_chunk(tmp_path, fake_token):
    """82MB file should split into multiple ~10MB chunks with correct Content-Range headers."""
    size = 82_074_983  # the exact size from the user's error
    video = _make_video(tmp_path / "big.mp4", size)

    init_seen = {}
    put_calls = []

    def fake_post(url, **kw):
        if "init" in url:
            init_seen.update(kw.get("json", {}))
            return _ok_init_response()
        return _ok_status_response()

    def fake_put(url, **kw):
        put_calls.append({
            "url": url,
            "headers": kw.get("headers"),
            "data_len": len(kw.get("data", b"")),
        })
        # Intermediate chunks return 206, final returns 201.
        # We don't know which is final without state, so return 206 always —
        # the uploader accepts 200/201/206.
        return _ok_put_response(status_code=206)

    with patch.object(tiktok_uploader.requests, "post", side_effect=fake_post), \
         patch.object(tiktok_uploader.requests, "put", side_effect=fake_put):
        tiktok_uploader.upload_video_to_inbox(video)

    chunk_size = tiktok_uploader.MULTI_CHUNK_SIZE  # 10MB
    expected_chunks = size // chunk_size  # 7 for 82MB / 10MB
    assert expected_chunks == 7

    # init payload uses floor division
    src = init_seen["source_info"]
    assert src["video_size"] == size
    assert src["chunk_size"] == chunk_size
    assert src["total_chunk_count"] == expected_chunks

    # PUT calls: one per chunk, all to the same upload_url
    assert len(put_calls) == expected_chunks
    assert {c["url"] for c in put_calls} == {"https://upload.tiktok.test/u/1"}

    # Verify Content-Range headers form a contiguous cover of [0, size)
    total_bytes = 0
    last_end = -1
    for i, call in enumerate(put_calls):
        cr = call["headers"]["Content-Range"]
        # Format: "bytes {start}-{end}/{total}"
        prefix, total = cr.split("/")
        assert int(total) == size
        rng = prefix.replace("bytes ", "")
        start, end = (int(x) for x in rng.split("-"))
        # contiguous, non-overlapping
        assert start == last_end + 1
        last_end = end
        # payload length matches Content-Range length
        assert call["data_len"] == end - start + 1
        total_bytes += call["data_len"]
        # All intermediate chunks are exactly chunk_size; last absorbs remainder
        if i < expected_chunks - 1:
            assert end - start + 1 == chunk_size
        else:
            # last chunk = chunk_size + remainder
            assert end - start + 1 == chunk_size + (size % chunk_size)
            assert end == size - 1
    assert total_bytes == size


def test_multi_chunk_progress_callback(tmp_path, fake_token):
    """Progress callback should be invoked per chunk + init + status (total = chunks + 2)."""
    size = 30 * 1024 * 1024 + 5  # 30MB+5 → still single chunk
    video = _make_video(tmp_path / "v.mp4", size)
    progress_calls = []

    with patch.object(tiktok_uploader.requests, "post", side_effect=lambda url, **kw: (
        _ok_init_response() if "init" in url else _ok_status_response()
    )), patch.object(tiktok_uploader.requests, "put", return_value=_ok_put_response()):
        tiktok_uploader.upload_video_to_inbox(
            video,
            progress_callback=lambda cur, tot: progress_calls.append((cur, tot)),
        )

    # single chunk → total_steps = 1 + 1 + 1 = 3
    assert progress_calls[0] == (0, 3)
    assert progress_calls[-1] == (3, 3)
    # all totals match
    assert all(tot == 3 for _, tot in progress_calls)


def test_multi_chunk_progress_callback_scales(tmp_path, fake_token):
    """For a 7-chunk file, total_steps = 7 + 2 = 9."""
    size = 70 * 1024 * 1024  # 70MB → 7 chunks of 10MB each (exact)
    video = _make_video(tmp_path / "v.mp4", size)
    progress_calls = []

    with patch.object(tiktok_uploader.requests, "post", side_effect=lambda url, **kw: (
        _ok_init_response() if "init" in url else _ok_status_response()
    )), patch.object(tiktok_uploader.requests, "put", return_value=_ok_put_response(206)):
        tiktok_uploader.upload_video_to_inbox(
            video,
            progress_callback=lambda cur, tot: progress_calls.append((cur, tot)),
        )

    assert progress_calls[0] == (0, 9)
    assert progress_calls[-1] == (9, 9)


def test_chunk_failure_raises(tmp_path, fake_token):
    """If any chunk PUT fails with non-2xx/206, upload should raise."""
    size = 25 * 1024 * 1024  # single chunk
    video = _make_video(tmp_path / "v.mp4", size)

    bad_resp = MagicMock()
    bad_resp.status_code = 500
    bad_resp.text = "internal error"

    with patch.object(tiktok_uploader.requests, "post", side_effect=lambda url, **kw: (
        _ok_init_response() if "init" in url else _ok_status_response()
    )), patch.object(tiktok_uploader.requests, "put", return_value=bad_resp):
        with pytest.raises(RuntimeError, match="動画アップロード失敗"):
            tiktok_uploader.upload_video_to_inbox(video)


def test_empty_file_rejected(tmp_path, fake_token):
    video = tmp_path / "empty.mp4"
    video.write_bytes(b"")
    with pytest.raises(RuntimeError, match="空"):
        tiktok_uploader.upload_video_to_inbox(video)
