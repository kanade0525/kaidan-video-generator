"""Tests for concat_videos stream-copy fast path.

Background: concat_videos re-encoded every part via _normalize_video (full
libx264 pass). For bundle segments that are already h264/yuv420p at the target
resolution, that meant re-encoding a 15-minute video just to concatenate it —
done both per-segment (title+main) and again for the final bundle assembly.
The fix stream-copies the video of matching parts and only re-encodes audio.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.utils import ffmpeg


# ── _video_matches_concat_target ────────────────────────────────────────────

def _patch_probe(params):
    return patch.object(ffmpeg, "_probe_video_params", return_value=params)


def test_matches_when_all_params_align(tmp_path):
    p = tmp_path / "seg.mp4"
    with _patch_probe({"codec": "h264", "width": 1920, "height": 1080,
                       "pix_fmt": "yuv420p", "fps": 30.0}):
        assert ffmpeg._video_matches_concat_target(p, 1920, 1080, 30) is True


def test_no_match_on_resolution(tmp_path):
    p = tmp_path / "seg.mp4"
    with _patch_probe({"codec": "h264", "width": 1280, "height": 720,
                       "pix_fmt": "yuv420p", "fps": 30.0}):
        assert ffmpeg._video_matches_concat_target(p, 1920, 1080, 30) is False


def test_no_match_on_codec_or_pixfmt(tmp_path):
    p = tmp_path / "seg.mp4"
    with _patch_probe({"codec": "hevc", "width": 1920, "height": 1080,
                       "pix_fmt": "yuv420p", "fps": 30.0}):
        assert ffmpeg._video_matches_concat_target(p, 1920, 1080, 30) is False
    with _patch_probe({"codec": "h264", "width": 1920, "height": 1080,
                       "pix_fmt": "yuv444p", "fps": 30.0}):
        assert ffmpeg._video_matches_concat_target(p, 1920, 1080, 30) is False


def test_no_match_when_probe_fails(tmp_path):
    with _patch_probe({}):
        assert ffmpeg._video_matches_concat_target(tmp_path / "x.mp4", 1920, 1080, 30) is False


def test_fps_tolerance(tmp_path):
    """29.97 should still count as 30."""
    p = tmp_path / "seg.mp4"
    with _patch_probe({"codec": "h264", "width": 1920, "height": 1080,
                       "pix_fmt": "yuv420p", "fps": 29.97}):
        assert ffmpeg._video_matches_concat_target(p, 1920, 1080, 30) is True


# ── concat_videos routing ───────────────────────────────────────────────────

def test_concat_copies_matching_and_reencodes_mismatch(tmp_path):
    """Matching parts → stream-copy (-c:v copy); mismatching → _normalize_video."""
    seg = tmp_path / "000.mp4"      # matches target
    op = tmp_path / "op.mp4"        # does NOT match
    out = tmp_path / "bundle.mp4"
    for f in (seg, op):
        f.write_bytes(b"fake")

    completed = MagicMock(returncode=0, stderr="")

    def fake_match(path, w, h, fps):
        return path == seg  # only the segment matches

    with patch.object(ffmpeg, "_video_matches_concat_target", side_effect=fake_match), \
         patch.object(ffmpeg, "_normalize_video") as mnorm, \
         patch.object(ffmpeg, "get_audio_duration", return_value=900.0), \
         patch.object(ffmpeg.subprocess, "run", return_value=completed) as mrun:
        ffmpeg.concat_videos([op, seg], out, width=1920, height=1080, fps=30)

    # The mismatching OP went through _normalize_video (full re-encode)
    assert mnorm.call_count == 1
    assert mnorm.call_args.args[0] == op

    # The matching segment was stream-copied: find a copy invocation
    copy_calls = [
        c for c in mrun.call_args_list
        if "-c:v" in list(c.args[0]) and "copy" in list(c.args[0])
    ]
    assert copy_calls, "matching segment should be stream-copied (-c:v copy)"
    copy_cmd = list(copy_calls[0].args[0])
    # Audio is still re-encoded uniformly for safe concat
    assert "aac" in copy_cmd and "44100" in copy_cmd
    assert "libx264" not in copy_cmd  # no video re-encode
