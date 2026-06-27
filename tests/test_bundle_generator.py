"""Tests for the bundle (詰め合わせ動画) generator orchestration.

The generator stitches multiple long-story segments together with OP/jingles/ED.
Heavy ffmpeg work is mocked — these tests verify the orchestration logic
(file resolution, ordering, fallback when assets missing).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.models import Story


@pytest.fixture
def fake_story_factory(tmp_path):
    """Create fake long-story output dirs with the required intermediate files."""
    output_root = tmp_path / "output"
    output_root.mkdir()

    def make(title: str, story_id: int = 1) -> Story:
        sdir = output_root / title
        sdir.mkdir()
        (sdir / "images").mkdir()
        # Title card + a couple of scenes
        (sdir / "images" / "000_title_card.png").write_bytes(b"fake-png")
        (sdir / "images" / "scene_001.png").write_bytes(b"fake-png")
        (sdir / "images" / "scene_002.png").write_bytes(b"fake-png")
        # Narrations
        (sdir / "narration_complete.wav").write_bytes(b"fake-wav")
        (sdir / "title_narration.wav").write_bytes(b"fake-wav")
        # Original chunks (for scroll subtitle)
        (sdir / "original_chunks.json").write_text(
            json.dumps(["これは怪談の本文です。"]),
            encoding="utf-8",
        )
        return Story(
            id=story_id,
            title=title,
            url=f"https://example.test/{title}",
            stage="video_complete",
            content_type="long",
        )

    return make, output_root


def _patch_paths(monkeypatch, output_root: Path):
    """Make app.utils.paths point at the test output_root."""
    import app.utils.paths as paths_mod

    monkeypatch.setattr(paths_mod, "OUTPUT_BASE", output_root)


def test_build_bundle_assembles_op_segments_jingles_ed(monkeypatch, fake_story_factory):
    """Bundle should assemble [OP, seg1, jingle, seg2, jingle, ..., segN, ED]."""
    make_story, output_root = fake_story_factory
    _patch_paths(monkeypatch, output_root)

    s1 = make_story("story_one", story_id=1)
    s2 = make_story("story_two", story_id=2)
    s3 = make_story("story_three", story_id=3)

    op = output_root / "op.mp4"
    op.write_bytes(b"op")
    ed = output_root / "ed.mp4"
    ed.write_bytes(b"ed")
    jingle = output_root / "jingle.mp3"
    jingle.write_bytes(b"jingle")

    # Capture the inputs given to ffmpeg helpers
    from app.services import bundle_generator

    create_calls = []
    burn_calls = []
    concat_calls: list[tuple[list[Path], Path]] = []

    def fake_create_video(images, narration, output_path, **kwargs):
        # Verify OP/ED disabled per-segment
        assert kwargs.get("include_op") is False
        assert kwargs.get("include_ed") is False
        assert kwargs.get("include_title_card") is True
        create_calls.append(output_path)
        Path(output_path).write_bytes(b"raw-segment")
        return Path(output_path)

    def fake_burn(story, raw_video, final_output, title_card, title_audio,
                  include_op_offset=True):
        # Bundle segments must NOT include OP offset for subtitle timing
        assert include_op_offset is False
        burn_calls.append((story.title, final_output))
        Path(final_output).write_bytes(b"final-segment")

    def fake_make_jingle(path: Path, *, target_width: int, target_height: int) -> Path:
        # Should not be called when a real jingle file is provided
        path.write_bytes(b"silent-jingle")
        return path

    def fake_concat(parts, output, **kwargs):
        concat_calls.append((list(parts), Path(output)))
        Path(output).write_bytes(b"final-bundle")

    monkeypatch.setattr(bundle_generator, "create_video", fake_create_video)
    monkeypatch.setattr(bundle_generator, "_burn_long_scroll_subtitles", fake_burn)
    monkeypatch.setattr(bundle_generator, "_make_silent_jingle", fake_make_jingle)
    monkeypatch.setattr(bundle_generator, "concat_videos", fake_concat)

    bundle_path = bundle_generator.build_bundle(
        stories=[s1, s2, s3],
        bundle_name="my_bundle",
        op_path=op,
        ed_path=ed,
        jingle_path=jingle,
    )

    # 3 segments built
    assert len(create_calls) == 3
    assert len(burn_calls) == 3

    # concat called once with the right ordering
    assert len(concat_calls) == 1
    parts, _output = concat_calls[0]
    # Expected ordering: OP, seg1, jingle, seg2, jingle, seg3, ED  →  7 items
    assert len(parts) == 7
    assert parts[0] == op
    assert parts[-1] == ed
    # Jingle appears at index 2 and 4 (between segments)
    assert parts[2] == jingle
    assert parts[4] == jingle
    # Bundle file path returned correctly
    assert bundle_path.name.endswith(".mp4")


def test_build_bundle_single_story_has_no_jingle(monkeypatch, fake_story_factory):
    """Single-story bundle should produce [OP, seg1, ED] with no jingle."""
    make_story, output_root = fake_story_factory
    _patch_paths(monkeypatch, output_root)

    s1 = make_story("only_story")
    op = output_root / "op.mp4"
    op.write_bytes(b"op")
    ed = output_root / "ed.mp4"
    ed.write_bytes(b"ed")
    jingle = output_root / "jingle.mp3"
    jingle.write_bytes(b"jingle")

    from app.services import bundle_generator

    concat_calls: list[tuple[list[Path], Path]] = []

    def noop_create(images, narration, output_path, **kwargs):
        Path(output_path).write_bytes(b"x")
        return Path(output_path)

    def noop_burn(*args, **kwargs):
        # final_output is third positional arg
        Path(args[2]).write_bytes(b"x")

    def noop_concat(parts, output, **kwargs):
        concat_calls.append((list(parts), Path(output)))
        Path(output).write_bytes(b"x")

    monkeypatch.setattr(bundle_generator, "create_video", noop_create)
    monkeypatch.setattr(bundle_generator, "_burn_long_scroll_subtitles", noop_burn)
    monkeypatch.setattr(bundle_generator, "concat_videos", noop_concat)

    bundle_generator.build_bundle(
        stories=[s1],
        bundle_name="single_bundle",
        op_path=op, ed_path=ed, jingle_path=jingle,
    )

    parts, _ = concat_calls[0]
    assert parts == [op, parts[1], ed]


def test_build_bundle_no_jingle_path_uses_silent_fallback(monkeypatch, fake_story_factory):
    """If jingle_path is None, a silent 0.5s jingle is generated as fallback."""
    make_story, output_root = fake_story_factory
    _patch_paths(monkeypatch, output_root)

    s1 = make_story("a")
    s2 = make_story("b")
    op = output_root / "op.mp4"
    op.write_bytes(b"op")
    ed = output_root / "ed.mp4"
    ed.write_bytes(b"ed")

    from app.services import bundle_generator

    silent_called = []

    def fake_silent(path, *, target_width, target_height):
        silent_called.append(path)
        Path(path).write_bytes(b"silent")
        return path

    monkeypatch.setattr(bundle_generator, "create_video",
                        lambda images, narration, output_path, **kw: Path(output_path).write_bytes(b"x") or Path(output_path))
    monkeypatch.setattr(bundle_generator, "_burn_long_scroll_subtitles",
                        lambda *a, **k: Path(a[2]).write_bytes(b"x"))
    monkeypatch.setattr(bundle_generator, "_make_silent_jingle", fake_silent)
    monkeypatch.setattr(bundle_generator, "concat_videos",
                        lambda parts, output, **kw: Path(output).write_bytes(b"x"))

    bundle_generator.build_bundle(
        stories=[s1, s2],
        bundle_name="silent_bundle",
        op_path=op, ed_path=ed, jingle_path=None,
    )

    # Silent jingle generated exactly once
    assert len(silent_called) == 1


def test_build_bundle_writes_manifest(monkeypatch, fake_story_factory):
    """Manifest JSON records story order and metadata."""
    make_story, output_root = fake_story_factory
    _patch_paths(monkeypatch, output_root)

    s1 = make_story("alpha", story_id=10)
    s2 = make_story("beta", story_id=20)

    from app.services import bundle_generator

    monkeypatch.setattr(bundle_generator, "create_video",
                        lambda images, narration, output_path, **kw: Path(output_path).write_bytes(b"x") or Path(output_path))
    monkeypatch.setattr(bundle_generator, "_burn_long_scroll_subtitles",
                        lambda *a, **k: Path(a[2]).write_bytes(b"x"))
    monkeypatch.setattr(bundle_generator, "concat_videos",
                        lambda parts, output, **kw: Path(output).write_bytes(b"x"))
    monkeypatch.setattr(bundle_generator, "_make_silent_jingle",
                        lambda p, **kw: Path(p).write_bytes(b"x") or p)

    bundle_generator.build_bundle(
        stories=[s1, s2],
        bundle_name="manifest_bundle",
        op_path=None, ed_path=None, jingle_path=None,
    )

    from app.utils.paths import bundle_manifest_path
    manifest = json.loads(bundle_manifest_path("manifest_bundle").read_text())
    assert manifest["name"] == "manifest_bundle"
    assert [s["id"] for s in manifest["stories"]] == [10, 20]
    assert [s["title"] for s in manifest["stories"]] == ["alpha", "beta"]


def test_build_bundle_cleans_segments_by_default(monkeypatch, fake_story_factory):
    """Default behavior: segments/ deleted on success to free disk space."""
    make_story, output_root = fake_story_factory
    _patch_paths(monkeypatch, output_root)

    s1 = make_story("a")
    s2 = make_story("b")

    from app.services import bundle_generator

    monkeypatch.setattr(bundle_generator, "create_video",
                        lambda images, narration, output_path, **kw: Path(output_path).write_bytes(b"x") or Path(output_path))
    monkeypatch.setattr(bundle_generator, "_burn_long_scroll_subtitles",
                        lambda *a, **k: Path(a[2]).write_bytes(b"x"))
    monkeypatch.setattr(bundle_generator, "concat_videos",
                        lambda parts, output, **kw: Path(output).write_bytes(b"x"))
    monkeypatch.setattr(bundle_generator, "_make_silent_jingle",
                        lambda p, **kw: Path(p).write_bytes(b"x") or p)

    bundle_generator.build_bundle(
        stories=[s1, s2],
        bundle_name="cleanup_test",
        op_path=None, ed_path=None, jingle_path=None,
    )

    from app.utils.paths import bundle_dir
    seg_dir = bundle_dir("cleanup_test") / "segments"
    assert not seg_dir.exists(), "segments/ should be deleted on success"


def test_build_bundle_keep_segments_when_flagged(monkeypatch, fake_story_factory):
    """keep_segments=True preserves the segments/ directory."""
    make_story, output_root = fake_story_factory
    _patch_paths(monkeypatch, output_root)

    s1 = make_story("a")

    from app.services import bundle_generator

    monkeypatch.setattr(bundle_generator, "create_video",
                        lambda images, narration, output_path, **kw: Path(output_path).write_bytes(b"x") or Path(output_path))
    monkeypatch.setattr(bundle_generator, "_burn_long_scroll_subtitles",
                        lambda *a, **k: Path(a[2]).write_bytes(b"x"))
    monkeypatch.setattr(bundle_generator, "concat_videos",
                        lambda parts, output, **kw: Path(output).write_bytes(b"x"))

    bundle_generator.build_bundle(
        stories=[s1],
        bundle_name="keep_test",
        op_path=None, ed_path=None, jingle_path=None,
        keep_segments=True,
    )

    from app.utils.paths import bundle_dir
    seg_dir = bundle_dir("keep_test") / "segments"
    assert seg_dir.exists(), "segments/ should remain when keep_segments=True"
    assert any(seg_dir.iterdir()), "segments/ should contain files"


def test_format_chapter_timestamp():
    """Chapter timestamps use MM:SS for under an hour, H:MM:SS otherwise."""
    from app.services.bundle_generator import format_chapter_timestamp

    assert format_chapter_timestamp(0) == "00:00"
    assert format_chapter_timestamp(83) == "01:23"
    assert format_chapter_timestamp(3599) == "59:59"
    # YouTube requires H:MM:SS once over an hour
    assert format_chapter_timestamp(3600) == "1:00:00"
    assert format_chapter_timestamp(3661) == "1:01:01"


def test_render_chapters_block():
    """Chapter block uses YouTube auto-detect format (00:00 Title per line)."""
    from app.services.bundle_generator import render_chapters_block

    chapters = [
        {"title": "オープニング", "start_seconds": 0.0},
        {"title": "第1話", "start_seconds": 90.0},
        {"title": "エンディング", "start_seconds": 5400.0},
    ]
    block = render_chapters_block(chapters)
    lines = block.split("\n")
    assert lines[0].startswith("00:00 ")
    assert "オープニング" in lines[0]
    assert lines[1].startswith("01:30 ")
    assert lines[2].startswith("1:30:00 ")


def test_build_bundle_writes_chapters(monkeypatch, fake_story_factory):
    """Bundle manifest contains chapter offsets aligned to YouTube format."""
    make_story, output_root = fake_story_factory
    _patch_paths(monkeypatch, output_root)

    s1 = make_story("alpha", story_id=1)
    s2 = make_story("beta", story_id=2)

    op = output_root / "op.mp4"
    op.write_bytes(b"op")
    ed = output_root / "ed.mp4"
    ed.write_bytes(b"ed")
    jingle = output_root / "jingle.mp3"
    jingle.write_bytes(b"j")

    from app.services import bundle_generator

    # Stub durations: OP=10s, segment=300s each, jingle=2s
    fake_durations = {
        op: 10.0,
        jingle: 2.0,
    }

    def fake_get_dur(p):
        return fake_durations.get(Path(p), 300.0)

    monkeypatch.setattr(bundle_generator, "get_audio_duration", fake_get_dur)
    monkeypatch.setattr(bundle_generator, "create_video",
                        lambda images, narration, output_path, **kw: Path(output_path).write_bytes(b"x") or Path(output_path))
    monkeypatch.setattr(bundle_generator, "_burn_long_scroll_subtitles",
                        lambda *a, **k: Path(a[2]).write_bytes(b"x"))
    monkeypatch.setattr(bundle_generator, "concat_videos",
                        lambda parts, output, **kw: Path(output).write_bytes(b"x"))

    bundle_generator.build_bundle(
        stories=[s1, s2],
        bundle_name="chapters_test",
        op_path=op, ed_path=ed, jingle_path=jingle,
    )

    from app.utils.paths import bundle_manifest_path
    manifest = json.loads(bundle_manifest_path("chapters_test").read_text())
    chapters = manifest["chapters"]

    # Expected: OP @ 0, alpha @ 10, beta @ 10+300+2=312, ED @ 312+300=612
    assert chapters[0] == {"title": "オープニング", "start_seconds": 0.0}
    assert chapters[1]["title"] == "alpha"
    assert chapters[1]["start_seconds"] == 10.0
    assert chapters[1]["story_id"] == 1
    assert chapters[2]["title"] == "beta"
    assert chapters[2]["start_seconds"] == 312.0
    assert chapters[3] == {"title": "エンディング", "start_seconds": 612.0}


def test_estimate_chapters_from_manifest(monkeypatch, fake_story_factory, tmp_path):
    """Retroactive chapter computation for old bundles whose manifest lacks them.

    Uses each story's narration_complete.wav duration + OP/jingle/silence overhead.
    Story narration files are looked up from story_dir(title, 'long').
    """
    make_story, output_root = fake_story_factory
    _patch_paths(monkeypatch, output_root)

    # Make the on-disk story dirs so narration_path lookups succeed
    s1 = make_story("alpha")
    s2 = make_story("beta")

    op = output_root / "op.mp4"
    op.write_bytes(b"op")
    jingle = output_root / "jingle.mp3"
    jingle.write_bytes(b"j")

    # Stub config
    import app.config as cfg
    monkeypatch.setattr(cfg, "CONFIG_PATH", tmp_path / "c.toml")
    cfg.save_config({
        "op_path": str(op),
        "ed_path": "",
        "bundle_jingle_path": str(jingle),
        "leading_silence": 2.0,
        "trailing_silence": 2.0,
    })

    # Stub durations
    from app.services import bundle_generator
    fake_durs = {op: 10.0, jingle: 2.0}

    def fake_get(p):
        # narration_complete.wav = 300s, title_narration.wav = 5s
        if Path(p).name == "narration_complete.wav":
            return 300.0
        if Path(p).name == "title_narration.wav":
            return 5.0
        return fake_durs.get(Path(p), 0.0)

    monkeypatch.setattr(bundle_generator, "get_audio_duration", fake_get)

    manifest = {
        "name": "old_bundle",
        "stories": [
            {"id": s1.id, "title": s1.title},
            {"id": s2.id, "title": s2.title},
        ],
    }

    chapters = bundle_generator.estimate_chapters_from_manifest(manifest)

    # Expected overhead per story:
    #   1.0 (title_silence_before) + 5.0 (title_dur) + 1.0 (title_silence_after)
    #   + 2.0 (leading) + 300.0 (narration) + 2.0 (trailing) = 311.0s
    # Layout: OP (10s) → alpha (311s) → jingle (2s) → beta (311s)
    assert chapters[0] == {"title": "オープニング", "start_seconds": 0.0}
    assert chapters[1]["title"] == "alpha"
    assert chapters[1]["start_seconds"] == 10.0
    assert chapters[2]["title"] == "beta"
    assert chapters[2]["start_seconds"] == 10.0 + 311.0 + 2.0


def test_build_bundle_missing_narration_raises(monkeypatch, fake_story_factory):
    """If a story is missing narration_complete.wav, raise a clear error."""
    make_story, output_root = fake_story_factory
    _patch_paths(monkeypatch, output_root)

    s1 = make_story("incomplete")
    # Remove narration to simulate incomplete story
    (output_root / "incomplete" / "narration_complete.wav").unlink()

    from app.services import bundle_generator

    with pytest.raises(FileNotFoundError, match="narration"):
        bundle_generator.build_bundle(
            stories=[s1],
            bundle_name="should_fail",
            op_path=None, ed_path=None, jingle_path=None,
        )
