from __future__ import annotations

from pathlib import Path

from app.config import get as cfg_get
from app.utils.ffmpeg import (
    add_fade,
    add_fade_to_clip,
    clear_duration_cache,
    concat_videos,
    create_slideshow,
    create_title_clip,
    mix_bgm,
)
from app.utils.log import get_logger

log = get_logger("kaidan.video")

# Silence padding (seconds) added before/after narration for smooth transitions
LEADING_SILENCE = 2.0
TRAILING_SILENCE = 2.0


def create_video(
    images: list[Path],
    narration: Path,
    output_path: Path,
    bgm_path: str | None = None,
    durations: list[float] | None = None,
    title_card: Path | None = None,
    title_audio: Path | None = None,
    progress_callback=None,
    leading_silence: float | None = None,
    trailing_silence: float | None = None,
    include_op: bool = True,
    include_ed: bool = True,
    include_title_card: bool = True,
    target_width: int | None = None,
    target_height: int | None = None,
) -> Path:
    """Create the final video from images, narration, and optional BGM."""
    fps = cfg_get("fps")
    fade_in = cfg_get("fade_in")
    bgm = bgm_path or cfg_get("bgm_path")
    bgm_volume = cfg_get("bgm_volume")
    lead_sil = leading_silence if leading_silence is not None else LEADING_SILENCE
    trail_sil = trailing_silence if trailing_silence is not None else TRAILING_SILENCE

    temp_dir = output_path.parent
    slideshow_path = temp_dir / "slideshow_temp.mp4"
    faded_path = temp_dir / "faded_temp.mp4"

    # Step 0: Normalize volume + add leading/trailing silence
    from app.utils.ffmpeg import get_audio_duration, run_ffmpeg

    # 0a: Normalize
    normalized = temp_dir / "narration_normalized.wav"
    log.info("ナレーション音量ノーマライズ中...")
    run_ffmpeg([
        "-i", str(narration),
        "-af", "loudnorm=I=-14:TP=-1:LRA=11",
        str(normalized),
    ])

    # 0b: Add leading/trailing silence (for ED transition)
    boosted_narration = temp_dir / "narration_boosted.wav"
    narr_dur = get_audio_duration(normalized)
    total_dur = lead_sil + narr_dur + trail_sil
    log.info("前後無音追加中（lead=%.1fs, narr=%.1fs, trail=%.1fs → 計%.1fs）...",
             lead_sil, narr_dur, trail_sil, total_dur)
    lead_ms = int(lead_sil * 1000)
    trail_ms = trail_sil
    run_ffmpeg([
        "-i", str(normalized),
        "-af", f"adelay={lead_ms}|{lead_ms},apad=pad_dur={trail_ms}",
        "-t", f"{total_dur:.3f}",
        str(boosted_narration),
    ])
    # Verify duration matches expectation
    actual_dur = get_audio_duration(boosted_narration)
    if abs(actual_dur - total_dur) > 0.5:
        log.warning("⚠ 音声尺ズレ: 期待=%.2fs, 実際=%.2fs (差=%.2fs)",
                     total_dur, actual_dur, actual_dur - total_dur)
    else:
        log.info("音声尺OK: %.2fs", actual_dur)
    normalized.unlink(missing_ok=True)

    # Step 1: Create slideshow
    if progress_callback:
        progress_callback(1, 4)
    log.info("スライドショー作成中...")
    get_audio_duration(boosted_narration)
    create_slideshow(
        images, boosted_narration, slideshow_path, fps=fps, durations=durations,
        target_width=target_width, target_height=target_height,
    )

    # Step 2: Add fade-in only (no fade-out, ED handles the ending)
    if progress_callback:
        progress_callback(2, 3)
    log.info("フェードイン追加中...")
    add_fade(slideshow_path, faded_path, fade_in=fade_in, fade_out=0)

    # Step 3: Mix BGM if configured
    if progress_callback:
        progress_callback(3, 5)
    bgm_mixed_path = temp_dir / "bgm_mixed.mp4"
    if bgm and Path(bgm).exists():
        log.info("BGMミックス中...")
        mix_bgm(faded_path, Path(bgm), bgm_mixed_path, bgm_volume=bgm_volume)
    else:
        faded_path.rename(bgm_mixed_path)

    # Step 4: Concat OP + title clip + main + ED
    if progress_callback:
        progress_callback(4, 5)
    op_path = cfg_get("op_path")
    op_fade = cfg_get("op_fade_out")
    ed_path = cfg_get("ed_path")

    parts = []

    # OP with fade-out
    if include_op and op_path and Path(op_path).exists():
        log.info("OP追加中（フェードアウト: %.1fs）...", op_fade)
        op_faded = temp_dir / "op_faded.mp4"
        add_fade_to_clip(Path(op_path), op_faded, fade_out=op_fade)
        parts.append(op_faded)

    # Title clip (title card image + title narration with pauses)
    title_clip_path = temp_dir / "title_clip.mp4"
    if include_title_card and title_card and title_card.exists() and title_audio and title_audio.exists():
        log.info("タイトルクリップ作成中...")
        create_title_clip(title_card, title_audio, title_clip_path, fps=fps)
        parts.append(title_clip_path)

    parts.append(bgm_mixed_path)

    # ED
    if include_ed and ed_path and Path(ed_path).exists():
        log.info("ED追加中...")
        parts.append(Path(ed_path))

    if len(parts) > 1:
        log.info("OP/ED結合中...")
        tw = target_width or 1920
        th = target_height or 1080
        concat_videos(parts, output_path, width=tw, height=th)
    else:
        bgm_mixed_path.rename(output_path)

    # Cleanup temp files
    slideshow_path.unlink(missing_ok=True)
    boosted_narration.unlink(missing_ok=True)
    if faded_path.exists() and faded_path != output_path:
        faded_path.unlink(missing_ok=True)
    bgm_mixed_path.unlink(missing_ok=True)
    op_faded_path = temp_dir / "op_faded.mp4"
    op_faded_path.unlink(missing_ok=True)
    title_clip_path.unlink(missing_ok=True)

    clear_duration_cache()
    # Verify final video duration
    final_dur = get_audio_duration(output_path)
    expected_min = total_dur  # At minimum, slideshow duration
    log.info("動画生成完了: %s (尺: %.1fs, ナレーション部: %.1fs)", output_path, final_dur, total_dur)
    if final_dur < total_dur - 1.0:
        log.warning("⚠ 動画尺不足: 期待≥%.1fs, 実際=%.1fs (%.1fs不足)",
                     total_dur, final_dur, total_dur - final_dur)
    return output_path
