"""YouTube のサムネイル 2MB 制限に関するテスト。

ユーザー報告: サムネイルを1枚目の画像にしたい。実装は入っていたが
"Media larger than: 2097152" で失敗し続けており、ログには
「電話番号認証が必要な場合があります」と誤った原因が出ていた。
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
from PIL import Image

from app.services import youtube_uploader as yt


def _png(path: Path, width=1080, height=1920, noisy=True) -> Path:
    """圧縮の効かないノイズ画像で大きな PNG を作る。"""
    rng = np.random.default_rng(0)
    if noisy:
        arr = rng.integers(0, 255, size=(height, width, 3), dtype=np.uint8)
    else:
        arr = np.zeros((height, width, 3), dtype=np.uint8)
    Image.fromarray(arr).save(path, format="PNG")
    return path


class TestFitThumbnail:
    def test_small_file_passes_through(self, tmp_path):
        src = _png(tmp_path / "small.png", 200, 356, noisy=False)
        out, mime = yt.fit_thumbnail(src)
        assert out == src
        assert mime == "image/png"

    def test_large_file_is_compressed_under_limit(self, tmp_path):
        src = _png(tmp_path / "big.png")
        assert src.stat().st_size > yt.THUMBNAIL_MAX_BYTES
        out, mime = yt.fit_thumbnail(src)
        assert out != src
        assert out.stat().st_size <= yt.THUMBNAIL_MAX_BYTES
        assert mime == "image/jpeg"

    def test_compressed_file_is_still_an_image(self, tmp_path):
        src = _png(tmp_path / "big2.png")
        out, _ = yt.fit_thumbnail(src)
        img = Image.open(out)
        assert img.width > 0 and img.height > 0

    def test_original_is_not_modified(self, tmp_path):
        """動画に使う元画像を壊さない。"""
        src = _png(tmp_path / "big3.png")
        before = src.read_bytes()
        yt.fit_thumbnail(src)
        assert src.read_bytes() == before

    def test_jpeg_input_keeps_mimetype(self, tmp_path):
        p = tmp_path / "x.jpg"
        Image.new("RGB", (100, 100), (5, 5, 5)).save(p, format="JPEG")
        _, mime = yt.fit_thumbnail(p)
        assert mime == "image/jpeg"


class TestSetThumbnail:
    def _service(self):
        svc = MagicMock()
        svc.thumbnails.return_value.set.return_value.execute.return_value = {}
        return svc

    def test_sends_compressed_file(self, tmp_path):
        src = _png(tmp_path / "big.png")
        svc = self._service()
        with patch.object(yt, "_get_service", return_value=svc), \
             patch("googleapiclient.http.MediaFileUpload") as media:
            yt.set_thumbnail("vid1", src)
        sent = Path(media.call_args[0][0])
        assert sent.stat().st_size <= yt.THUMBNAIL_MAX_BYTES

    def test_missing_file_does_not_raise(self, tmp_path):
        yt.set_thumbnail("vid1", tmp_path / "nope.png")

    def test_api_failure_does_not_raise(self, tmp_path):
        """動画は既に上がっているので、ここで失敗させると重複投稿になる。"""
        src = _png(tmp_path / "s.png", 200, 356, noisy=False)
        svc = self._service()
        svc.thumbnails.return_value.set.return_value.execute.side_effect = RuntimeError("boom")
        with patch.object(yt, "_get_service", return_value=svc), \
             patch("googleapiclient.http.MediaFileUpload"):
            yt.set_thumbnail("vid1", src)

    def test_failure_is_logged_as_error(self, tmp_path, caplog):
        """warning ではなく error で、原因をそのまま残す。"""
        import logging

        src = _png(tmp_path / "s2.png", 200, 356, noisy=False)
        svc = self._service()
        svc.thumbnails.return_value.set.return_value.execute.side_effect = RuntimeError(
            "Media larger than: 2097152",
        )
        with caplog.at_level(logging.ERROR), \
             patch.object(yt, "_get_service", return_value=svc), \
             patch("googleapiclient.http.MediaFileUpload"):
            yt.set_thumbnail("vid1", src)
        assert "Media larger than" in caplog.text
        assert "電話番号認証" not in caplog.text
