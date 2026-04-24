"""Tests for create_title_clip fade filter handling.

Regression: when fade_in=0, the original implementation emitted
`fade=in:st=0:d=0` into filter_complex, which ffmpeg renders as a black
first frame. This test locks in that fade=0 skips the filter entirely.
"""

from unittest.mock import patch

from app.utils import ffmpeg as ff


def _captured_args(tmp_path, **kwargs):
    """Call create_title_clip with run_ffmpeg mocked; return the args list."""
    captured = {}

    def fake_run(args):
        captured["args"] = args

    image = tmp_path / "title.png"
    audio = tmp_path / "narration.wav"
    output = tmp_path / "out.mp4"
    image.write_bytes(b"x")
    audio.write_bytes(b"x")

    with patch.object(ff, "run_ffmpeg", side_effect=fake_run), \
         patch.object(ff, "get_audio_duration", return_value=3.0):
        ff.create_title_clip(image, audio, output, **kwargs)

    return captured["args"]


def _filter_complex(args):
    idx = args.index("-filter_complex")
    return args[idx + 1]


def test_fade_in_zero_skips_fade_in_filter(tmp_path):
    """With fade_in=0, filter_complex must not contain `fade=in`."""
    args = _captured_args(tmp_path, fade_in=0, fade_out=0.5,
                          silence_before=0, silence_after=0)
    fc = _filter_complex(args)
    assert "fade=in" not in fc, f"fade=in leaked into filter: {fc}"
    assert "fade=out" in fc


def test_fade_out_zero_skips_fade_out_filter(tmp_path):
    """With fade_out=0, filter_complex must not contain `fade=out`."""
    args = _captured_args(tmp_path, fade_in=0.5, fade_out=0,
                          silence_before=0, silence_after=0)
    fc = _filter_complex(args)
    assert "fade=out" not in fc, f"fade=out leaked into filter: {fc}"
    assert "fade=in" in fc


def test_both_fades_zero_uses_null_video_filter(tmp_path):
    """With both fades=0, the video filter must be a passthrough (`null`)."""
    args = _captured_args(tmp_path, fade_in=0, fade_out=0,
                          silence_before=0, silence_after=0)
    fc = _filter_complex(args)
    assert "fade=in" not in fc
    assert "fade=out" not in fc
    assert "[0:v]null[v]" in fc


def test_nonzero_fades_include_both_filters(tmp_path):
    """With both fades > 0, both filter segments are present."""
    args = _captured_args(tmp_path, fade_in=0.5, fade_out=0.5,
                          silence_before=1.0, silence_after=1.0)
    fc = _filter_complex(args)
    assert "fade=in:st=0:d=0.5" in fc
    assert "fade=out:st=" in fc
