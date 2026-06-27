"""Tests for create_slideshow / add_fade encode timeout + speed handling.

Background: long single-scene bundle segments (10〜15 min narration) timed out
because run_ffmpeg used a fixed 600s timeout regardless of length, and the
per-frame scale/crop on a huge PNG made the encode painfully slow. The fix:
  - scale the timeout to the clip duration,
  - pre-scale the image once instead of every frame,
  - use a fast still-image preset.
add_fade had the same fixed-timeout problem on the full-length re-encode.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.utils import ffmpeg


def _calls_for_single_image(tmp_path, duration, *, with_scale=True):
    image = tmp_path / "scene_000.png"
    audio = tmp_path / "narration.wav"
    out = tmp_path / "slideshow_temp.mp4"
    image.write_bytes(b"fake-png")
    audio.write_bytes(b"fake-wav")

    completed = MagicMock(returncode=0, stderr="")
    kw = dict(target_width=1920, target_height=1080) if with_scale else {}
    with patch.object(ffmpeg, "get_audio_duration", return_value=duration), \
         patch.object(ffmpeg.subprocess, "run", return_value=completed) as mrun:
        ffmpeg.create_slideshow([image], audio, out, **kw)
    return mrun.call_args_list


def _encode_call(calls):
    """The looping encode call (as opposed to the one-shot prescale)."""
    for c in calls:
        if "-loop" in list(c.args[0]):
            return c
    raise AssertionError("encode (-loop) call not found")


def test_short_clip_keeps_minimum_timeout(tmp_path):
    call = _encode_call(_calls_for_single_image(tmp_path, duration=30.0))
    assert call.kwargs["timeout"] == 600


def test_long_clip_scales_timeout(tmp_path):
    """756s narration (the original failing case) → large timeout."""
    call = _encode_call(_calls_for_single_image(tmp_path, duration=756.0))
    assert call.kwargs["timeout"] == 2568  # max(600, 756*3 + 300)
    assert call.kwargs["timeout"] > 756.0


def test_single_image_uses_stillimage_tune(tmp_path):
    cmd = list(_encode_call(_calls_for_single_image(tmp_path, duration=120.0)).args[0])
    assert "-tune" in cmd and cmd[cmd.index("-tune") + 1] == "stillimage"
    assert "-preset" in cmd and cmd[cmd.index("-preset") + 1] == "veryfast"


def test_image_is_prescaled_once(tmp_path):
    """When scaling, a one-shot prescale (-frames:v 1) runs before the encode,
    and the encode itself no longer carries the per-frame scale filter."""
    calls = _calls_for_single_image(tmp_path, duration=120.0, with_scale=True)
    prescale = calls[0].args[0]
    assert "-frames:v" in prescale and prescale[prescale.index("-frames:v") + 1] == "1"
    assert "-vf" in prescale  # the scale/crop happens here, once
    enc = list(_encode_call(calls).args[0])
    assert "-vf" not in enc  # not re-applied every frame


def test_no_prescale_when_no_target_resolution(tmp_path):
    """Without a target resolution there's no scaling to hoist out."""
    calls = _calls_for_single_image(tmp_path, duration=120.0, with_scale=False)
    assert len(calls) == 1  # just the encode, no prescale step


def _run_add_fade(tmp_path, duration):
    inp = tmp_path / "slideshow_temp.mp4"
    out = tmp_path / "faded_temp.mp4"
    inp.write_bytes(b"fake-mp4")
    completed = MagicMock(returncode=0, stderr="")
    with patch.object(ffmpeg, "get_audio_duration", return_value=duration), \
         patch.object(ffmpeg.subprocess, "run", return_value=completed) as mrun:
        ffmpeg.add_fade(inp, out, fade_in=1.0, fade_out=0)
    return mrun.call_args_list[0]


def test_add_fade_scales_timeout_and_preset(tmp_path):
    """The full-length fade re-encode must not use the fixed 600s timeout."""
    call = _run_add_fade(tmp_path, duration=877.0)
    assert call.kwargs["timeout"] == max(1800, int(877.0 * 6))
    cmd = list(call.args[0])
    assert "-preset" in cmd and cmd[cmd.index("-preset") + 1] == "veryfast"
