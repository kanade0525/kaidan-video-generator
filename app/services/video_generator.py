from __future__ import annotations

from pathlib import Path

from app.config import get as cfg_get
from app.utils.ffmpeg import add_fade, create_slideshow, mix_bgm
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

    # Step 0: Add leading silence + boost narration volume
    boosted_narration = temp_dir / "narration_boosted.wav"
    from app.utils.ffmpeg import run_ffmpeg
    log.info("ナレーション音量ブースト + 冒頭無音追加中...")
    run_ffmpeg([
        "-i", str(narration),
        "-af", "adelay=2000|2000,volume=3.0",
        str(boosted_narration),
    ])

    # Step 1: Create slideshow
    if progress_callback:
        progress_callback(1, 4)
    log.info("スライドショー作成中...")
    create_slideshow(images, boosted_narration, slideshow_path, fps=fps, durations=durations)

    # Step 2: Add fade effects
    if progress_callback:
        progress_callback(2, 3)
    log.info("フェード効果追加中...")
    add_fade(slideshow_path, faded_path, fade_in=fade_in, fade_out=fade_out)

    # Step 3: Mix BGM if configured
    if progress_callback:
        progress_callback(3, 3)
    if bgm and Path(bgm).exists():
        log.info("BGMミックス中...")
        mix_bgm(faded_path, Path(bgm), output_path, bgm_volume=bgm_volume)
    else:
        faded_path.rename(output_path)

    # Cleanup temp files
    slideshow_path.unlink(missing_ok=True)
    boosted_narration.unlink(missing_ok=True)
    if faded_path.exists() and faded_path != output_path:
        faded_path.unlink(missing_ok=True)

    log.info("動画生成完了: %s", output_path)
    return output_path
