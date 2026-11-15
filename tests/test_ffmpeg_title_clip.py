"""Tests for create_title_clip ffmpeg invocation.

Specifically verifies that the filter chain force-evens the input image
dimensions so that libx264 + yuv420p does not fail on odd-sized source
images (which produced exit -22 / "Could not open encoder before EOF").
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.utils import ffmpeg


@pytest.fixture
def fake_audio_duration():
    """Patch ffprobe duration so create_title_clip doesn't try to read real files."""
    with patch.object(ffmpeg, "get_audio_duration", return_value=5.0):
        yield


def _captured_cmd(calls) -> list[str]:
    """Extract the ffmpeg argv from subprocess.run calls."""
    assert calls, "subprocess.run was not called"
    args, _kw = calls[0]
    return list(args[0])


def test_title_clip_force_evens_dimensions(tmp_path, fake_audio_duration):
    """Filter chain must include scale=trunc(iw/2)*2:trunc(ih/2)*2 so odd-sized
    title cards (e.g., 941x1672) don't break libx264 yuv420p."""
    image = tmp_path / "title.png"
    audio = tmp_path / "title.wav"
    out = tmp_path / "title_clip.mp4"
    image.write_bytes(b"fake-png")
    audio.write_bytes(b"fake-wav")

    completed = MagicMock(returncode=0, stderr="")
    with patch.object(ffmpeg.subprocess, "run", return_value=completed) as mrun:
        ffmpeg.create_title_clip(image, audio, out)

    cmd = _captured_cmd(mrun.call_args_list)
    # Find the filter_complex value
    assert "-filter_complex" in cmd
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "scale=trunc(iw/2)*2:trunc(ih/2)*2" in fc, (
        f"force-even scale filter missing from filter_complex: {fc}"
    )
    # Sanity: scale must precede fade (otherwise it doesn't help)
    assert fc.index("scale=trunc") < fc.index("fade=in"), (
        "scale must come before fade in the filter chain"
    )


def test_title_clip_still_uses_yuv420p(tmp_path, fake_audio_duration):
    """yuv420p is the chroma format that requires even dims — ensure we keep it."""
    image = tmp_path / "title.png"
    audio = tmp_path / "title.wav"
    out = tmp_path / "title_clip.mp4"
    image.write_bytes(b"fake-png")
    audio.write_bytes(b"fake-wav")

    completed = MagicMock(returncode=0, stderr="")
    with patch.object(ffmpeg.subprocess, "run", return_value=completed) as mrun:
        ffmpeg.create_title_clip(image, audio, out)

    cmd = _captured_cmd(mrun.call_args_list)
    pix_fmt_idx = cmd.index("-pix_fmt")
    assert cmd[pix_fmt_idx + 1] == "yuv420p"


def test_title_clip_failure_propagates(tmp_path, fake_audio_duration):
    """If ffmpeg returns non-zero, run_ffmpeg raises RuntimeError."""
    image = tmp_path / "title.png"
    audio = tmp_path / "title.wav"
    out = tmp_path / "title_clip.mp4"
    image.write_bytes(b"fake-png")
    audio.write_bytes(b"fake-wav")

    bad = MagicMock(returncode=-22, stderr="Could not open encoder before EOF")
    with patch.object(ffmpeg.subprocess, "run", return_value=bad):
        with pytest.raises(RuntimeError, match="ffmpeg failed"):
            ffmpeg.create_title_clip(image, audio, out)


class TestFadeInDisabled:
    """fade_in=0 のときフェードを完全に無くす。

    ffmpeg の fade フィルタは d=0 を「フェード無し」ではなく
    「既定の nb_frames(25) を使う」と解釈する。0 を渡しただけでは
    30fps で約0.8秒のフェードが残り、Shorts の冒頭が暗く始まってしまう。
    """

    def _filter_arg(self, calls):
        args = calls[0]
        return args[args.index("-filter_complex") + 1]

    def test_fade_in_filter_omitted_when_zero(self, tmp_path, monkeypatch):
        from app.utils import ffmpeg

        calls = []
        monkeypatch.setattr(ffmpeg, "run_ffmpeg", lambda a: calls.append(a))
        monkeypatch.setattr(ffmpeg, "get_audio_duration", lambda p: 2.0)

        ffmpeg.create_title_clip(
            tmp_path / "i.png", tmp_path / "a.wav", tmp_path / "o.mp4",
            fade_in=0, fade_out=0.5,
        )
        assert "fade=in" not in self._filter_arg(calls)

    def test_fade_out_is_untouched(self, tmp_path, monkeypatch):
        """fade_out は依頼範囲外なので従来どおり指定する。"""
        from app.utils import ffmpeg

        calls = []
        monkeypatch.setattr(ffmpeg, "run_ffmpeg", lambda a: calls.append(a))
        monkeypatch.setattr(ffmpeg, "get_audio_duration", lambda p: 2.0)

        ffmpeg.create_title_clip(
            tmp_path / "i.png", tmp_path / "a.wav", tmp_path / "o.mp4",
            fade_in=0, fade_out=0.5,
        )
        assert "fade=out:st=3.50:d=0.5" in self._filter_arg(calls)

    def test_fade_in_applied_when_positive(self, tmp_path, monkeypatch):
        from app.utils import ffmpeg

        calls = []
        monkeypatch.setattr(ffmpeg, "run_ffmpeg", lambda a: calls.append(a))
        monkeypatch.setattr(ffmpeg, "get_audio_duration", lambda p: 2.0)

        ffmpeg.create_title_clip(
            tmp_path / "i.png", tmp_path / "a.wav", tmp_path / "o.mp4",
            fade_in=0.5, fade_out=0.5,
        )
        assert "fade=in:st=0:d=0.5" in self._filter_arg(calls)

    def test_scale_filter_kept(self, tmp_path, monkeypatch):
        """偶数化の scale は libx264 に必須なので常に残す。"""
        from app.utils import ffmpeg

        calls = []
        monkeypatch.setattr(ffmpeg, "run_ffmpeg", lambda a: calls.append(a))
        monkeypatch.setattr(ffmpeg, "get_audio_duration", lambda p: 2.0)

        ffmpeg.create_title_clip(
            tmp_path / "i.png", tmp_path / "a.wav", tmp_path / "o.mp4", fade_in=0,
        )
        assert "scale=trunc(iw/2)*2:trunc(ih/2)*2" in self._filter_arg(calls)
