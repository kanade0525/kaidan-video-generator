from __future__ import annotations

from pathlib import Path

from app.config import get as cfg_get
from app.utils.ffmpeg import add_fade, add_fade_to_clip, concat_videos, create_slideshow, mix_bgm
from app.utils.log import get_logger

log = get_logger("kaidan.video")


def create_video(
    images: list[Path],
    narration: Path,
    output_path: Path,
    bgm_path: str | None = None,
    durations: list[float] | None = None,
    progress_callback=None,
) -> Path:
    """Create the final video from images, narration, and optional BGM."""
    fps = cfg_get("fps")
    fade_in = cfg_get("fade_in")
    fade_out = cfg_get("fade_out")
    bgm = bgm_path or cfg_get("bgm_path")
    bgm_volume = cfg_get("bgm_volume")

    temp_dir = output_path.parent
    slideshow_path = temp_dir / "slideshow_temp.mp4"
    faded_path = temp_dir / "faded_temp.mp4"

    # Step 0: Normalize volume + add leading/trailing silence
    from app.utils.ffmpeg import run_ffmpeg, get_audio_duration

    # 0a: Normalize
    normalized = temp_dir / "narration_normalized.wav"
    log.info("ナレーション音量ノーマライズ中...")
    run_ffmpeg([
        "-i", str(narration),
        "-af", "loudnorm=I=-14:TP=-1:LRA=11",
        str(normalized),
    ])

    # 0b: Add 2s leading silence + 2s trailing silence (for ED transition)
    boosted_narration = temp_dir / "narration_boosted.wav"
    narr_dur = get_audio_duration(normalized)
    total_dur = 2.0 + narr_dur + 2.0
    log.info("前後無音追加中（計%.1fs）...", total_dur)
    run_ffmpeg([
        "-i", str(normalized),
        "-af", "adelay=2000|2000",
        "-t", f"{total_dur:.3f}",
        str(boosted_narration),
    ])
    normalized.unlink(missing_ok=True)

    # Step 1: Create slideshow
    if progress_callback:
        progress_callback(1, 4)
    log.info("スライドショー作成中...")
    audio_dur = get_audio_duration(boosted_narration)
    create_slideshow(images, boosted_narration, slideshow_path, fps=fps, durations=durations)

    # Step 2: Add fade-in only (no fade-out, ED handles the ending)
    if progress_callback:
        progress_callback(2, 3)
    log.info("フェードイン追加中...")
    add_fade(slideshow_path, faded_path, fade_in=fade_in, fade_out=0)

    # Step 3: Mix BGM if configured
    if progress_callback:
        progress_callback(3, 5)
    bgm_path = temp_dir / "bgm_mixed.mp4"
    if bgm and Path(bgm).exists():
        log.info("BGMミックス中...")
        mix_bgm(faded_path, Path(bgm), bgm_path, bgm_volume=bgm_volume)
    else:
        faded_path.rename(bgm_path)

    # Step 4: Concat OP + main + ED
    if progress_callback:
        progress_callback(4, 5)
    op_path = cfg_get("op_path")
    op_fade = cfg_get("op_fade_out")
    ed_path = cfg_get("ed_path")

    parts = []

    # OP with fade-out
    if op_path and Path(op_path).exists():
        log.info("OP追加中（フェードアウト: %.1fs）...", op_fade)
        op_faded = temp_dir / "op_faded.mp4"
        add_fade_to_clip(Path(op_path), op_faded, fade_out=op_fade)
        parts.append(op_faded)

    parts.append(bgm_path)

    # ED
    if ed_path and Path(ed_path).exists():
        log.info("ED追加中...")
        parts.append(Path(ed_path))

    if len(parts) > 1:
        log.info("OP/ED結合中...")
        concat_videos(parts, output_path)
    else:
        bgm_path.rename(output_path)

    # Cleanup temp files
    slideshow_path.unlink(missing_ok=True)
    boosted_narration.unlink(missing_ok=True)
    if faded_path.exists() and faded_path != output_path:
        faded_path.unlink(missing_ok=True)
    bgm_path.unlink(missing_ok=True)
    op_faded_path = temp_dir / "op_faded.mp4"
    op_faded_path.unlink(missing_ok=True)

    log.info("動画生成完了: %s", output_path)
    return output_path
