"""YouTube に上げ終えた Short を保管先 (Google Drive など) へ移し、ローカルを空ける。

完成動画 (mp4) を保管先へコピーし、内容が一致したのを確かめてからローカルの
mp4 と音声・画像を消す。台本などのテキストは残すので、結果画面の表示や
読みの見直しはそのままできる。

保管先はコンテナ内のパスを環境変数 ``SHORTS_ARCHIVE_DIR`` で受け取る
(docker-compose.yml がホストの ``SHORTS_ARCHIVE_HOST_DIR`` をマウントする)。
未設定ならこの機能は何もしない。

取り消せない削除を伴うので、次の順序を守る:

1. 保管先へ一時ファイル名でコピー
2. サイズと SHA-256 が元と一致することを確認
3. 正式な名前に付け替え
4. ここまで成功して初めてローカルを削除

途中で失敗した場合はローカルに一切手を付けない。アップロード自体は成功して
いるので、ここでの失敗をステージの失敗にしない (再アップロードで YouTube に
重複を作らないため)。次回起動時の掃き出しでやり直す。
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

import app.utils.paths as paths
from app import database as db
from app.models import Story
from app.utils.log import get_logger

log = get_logger("kaidan.archive")

ARCHIVE_DIR_ENV = "SHORTS_ARCHIVE_DIR"

# 「YouTube に上げ終えた」とみなすステージ。使用報告の実行中・完了も含む。
UPLOADED_STAGES = ("youtube_uploaded", "report_submitted:running", "report_submitted")

# 動画を保管先へ移したあとに消す生成物 (ストーリーディレクトリ直下)。
# 再生成できる音声・画像だけ。custom_thumbnail.* はユーザーが置いた物なので残す。
MEDIA_FILES = ("narration_complete.wav", "title_narration.wav", "scroll_subtitle.png")
MEDIA_DIRS = ("audio", "images")

# archive_story の結果
ARCHIVED = "archived"            # 保管先へ移してローカルを消した
ALREADY = "already"              # 既に保管先にあり、ローカルに消す物もない
DISABLED = "disabled"            # 保管先が未設定
NOT_UPLOADED = "not_uploaded"    # まだ YouTube に上がっていない / Short ではない
NO_VIDEO = "no_video"            # ローカルにも保管先にも動画がない
NOT_READY = "not_ready"          # 保管先が使えない (未マウントなど)
CONFLICT = "conflict"            # 保管先に同名で中身の違うファイルがある
FAILED = "failed"                # コピー・検証に失敗 (ローカルは無傷)


def archive_dir() -> Path | None:
    """保管先ディレクトリ。未設定なら None (機能オフ)。"""
    raw = os.environ.get(ARCHIVE_DIR_ENV, "").strip()
    return Path(raw) if raw else None


def is_uploaded_short(story: Story) -> bool:
    return (
        story.content_type == "short"
        and bool(story.youtube_video_id)
        and story.stage in UPLOADED_STAGES
    )


def _short_dir(title: str) -> Path:
    """Short の出力ディレクトリ。paths.story_dir と違い、無ければ作らない。"""
    return paths.OUTPUT_BASE / "shorts" / paths.safe_dirname(title)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _same_content(a: Path, b: Path) -> bool:
    return a.stat().st_size == b.stat().st_size and _sha256(a) == _sha256(b)


def _destination_problem(dest_dir: Path) -> str:
    """保管先が使えない理由。使えるなら空文字。"""
    if not dest_dir.is_dir():
        return f"保管先がありません: {dest_dir}"
    # Drive が未マウントのまま起動すると、マウント元が空のフォルダとして
    # 作られることがある。そこへ移すと「Drive に上がったつもりでローカルを
    # 消す」ことになるので、空の保管先には移さない。
    if not any(dest_dir.iterdir()):
        return f"保管先が空です (Drive が未マウントの可能性): {dest_dir}"
    return ""


def _remove_media(sdir: Path) -> list[str]:
    """ストーリーディレクトリ内の音声・画像を消す。消した名前を返す。"""
    removed: list[str] = []
    for name in MEDIA_FILES:
        p = sdir / name
        if p.is_file():
            p.unlink()
            removed.append(name)
    for name in MEDIA_DIRS:
        p = sdir / name
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p)
            removed.append(f"{name}/")
    return removed


def archive_story(story: Story) -> str:
    """アップ済み Short の動画を保管先へ移し、ローカルの動画・音声・画像を消す。

    例外は投げない。結果は ARCHIVED などの定数で返す。
    """
    dest_dir = archive_dir()
    if dest_dir is None:
        return DISABLED
    if not is_uploaded_short(story):
        return NOT_UPLOADED

    sdir = _short_dir(story.title)
    src = sdir / f"{paths.safe_dirname(story.title)}.mp4"
    tmp: Path | None = None
    try:
        problem = _destination_problem(dest_dir)
        if problem:
            log.warning("[archive] %s — スキップ: %s", problem, story.title)
            return NOT_READY

        dest = dest_dir / src.name

        if not src.is_file():
            if not dest.is_file():
                return NO_VIDEO
            # 動画は既に保管先にある (手で移した / 前回の途中で止まった)。
            # 残っている音声・画像だけ片付ける。
            removed = _remove_media(sdir) if sdir.is_dir() else []
            if not removed:
                return ALREADY
            log.info("[archive] 残っていた生成物を削除: %s (%s)",
                     story.title, ", ".join(removed))
            return ARCHIVED

        if dest.exists():
            if not _same_content(src, dest):
                log.warning(
                    "[archive] 保管先に同名で中身の違う動画があります。上書きせず"
                    "ローカルを残します: %s", dest.name,
                )
                return CONFLICT
        else:
            tmp = dest_dir / f".{src.name}.part"
            shutil.copyfile(src, tmp)
            if not _same_content(src, tmp):
                tmp.unlink(missing_ok=True)
                log.error("[archive] コピー後の内容が一致しません。ローカルを残します: %s",
                          story.title)
                return FAILED
            os.replace(tmp, dest)
            tmp = None

        size_mb = src.stat().st_size / 1_000_000
        src.unlink()
        removed = _remove_media(sdir)
        log.info("[archive] 保管先へ移動: %s (%.1fMB) / 削除: %s",
                 dest.name, size_mb, ", ".join(removed) or "なし")
        db.add_log("INFO", f"保管先へ移動: {dest.name} ({size_mb:.1f}MB)",
                   "youtube_uploaded", story.id)
        return ARCHIVED
    except Exception as e:
        log.warning("[archive] 移動に失敗 (ローカルは残っています): %s - %s", story.title, e)
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        return FAILED


def archive_story_by_id(story_id: int) -> str:
    """ステージ完了直後に呼ぶ用。DB から最新の状態を読み直して移す。"""
    try:
        story = db.get_story_by_id(story_id)
    except Exception as e:
        log.warning("[archive] ストーリー取得に失敗: id=%s - %s", story_id, e)
        return FAILED
    if story is None:
        return NOT_UPLOADED
    return archive_story(story)


def archive_uploaded_shorts() -> dict[str, int]:
    """アップ済みでローカルに残っている Short をまとめて移す (起動時の掃き出し)。

    保管先が一時的に使えず移せなかった分を、次の起動で拾い直す。
    """
    counts: dict[str, int] = {}
    if archive_dir() is None:
        return counts
    try:
        stories: list[Story] = []
        for stage in UPLOADED_STAGES:
            stories.extend(db.get_stories(stage=stage, content_type="short", limit=100_000))
    except Exception as e:
        log.warning("[archive] アップ済み Short の取得に失敗: %s", e)
        return counts

    for story in stories:
        result = archive_story(story)
        counts[result] = counts.get(result, 0) + 1
    moved = counts.get(ARCHIVED, 0)
    held = counts.get(CONFLICT, 0) + counts.get(FAILED, 0) + counts.get(NOT_READY, 0)
    if moved or held:
        log.info("[archive] 掃き出し完了: %s", counts)
    return counts
