from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from app import database as db
from app.models import Story
from app.services import image_generator, scraper, text_processor, video_generator, voice_generator
from app.utils.log import get_logger
from app.utils.paths import (
    audio_dir,
    chunks_path,
    images_dir,
    meta_path,
    narration_path,
    narration_source_path,
    original_chunks_path,
    processed_text_path,
    raw_content_path,
    source_chunks_path,
    story_dir,
    timestamps_path,
    video_path,
)

log = get_logger("kaidan.stages")

ProgressCallback = Callable[[int, int], None] | None


def do_scrape(story: Story, progress_callback: ProgressCallback = None) -> None:
    """Stage: Fetch story content from URL."""
    log.info("[scrape] %s", story.title)
    if progress_callback:
        progress_callback(0, 1)
    content = scraper.fetch_story_content(story.url)

    ct = story.content_type
    out = raw_content_path(story.title, ct)
    out.write_text(content, encoding="utf-8")
    db.update_char_count(story.id, len(content))
    if progress_callback:
        progress_callback(1, 1)
    log.info("[scrape] 保存: %s (%d chars)", out.name, len(content))


def backfill_char_counts() -> int:
    """Idempotent backfill of char_count from raw_content for stories missing it.

    Long stories scraped before char_count tracking have NULL. Reads each
    story's raw_content.txt (if present) and records its length. Returns the
    number updated. Cheap after the first run — remaining NULL rows are
    un-scraped stories with no raw file, which are skipped.
    """
    updated = 0
    for story in db.get_stories_missing_char_count():
        try:
            raw = raw_content_path(story.title, story.content_type)
            if raw.exists():
                db.update_char_count(story.id, len(raw.read_text(encoding="utf-8")))
                updated += 1
        except Exception as e:  # noqa: BLE001 — best-effort backfill
            log.warning("[backfill char_count] %s: %s", story.title, e)
    if updated:
        log.info("[backfill char_count] %d件の文字数を補完", updated)
    return updated


def do_text(
    story: Story,
    progress_callback: ProgressCallback = None,
    use_ai_proofread: bool | None = None,
) -> None:
    """Stage: Process text with MeCab (optional Gemini AI proofread).

    `use_ai_proofread=None` (ワーカー経由の既定) では設定
    `text_ai_proofread_enabled` に従う。True にしておけば「AI校正プレビュー →
    適用」の手動ステップ無しで校正済みテキストが得られる。UI からの再処理は
    チェックボックスの値を明示的に渡すのでこの既定より優先される。
    """
    from app.config import get as cfg_get

    if use_ai_proofread is None:
        use_ai_proofread = bool(cfg_get("text_ai_proofread_enabled"))
    log.info("[text] %s (ai_proofread=%s)", story.title, use_ai_proofread)
    ct = story.content_type
    if progress_callback:
        progress_callback(0, 2)
    raw = raw_content_path(story.title, ct).read_text(encoding="utf-8")

    processed = text_processor.process_text(raw, use_ai_proofread=use_ai_proofread)
    processed_text_path(story.title, ct).write_text(processed, encoding="utf-8")
    if progress_callback:
        progress_callback(1, 2)

    chunks = text_processor.split_into_chunks(processed)
    chunks_path(story.title, ct).write_text(
        json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Save original text chunks (kanji) mapped 1:1 to hiragana chunks for subtitles
    orig_chunks = text_processor.split_into_n_chunks(raw, len(chunks))
    original_chunks_path(story.title, ct).write_text(
        json.dumps(orig_chunks, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # アクセント用の「漢字混じり原稿」。読みが一致するチャンクだけ音声生成で
    # 採用されるので、生成に失敗しても後段はひらがな原稿で問題なく動く。
    save_narration_source(story, raw, processed)

    if progress_callback:
        progress_callback(2, 2)
    log.info("[text] %d チャンク生成 (原文チャンクも保存)", len(chunks))


def save_narration_source(story: Story, raw: str, processed: str) -> bool:
    """漢字混じりのナレーション原稿を生成して保存する。

    ひらがなチャンクと文単位で対応付けて `source_chunks.json` に置く。長さ基準で
    別々に分割すると境界がずれ、音声生成側の読み一致判定がまず通らない。
    対応付けできない場合や MeCab が無い場合は何も保存せず False を返す
    (ひらがな原稿のみで従来どおり動作する)。
    """
    ct = story.content_type
    try:
        mixed = text_processor.build_narration_source(raw)
    except Exception as e:  # noqa: BLE001 — 任意機能なので失敗を伝播させない
        log.warning("[text] ナレーション原稿の生成に失敗: %s", e)
        return False
    if not mixed:
        return False

    source_chunks = text_processor.align_chunks(processed, mixed)
    if source_chunks is None:
        log.info("[text] ひらがな原稿と対応付けできず、アクセント原稿は使わない")
        return False

    narration_source_path(story.title, ct).write_text(mixed, encoding="utf-8")
    source_chunks_path(story.title, ct).write_text(
        json.dumps(source_chunks, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    log.info("[text] ナレーション原稿 (漢字混じり) を保存: %d チャンク", len(source_chunks))
    return True


def load_source_chunks(story: Story, n_chunks: int) -> list[str] | None:
    """保存済みのアクセント用チャンクを読む。数が合わなければ None。"""
    path = source_chunks_path(story.title, story.content_type)
    if not path.exists():
        return None
    try:
        chunks = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        log.warning("[voice] ナレーション原稿の読込に失敗: %s", e)
        return None
    if not isinstance(chunks, list) or len(chunks) != n_chunks:
        return None
    return chunks


def do_voice(story: Story, progress_callback: ProgressCallback = None) -> None:
    """Stage: Generate voice narration via VOICEVOX."""
    log.info("[voice] %s", story.title)
    ct = story.content_type
    chunks = json.loads(chunks_path(story.title, ct).read_text(encoding="utf-8"))
    voice_generator.generate_narration(
        chunks, audio_dir(story.title, ct),
        progress_callback=progress_callback,
        source_chunks=load_source_chunks(story, len(chunks)),
    )


def do_images(story: Story, progress_callback: ProgressCallback = None) -> None:
    """Stage: Generate images for the story."""
    log.info("[images] %s", story.title)
    ct = story.content_type
    raw = raw_content_path(story.title, ct).read_text(encoding="utf-8")
    category = story.categories[0] if story.categories else "怪談"
    paths = image_generator.generate_images_for_story(
        raw, story.title, images_dir(story.title, ct),
        category=category, progress_callback=progress_callback,
    )
    log.info("[images] %d 画像生成", len(paths))


TITLE_CARD_FILENAME = "000_title_card.png"
SHORTS_TITLE_CARD_FILENAME = "scene_000.png"


def title_card_filename(content_type: str) -> str:
    """Title-card filename for the given content type.

    Long-form keeps the historical 000_title_card.png. Shorts bake the title
    overlay directly into scene_000.png so only 2 PNGs are produced
    (scene_000.png with overlay + scene_001.png bare scene).
    """
    return SHORTS_TITLE_CARD_FILENAME if content_type == "short" else TITLE_CARD_FILENAME


def resolve_thumbnail_path(title: str, content_type: str) -> Path | None:
    """Resolve the YouTube thumbnail for a story.

    A user-uploaded custom_thumbnail.{png,jpg,jpeg} in the story dir takes
    priority over the auto-generated title card; returns None if neither exists.
    Shared by the manual upload UI and the do_youtube_upload pipeline stage so
    both honor a custom thumbnail — previously the stage always used the title
    card, so custom thumbnails were ignored on auto/scheduled/retry uploads.
    """
    sdir = story_dir(title, content_type)
    for ext in ("png", "jpg", "jpeg"):
        custom = sdir / f"custom_thumbnail.{ext}"
        if custom.exists():
            return custom
    card = images_dir(title, content_type) / title_card_filename(content_type)
    return card if card.exists() else None


def load_scene_images(
    img_dir: Path, slideshow_config_path: Path, content_type: str = "long",
) -> tuple[list[Path], list[float] | None]:
    """Load scene images excluding title card. Returns (images, durations)."""
    title_name = title_card_filename(content_type)

    images: list[Path] = []
    durations: list[float] | None = None

    if slideshow_config_path.exists():
        slide_config = json.loads(slideshow_config_path.read_text())
        if slide_config:
            durations = []
            for slide in slide_config:
                if slide["file"] == title_name:
                    continue
                img_path = img_dir / slide["file"]
                if img_path.exists():
                    images.append(img_path)
                    durations.append(slide.get("duration", 0))

    if not images:
        images = sorted(
            p for p in img_dir.glob("*.png") if p.name != title_name
        )
        durations = None

    return images, durations


def do_video(story: Story, progress_callback: ProgressCallback = None) -> None:
    """Stage: Create final video."""
    log.info("[video] %s", story.title)
    ct = story.content_type
    if progress_callback:
        progress_callback(0, 3)
    img_dir = images_dir(story.title, ct)
    sdir = story_dir(story.title, ct)

    title_card = img_dir / title_card_filename(ct)
    images, durations = load_scene_images(img_dir, sdir / "slideshow.json", ct)

    if not images:
        raise RuntimeError("No images found")

    narration = narration_path(story.title, ct)
    if not narration.exists():
        raise RuntimeError("Narration file not found")

    # Generate title narration audio
    title_audio = None
    if title_card.exists():
        title_audio = sdir / "title_narration.wav"
        voice_generator.generate_title_audio(story.title, title_audio, story.title_furigana)

    # Create video WITHOUT ED first; ED is concatenated after subtitles are
    # burned so the scrolling subtitle's read-tail does not overlap the ED.
    raw_output = sdir / "raw_long.mp4"
    video_generator.create_video(
        images, narration, raw_output,
        durations=durations,
        title_card=title_card if title_card.exists() else None,
        title_audio=title_audio,
        progress_callback=progress_callback,
        include_ed=False,
    )

    # Burn scrolling subtitle (original text) onto the ED-less video
    output = video_path(story.title, ct)
    subtitled = sdir / "subtitled_long.mp4"
    _burn_long_scroll_subtitles(story, raw_output, subtitled, title_card, title_audio)
    raw_output.unlink(missing_ok=True)

    # Append ED after subtitles are burned
    _append_ed(subtitled, output)

    # Generate timestamps for YouTube description
    _save_timestamps(story, title_card, title_audio)


def _append_ed(src: Path, dst: Path) -> None:
    """Append ED video (if configured) to src, writing to dst. No-op if no ED."""
    from pathlib import Path as _P

    from app.config import get as cfg_get
    from app.utils.ffmpeg import concat_videos

    ed_path = cfg_get("ed_path")
    if ed_path and _P(ed_path).exists():
        concat_videos([src, _P(ed_path)], dst, width=1920, height=1080)
        src.unlink(missing_ok=True)
    else:
        src.rename(dst)


def _burn_long_scroll_subtitles(
    story: Story,
    raw_video: Path,
    final_output: Path,
    title_card: Path | None,
    title_audio: Path | None,
    include_op_offset: bool = True,
) -> None:
    """Burn a full-screen scrolling subtitle (original text) onto the long video.

    Uses the same `generate_scroll_image` + overlay approach as Shorts, but
    with 1920x1080 coordinates. Timing is length-proportional across the whole
    narration, avoiding chunk-to-chunk drift from hiragana/kanji length mismatch.

    `include_op_offset=False` is used when the segment is built without OP
    (e.g. bundle segments where OP is added once at the bundle level).

    Falls back to copying `raw_video` → `final_output` if the required inputs
    (original chunks, narration audio) are missing.
    """
    from app.config import get as cfg_get
    from app.utils.ffmpeg import _split_subtitle_text, burn_all_overlays, get_audio_duration

    ct = story.content_type
    sdir = story_dir(story.title, ct)
    orig_chunks_file = original_chunks_path(story.title, ct)
    narration = narration_path(story.title, ct)

    if not orig_chunks_file.exists() or not narration.exists():
        log.info("[video] 原文チャンクまたはナレーションなし、字幕焼き込みスキップ")
        raw_video.rename(final_output)
        return

    subtitle_chunks = json.loads(orig_chunks_file.read_text(encoding="utf-8"))
    subtitle_text = "".join(subtitle_chunks)
    if not subtitle_text.strip():
        log.info("[video] 字幕テキストが空、焼き込みスキップ")
        raw_video.rename(final_output)
        return

    segments = _split_subtitle_text(subtitle_text, max_chars=40)
    scroll_txt = sdir / "scroll_subtitle_long.txt"
    scroll_txt.write_text("\n".join(segments), encoding="utf-8")

    # Scroll timing spans the entire narration.
    # Offset = OP + title_clip + leading_silence
    offset = 0.0
    if include_op_offset:
        op_path = cfg_get("op_path")
        if op_path and Path(op_path).exists():
            offset += get_audio_duration(Path(op_path))
    if title_card and title_card.exists() and title_audio and title_audio.exists():
        offset += 1.0 + get_audio_duration(title_audio) + 1.0
    offset += cfg_get("leading_silence") if cfg_get("leading_silence") is not None else 2.0

    # Extend scroll animation past narration end so the last lines reach
    # the bottom a few seconds after the audio finishes. Scroll speed is
    # effectively unchanged (read_tail is ~1-2% of a typical narration).
    read_tail = 5.0
    scroll_dur = get_audio_duration(narration) + read_tail

    log.info(
        "[video] スクロール字幕焼き込み: %d 行, start=%.2fs, duration=%.1fs",
        len(segments), offset, scroll_dur,
    )
    burn_all_overlays(
        raw_video, final_output,
        scroll_textfile=scroll_txt,
        scroll_start_time=offset,
        scroll_duration=scroll_dur,
        scroll_top=120,
        scroll_bottom=960,
        scroll_font_size=44,
        scroll_line_spacing=32,
        scroll_margin_right=120,
        scroll_video_width=1920,
        scroll_overlay_x=60,
        mask_zones=False,
    )


def _format_timestamp(seconds: float) -> str:
    """Format seconds as M:SS or H:MM:SS."""
    total = int(seconds)
    h, remainder = divmod(total, 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _save_timestamps(
    story: Story,
    title_card: Path | None,
    title_audio: Path | None,
) -> None:
    """Calculate and save video part timestamps for YouTube description."""
    from app.config import get as cfg_get
    from app.utils.ffmpeg import get_audio_duration

    ct = story.content_type
    cursor = 0.0
    parts = []

    # OP
    op_path = cfg_get("op_path")
    if op_path and Path(op_path).exists():
        parts.append({"label": "オープニング", "start": cursor})
        op_dur = get_audio_duration(Path(op_path))
        cursor += op_dur

    # Title card
    if title_card and title_card.exists() and title_audio and title_audio.exists():
        parts.append({"label": "タイトル", "start": cursor})
        title_dur = 1.0 + get_audio_duration(title_audio) + 1.0
        cursor += title_dur

    # Main content
    parts.append({"label": "本編", "start": cursor})
    narration = narration_path(story.title, ct)
    if narration.exists():
        lead = cfg_get("leading_silence") if cfg_get("leading_silence") else 2.0
        narr_dur = get_audio_duration(narration)
        trail = cfg_get("trailing_silence") if cfg_get("trailing_silence") else 2.0
        cursor += lead + narr_dur + trail

    # ED
    ed_path = cfg_get("ed_path")
    if ed_path and Path(ed_path).exists():
        parts.append({"label": "エンディング", "start": cursor})

    ts_file = timestamps_path(story.title, ct)
    ts_file.write_text(
        json.dumps(parts, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    log.info("[video] タイムスタンプ保存: %s", ts_file)


def do_youtube_upload(story: Story, progress_callback: ProgressCallback = None) -> None:
    """Stage: Upload video to YouTube."""
    from app.config import get as cfg_get
    from app.services import youtube_uploader

    log.info("[youtube] %s", story.title)

    # Skip if already uploaded
    if story.youtube_video_id:
        log.info("既にアップロード済み: %s", story.youtube_video_id)
        return

    ct = story.content_type
    vid = video_path(story.title, ct)
    if not vid.exists():
        raise RuntimeError("動画ファイルが見つかりません")

    if not youtube_uploader.is_authenticated():
        raise RuntimeError("YouTube未認証。設定ページから認証を実行してください。")

    from app.services.voice_generator import get_speaker_name
    title_template = cfg_get("youtube_title_template")
    category = story.categories[0] if story.categories else "怪談"
    yt_title = title_template.format(title=story.title, category=category)
    speaker_name = get_speaker_name()
    playlist_url = cfg_get("youtube_playlist_url") or ""
    # Source-aware description template. kikikaikai-sourced stories migrated
    # to long (Short→Long) must credit 奇々怪々, not HHS図書館.
    if story.source == "kikikaikai":
        description_template = cfg_get("long_kikikaikai_youtube_description_template")
        # Load author from meta.json if available (kikikaikai scraper writes it)
        from app.utils.paths import meta_path
        meta_file = meta_path(story.title, ct)
        author = story.author
        if meta_file.exists():
            try:
                meta_data = json.loads(meta_file.read_text(encoding="utf-8"))
                author = meta_data.get("author", author)
            except Exception:
                pass
        description = description_template.format(
            title=story.title, url=story.url, speaker=speaker_name,
            playlist_url=playlist_url, author=author,
        )
    else:
        description_template = cfg_get("youtube_description_template")
        description = description_template.format(
            title=story.title, url=story.url, speaker=speaker_name,
            playlist_url=playlist_url,
        )

    # Insert timestamps if available
    ts_file = timestamps_path(story.title, ct)
    if ts_file.exists():
        parts = json.loads(ts_file.read_text(encoding="utf-8"))
        ts_lines = [f"{_format_timestamp(p['start'])} {p['label']}" for p in parts]
        timestamp_block = "\n".join(ts_lines)
        description = f"{timestamp_block}\n\n{description}"

    tags = cfg_get("youtube_tags")
    category_id = cfg_get("youtube_category_id")
    privacy = cfg_get("youtube_privacy_status")

    publish_at = youtube_uploader.resolve_publish_time()

    # Thumbnail: user-uploaded custom_thumbnail.* takes priority over the
    # auto-generated title card (same resolver as the manual upload UI).
    # 以前はここで thumbnail_path を渡しておらず、長編はサムネ未設定で
    # アップロードされていた(YouTube自動生成フレームになる)。
    thumbnail = resolve_thumbnail_path(story.title, ct)

    result = youtube_uploader.upload_video(
        video_path=vid,
        title=yt_title,
        description=description,
        tags=tags if isinstance(tags, list) else [t.strip() for t in tags.split(",")],
        category_id=category_id,
        privacy_status=privacy,
        publish_at=publish_at,
        thumbnail_path=thumbnail,
        progress_callback=progress_callback,
    )

    db.mark_uploaded(story.id, result["video_id"], publish_at)


def do_submit_report(story: Story, progress_callback: ProgressCallback = None) -> None:
    """Stage: Submit usage report to HHS Library.

    使用報告が必要なのは HHS図書館 (ホラホリ) 由来の作品だけ。奇々怪々由来は
    報告先が無いので、何もせず正常終了してステージだけ進める — こうしないと
    オートパイロットで奇々怪々の Short が最終ステージで必ず失敗して溜まる。
    """
    from app.config import get as cfg_get
    from app.services import youtube_uploader

    log.info("[report] %s", story.title)
    if progress_callback:
        progress_callback(0, 1)

    if story.source != "hhs":
        log.info("[report] HHS由来ではないため使用報告は不要: %s", story.source)
        if progress_callback:
            progress_callback(1, 1)
        return

    if not story.youtube_video_id:
        raise RuntimeError(
            "YouTube動画IDが見つかりません。"
            "先にYouTubeアップロードを実行してください。"
        )

    video_url = f"https://youtube.com/watch?v={story.youtube_video_id}"
    channel_name = cfg_get("youtube_channel_name")
    contact_email = cfg_get("youtube_contact_email")

    if not channel_name or not contact_email:
        raise RuntimeError(
            "使用報告に必要な設定が不足しています。"
            f"チャンネル名: {'設定済' if channel_name else '未設定'}, "
            f"メールアドレス: {'設定済' if contact_email else '未設定'}"
        )

    youtube_uploader.submit_usage_report(
        story_title=story.title,
        video_url=video_url,
        channel_name=channel_name,
        email=contact_email,
    )

    if progress_callback:
        progress_callback(1, 1)
    log.info("[report] 使用報告送信完了: %s", story.title)


# ── Shorts-specific stage functions ────────────────


def do_scrape_short(story: Story, progress_callback: ProgressCallback = None) -> None:
    """Stage: Fetch story content from kikikaikai."""
    from app.services import kikikaikai_scraper

    log.info("[scrape:short] %s", story.title)
    if progress_callback:
        progress_callback(0, 1)

    text, metadata = kikikaikai_scraper.fetch_story_content(story.url)

    ct = story.content_type
    raw_content_path(story.title, ct).write_text(text, encoding="utf-8")

    # Save metadata for credit overlay
    meta = {
        "author": metadata.get("author", story.author),
        "tags": metadata.get("tags", []),
        "char_count": metadata.get("char_count", len(text)),
        "source": "kikikaikai",
        "source_url": story.url,
    }
    meta_path(story.title, ct).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    db.update_char_count(story.id, len(text))

    if progress_callback:
        progress_callback(1, 1)
    log.info("[scrape:short] 保存: %s (%d chars)", story.title, len(text))


def do_voice_short(story: Story, progress_callback: ProgressCallback = None) -> None:
    """Stage: Generate voice + validate duration for shorts."""
    from app.config import get as cfg_get
    from app.utils.ffmpeg import get_audio_duration

    log.info("[voice:short] %s", story.title)
    ct = story.content_type
    chunks = json.loads(chunks_path(story.title, ct).read_text(encoding="utf-8"))
    shorts_speed = cfg_get("shorts_speed")
    voice_generator.generate_narration(
        chunks, audio_dir(story.title, ct),
        progress_callback=progress_callback,
        speed=shorts_speed,
        source_chunks=load_source_chunks(story, len(chunks)),
    )

    # Duration notice (non-blocking).
    # YouTube Shorts制限 (180s) は upload stage で弾く。voice 段階では警告のみ。
    narr = narration_path(story.title, ct)
    duration = get_audio_duration(narr)
    lead = cfg_get("shorts_leading_silence")
    trail = cfg_get("shorts_trailing_silence")
    endscreen = cfg_get("shorts_endscreen_duration") or 0.0
    total = duration + lead + trail + endscreen
    shorts_limit = 180.0

    if total > shorts_limit:
        log.warning(
            "[voice:short] YouTube Shorts尺制限超過: %.1fs > %.0fs "
            "(TikTok等の長尺プラットフォーム向けに動画は生成継続。YouTube Shortsアップロードは後段でスキップされます)",
            total, shorts_limit,
        )
    else:
        log.info("[voice:short] 尺OK: %.1fs (制限: %.0fs)", total, shorts_limit)


def do_images_short(story: Story, progress_callback: ProgressCallback = None) -> None:
    """Stage: Generate images for shorts (vertical, fewer scenes)."""
    from app.config import get as cfg_get

    log.info("[images:short] %s", story.title)
    ct = story.content_type
    raw = raw_content_path(story.title, ct).read_text(encoding="utf-8")
    # カテゴリ未設定を "怪談" で埋めない。埋めると人怖や不思議な話まで
    # 怪談バッジになり、未設定の作品にも誤ったバッジが付く。
    category = story.categories[0] if story.categories else ""

    paths = image_generator.generate_images_for_story(
        raw, story.title, images_dir(story.title, ct),
        category=category,
        progress_callback=progress_callback,
        content_type=ct,
    )
    log.info("[images:short] %d 画像生成", len(paths))


def build_short_credit_lines(story: Story, author: str) -> list[str]:
    """Build the credit overlay lines for a Shorts video.

    HHS図書館は作者情報を取得していないため、出典を「ホラホリ図書館」とし作者行を省く。
    奇々怪々由来は出典+作者の3行構成。
    """
    if story.source == "hhs":
        return ["ホラホリ図書館", f"「{story.title}」"]
    return ["奇々怪々", f"「{story.title}」", f"作者: {author}"]


def do_video_short(story: Story, progress_callback: ProgressCallback = None) -> None:
    """Stage: Create final short video (vertical, no OP/ED, with subtitles + credit)."""
    from app.config import get as cfg_get
    from app.utils.ffmpeg import (
        burn_all_overlays,
        _split_subtitle_text,
        concat_videos,
        generate_black_clip,
        get_audio_duration,
    )

    log.info("[video:short] %s", story.title)
    ct = story.content_type
    if progress_callback:
        progress_callback(0, 3)
    img_dir = images_dir(story.title, ct)
    sdir = story_dir(story.title, ct)
    a_dir = audio_dir(story.title, ct)

    title_card = img_dir / title_card_filename(ct)
    images, durations = load_scene_images(img_dir, sdir / "slideshow.json", ct)
    if not images:
        raise RuntimeError("No images found")

    narration = narration_path(story.title, ct)
    if not narration.exists():
        raise RuntimeError("Narration file not found")

    lead = cfg_get("shorts_leading_silence")
    trail = cfg_get("shorts_trailing_silence")

    # Generate title narration audio for title card
    title_audio = None
    title_clip_duration = 0.0
    shorts_speed = cfg_get("shorts_speed")
    if title_card.exists():
        title_audio = sdir / "title_narration.wav"
        voice_generator.generate_title_audio(story.title, title_audio, story.title_furigana, speed=shorts_speed)
        # Shorts はタイトルクリップ前後の無音を 0 にして即座にナレーションへ
        # 入るため、title_clip_duration は純粋な音声長のみ。
        title_clip_duration = get_audio_duration(title_audio)

    # Step 1: Create base video (no OP/ED, vertical 1080x1920)
    if progress_callback:
        progress_callback(1, 3)
    raw_output = sdir / "raw_short.mp4"
    video_generator.create_video(
        images, narration, raw_output,
        durations=durations,
        title_card=title_card if title_card.exists() else None,
        title_audio=title_audio,
        progress_callback=None,
        leading_silence=lead,
        trailing_silence=trail,
        include_op=False,
        include_ed=False,
        include_title_card=True,
        target_width=1080,
        target_height=1920,
        fade_in=0,
        title_fade_in=0,
        title_fade_out=0,
        title_silence_before=0,
        title_silence_after=0,
    )

    # Step 2: Generate scroll subtitle image and burn all overlays
    if progress_callback:
        progress_callback(2, 3)

    orig_chunks_file = original_chunks_path(story.title, ct)
    hiragana_chunks_file = chunks_path(story.title, ct)

    subtitle_text = ""
    if orig_chunks_file.exists():
        subtitle_chunks = json.loads(orig_chunks_file.read_text(encoding="utf-8"))
        subtitle_text = "".join(subtitle_chunks)
        log.info("[video:short] 字幕: 原文（漢字）使用")
    elif hiragana_chunks_file.exists():
        subtitle_chunks = json.loads(hiragana_chunks_file.read_text(encoding="utf-8"))
        subtitle_text = "".join(subtitle_chunks)
        log.info("[video:short] 字幕: ひらがなテキスト使用（原文チャンクなし）")

    # Generate scroll subtitle text file
    scroll_txt = None
    narration_duration = get_audio_duration(narration)
    scroll_start = title_clip_duration + lead
    scroll_dur = narration_duration

    if subtitle_text:
        # Split into lines and write as plain text for drawtext textfile
        segments = _split_subtitle_text(subtitle_text, max_chars=16)
        scroll_txt = sdir / "scroll_subtitle.txt"
        scroll_txt.write_text("\n".join(segments), encoding="utf-8")
        log.info("[video:short] スクロール字幕テキスト生成: %d行", len(segments))

    # Load metadata for credit overlay
    meta_file = meta_path(story.title, ct)
    author = story.author
    if meta_file.exists():
        meta_data = json.loads(meta_file.read_text(encoding="utf-8"))
        author = meta_data.get("author", author)

    output = video_path(story.title, ct)
    credit_lines = build_short_credit_lines(story, author)

    log.info("[video:short] 字幕・バナー・クレジットを一括焼き込み中...")
    burn_all_overlays(
        raw_output, output,
        scroll_textfile=scroll_txt,
        scroll_start_time=scroll_start,
        scroll_duration=scroll_dur,
        scroll_top=260,
        scroll_bottom=1440,
        scroll_font_size=48,
        scroll_line_spacing=48,
        scroll_margin_right=200,
        banner_text="ショート怪談",
        banner_font_size=64,
        banner_font_color="red",
        banner_margin_top=160,
        banner_start_time=title_clip_duration,
        credit_lines=credit_lines,
        credit_font_size=52,
        credit_margin_bottom=320,
    )
    raw_output.unlink(missing_ok=True)

    # Append black end screen clip for YouTube end screen (min 5s required)
    endscreen_dur = cfg_get("shorts_endscreen_duration")
    if endscreen_dur and endscreen_dur > 0:
        log.info("[video:short] 終了画面用黒画面追加中 (%.1fs)...", endscreen_dur)
        endscreen_clip = sdir / "endscreen_black.mp4"
        generate_black_clip(endscreen_clip, endscreen_dur, width=1080, height=1920)
        final_with_endscreen = sdir / "final_with_endscreen.mp4"
        concat_videos([output, endscreen_clip], final_with_endscreen, width=1080, height=1920)
        output.unlink(missing_ok=True)
        final_with_endscreen.rename(output)
        endscreen_clip.unlink(missing_ok=True)

    if progress_callback:
        progress_callback(3, 3)
    log.info("[video:short] 動画生成完了: %s", output)


def _shorts_credit_block(story: Story, author: str, speaker_name: str) -> str:
    """LLM が書いた説明文に足す出典・クレジット。

    テンプレート経由の説明文には最初から入っているので、そちらには足さない。
    BGM のクレジットは DOVA-SYNDROME の規約上必須なので必ず含める。
    """
    lines = ["\n\n━━━━━━━━━━━━━━━━━━━━"]
    if story.source == "hhs":
        lines += ["引用元: HHS図書館", f"「{story.title}」{story.url}"]
    else:
        lines += [
            "引用元: 奇々怪々",
            f"「{story.title}」{story.url}",
            f"作者: {author}",
        ]
    lines += [
        f"音声: VOICEVOX:{speaker_name}",
        "BGM: 「Where the Light Never Speaks」松浦洋介",
        "(DOVA-SYNDROME: https://dova-s.jp/bgm/play22758.html)",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    return "\n".join(lines)


def do_youtube_upload_short(story: Story, progress_callback: ProgressCallback = None) -> None:
    """Stage: Upload short video to YouTube with kikikaikai credits."""
    from app.config import get as cfg_get
    from app.services import youtube_uploader

    log.info("[youtube:short] %s", story.title)

    if story.youtube_video_id:
        log.info("既にアップロード済み: %s", story.youtube_video_id)
        return

    ct = story.content_type
    vid = video_path(story.title, ct)
    if not vid.exists():
        raise RuntimeError("動画ファイルが見つかりません")

    if not youtube_uploader.is_authenticated():
        raise RuntimeError("YouTube未認証。設定ページから認証を実行してください。")

    # YouTube Shorts の180秒上限を超える動画はアップロード対象外（TikTok等専用）
    from app.utils.ffmpeg import get_audio_duration
    shorts_limit = 180.0
    try:
        vid_dur = get_audio_duration(vid)
    except Exception:
        vid_dur = 0.0
    if vid_dur > shorts_limit:
        raise RuntimeError(
            f"YouTube Shorts尺制限超過のためアップロードスキップ: "
            f"{vid_dur:.1f}s > {shorts_limit:.0f}s"
        )

    # Load metadata for author
    meta_file = meta_path(story.title, ct)
    author = story.author
    if meta_file.exists():
        meta_data = json.loads(meta_file.read_text(encoding="utf-8"))
        author = meta_data.get("author", author)

    from app.services.voice_generator import get_speaker_name

    speaker_name = get_speaker_name()

    is_hhs = (story.source == "hhs")

    # 既定はテンプレート。LLM に書かせると釣りタイトルにはなるが、
    # チャンネルの定型フォーマット (挨拶・引用元・BGMクレジット・タグ) を
    # 一切踏襲しないので、設定で明示的に選んだときだけ使う。
    yt_title = ""
    description = ""
    if (cfg_get("shorts_metadata_source") or "template") == "llm":
        raw_file = raw_content_path(story.title, ct)
        raw_text = raw_file.read_text(encoding="utf-8") if raw_file.exists() else ""
        if raw_text:
            try:
                meta_result = text_processor.generate_shorts_metadata(
                    story.title, raw_text, author,
                )
                yt_title = meta_result["title"]
                # LLM の説明文には出典もクレジットも入らないので補う
                description = meta_result["description"] + _shorts_credit_block(
                    story, author, speaker_name,
                )
            except Exception as e:
                log.warning("[youtube:short] LLM metadata生成失敗: %s", e)

    if not yt_title:
        yt_title = cfg_get("shorts_youtube_title_template").format(title=story.title)
    if not description:
        # HHS由来 Short は ホラホリ規約に沿った引用元表記のテンプレートを使う。
        # テンプレートには出典・BGMクレジットが含まれているので追記しない
        # (追記すると引用元が2回出る)。
        if is_hhs:
            description = cfg_get(
                "shorts_hhs_youtube_description_template",
            ).format(title=story.title, url=story.url, speaker=speaker_name)
        else:
            description = cfg_get(
                "shorts_youtube_description_template",
            ).format(
                title=story.title, url=story.url, author=author, speaker=speaker_name,
            )

    tags = cfg_get("shorts_youtube_tags")
    # HHS由来 Short のみ #ホラホリ タグを付与（規約上の出典タグ慣習）
    if is_hhs:
        tag_list = [t.strip() for t in tags.split(",")] if isinstance(tags, str) else list(tags)
        if "ホラホリ" not in tag_list:
            tag_list.append("ホラホリ")
        tags = tag_list
    category_id = cfg_get("youtube_category_id")
    privacy = cfg_get("youtube_privacy_status")

    publish_at = youtube_uploader.resolve_publish_time()

    # Thumbnail: user-uploaded custom_thumbnail.* takes priority over the
    # auto-generated title card (same resolver as the manual upload UI).
    thumbnail = resolve_thumbnail_path(story.title, ct)

    result = youtube_uploader.upload_video(
        video_path=vid,
        title=yt_title,
        description=description,
        tags=tags if isinstance(tags, list) else [t.strip() for t in tags.split(",")],
        category_id=category_id,
        privacy_status=privacy,
        publish_at=publish_at,
        thumbnail_path=thumbnail,
        progress_callback=progress_callback,
    )

    db.mark_uploaded(story.id, result["video_id"], publish_at)


# Stage function registry: maps (content_type, output_stage) -> processing function
# report_submitted も登録してあるので、ワーカーを起動すればスクレイピングから
# 使用報告までノータッチで流れる (HHS由来以外は自動スキップ)。
STAGE_FUNCTIONS: dict[tuple[str, str], Callable] = {
    ("long", "scraped"): do_scrape,
    ("long", "text_processed"): do_text,
    ("long", "voice_generated"): do_voice,
    ("long", "images_generated"): do_images,
    ("long", "video_complete"): do_video,
    ("long", "youtube_uploaded"): do_youtube_upload,
    ("short", "scraped"): do_scrape_short,
    ("short", "text_processed"): do_text,
    ("short", "voice_generated"): do_voice_short,
    ("short", "images_generated"): do_images_short,
    ("short", "video_complete"): do_video_short,
    ("short", "youtube_uploaded"): do_youtube_upload_short,
    ("long", "report_submitted"): do_submit_report,
    ("short", "report_submitted"): do_submit_report,
}


# API のクレジット切れ・クォータ超過を示す文字列。これを踏んだら以降の
# ストーリーも確実に同じ失敗をするので、1件ずつ失敗扱いにして在庫を焦がす
# のではなく、パイプラインごと止める。
_QUOTA_ERROR_MARKERS = (
    "RESOURCE_EXHAUSTED",
    "credits are depleted",
    "quota",
    "billing",
    "429",
    "insufficient_quota",
)


def is_quota_error(exc: BaseException) -> bool:
    """例外が「お金/回数が尽きた」系かどうか。

    原因が解消するまで何度やっても失敗するので、リトライにも次のストーリーにも
    進ませてはいけない種類のエラー。残高切れの判定は `retry.is_credit_exhausted`
    と共通にし、こちらは RESOURCE_EXHAUSTED 等も含めて広めに拾う。
    """
    from app.pipeline.retry import is_credit_exhausted

    if is_credit_exhausted(exc):
        return True
    message = f"{exc} {getattr(exc, '__cause__', '') or ''}".lower()
    return any(marker.lower() in message for marker in _QUOTA_ERROR_MARKERS)


# ── ステージ実行ゲート (自動運転時のブレーキ) ─────────────────────────
# ワーカーがストーリーを取り出す前に呼ばれる。False を返すと、そのストーリーは
# 取り出されず次のポーリングまで待たされる。失敗扱いにはならないので、条件が
# 解消すれば自然に再開する。


def youtube_upload_gate(content_type: str) -> tuple[bool, str]:
    """YouTube アップロードを今すぐ実行してよいか。

    2 つのブレーキをかける:
    1. 1日あたりのアップロード本数 (API クォータ: videos.insert = 1600 ユニット、
       1日 10000 ユニットなので実質 6 本が限界)
    2. 予約投稿の先詰まり。指定日数より先まで公開枠が埋まっていたら止める。
       これで「常に N 日分の在庫を持って自動停止する」状態になり、未処理の
       ストーリーが大量にあっても一気に投稿し尽くさない。
    """
    from datetime import datetime, timedelta, timezone

    from app.config import get as cfg_get

    limit = int(cfg_get("youtube_daily_upload_limit") or 0)
    if limit > 0:
        since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        # ステージ完了ではなく「実際に送信した本数」で数える。既に投稿済みの
        # 作品はスキップされて即座にステージだけ進むので、完了数で数えると
        # 1本も上がっていないのに上限に達してしまう。
        done = db.count_uploads_since(since)
        if done >= limit:
            return False, f"24時間のアップロード上限に到達 ({done}/{limit})"

    max_ahead = int(cfg_get("youtube_schedule_max_ahead_days") or 0)
    if max_ahead > 0 and cfg_get("youtube_schedule_enabled"):
        latest = db.get_latest_scheduled_publish_at()
        if latest:
            try:
                parsed = datetime.fromisoformat(latest)
            except ValueError:
                parsed = None
            if parsed is not None:
                now = datetime.now(parsed.tzinfo) if parsed.tzinfo else datetime.now()
                if parsed - now > timedelta(days=max_ahead):
                    return False, (
                        f"予約投稿が {max_ahead} 日先まで埋まっている "
                        f"(最終: {latest[:16]})"
                    )

    return True, ""


def api_cost_gate(content_type: str) -> tuple[bool, str]:
    """24時間の API 費用が上限に達していないか。

    見積もりではなく `api_usage` に積み上げた実測で判定する。想定を外しても
    上限額で必ず止まるので、クレジットを一気に溶かすことがない。
    """
    from datetime import datetime, timedelta, timezone

    from app.config import get as cfg_get

    limit = float(cfg_get("api_daily_cost_limit_usd") or 0)
    if limit <= 0:
        return True, ""

    since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    spent = db.get_api_cost_since(since)
    if spent >= limit:
        return False, f"24時間のAPI費用が上限に到達 (${spent:.2f} / ${limit:.2f})"
    return True, ""


def generation_gate(content_type: str) -> tuple[bool, str]:
    """生成系ステージの実行可否。在庫とコストの両方を見る。"""
    allowed, reason = api_cost_gate(content_type)
    if not allowed:
        return allowed, reason
    return video_inventory_gate(content_type)


def video_inventory_gate(content_type: str) -> tuple[bool, str]:
    """音声・画像・動画の生成を続けてよいか (在庫ブレーキ)。

    アップロード側だけ絞っても上流が回り続けると、未処理ストーリーを片っ端から
    動画化してディスクと画像 API を消費してしまう。「未投稿の完成動画」が
    一定本数に達したら生成を止め、投稿されて在庫が減ったら自動で再開する。
    """
    from app.config import get as cfg_get

    limit = int(cfg_get("autopilot_max_ready_videos") or 0)
    if limit <= 0:
        return True, ""

    ready = db.get_stage_counts(content_type=content_type).get("video_complete", 0)
    if ready >= limit:
        return False, f"未投稿の完成動画が {ready} 本 (上限 {limit})"
    return True, ""


# stage -> ゲート関数。未登録のステージは常に実行可。
# スクレイピングとテキスト処理は安いので素通し、金と容量を食う工程だけ絞る。
STAGE_GATES: dict[str, Callable[[str], tuple[bool, str]]] = {
    # テキスト処理も課金するのでコスト上限の対象にする (在庫は関係ない)
    "text_processed": api_cost_gate,
    "voice_generated": generation_gate,
    "images_generated": generation_gate,
    "video_complete": generation_gate,
    "youtube_uploaded": youtube_upload_gate,
}

# ステージ完了後に待つ秒数。外部サービスへの連投を避けるため。
STAGE_POST_DELAY: dict[str, float] = {
    "scraped": 2.0,
    "report_submitted": 5.0,
}
