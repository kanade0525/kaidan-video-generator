"""パイプラインのステージを巻き戻して作り直す。

「画像がイマイチだから作り直したい」「読みを直したので音声から作り直したい」
といったときに、ストーリーのステージを前に戻してワーカーに再処理させる。

UI では「どの工程からやり直すか」を選んでもらい、内部ではその1つ手前の
ステージに戻す。`stage` は「そこまで完了した」を意味するので、画像生成を
やり直したければ `voice_generated` まで戻す、という関係になる。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app import database as db
from app.models import STAGE_LABELS, Story, prev_stage, stages_for
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

log = get_logger("kaidan.rewind")

# 生成物ではなくユーザーが用意した設定・素材。やり直しでも消さない。
#   custom_thumbnail.*  : 手動アップロードしたサムネイル
#   slideshow.json      : UI で編集した表示時間・並び順
_USER_OWNED_NAMES = ("custom_thumbnail", "slideshow.json")


@dataclass
class RewindPlan:
    """やり直しの内容。実行前に UI へ見せて確認を取るためのもの。"""

    redo_from: str                       # やり直す最初のステージ
    new_stage: str                       # 巻き戻し後の stage 値
    redo_stages: list[str] = field(default_factory=list)
    artifacts: list[Path] = field(default_factory=list)
    clears_youtube: bool = False

    @property
    def redo_labels(self) -> list[str]:
        return [STAGE_LABELS.get(s, s) for s in self.redo_stages]


def redoable_stages(content_type: str) -> list[str]:
    """やり直しの起点として選べるステージ (pending 以外の全て)。"""
    return stages_for(content_type)[1:]


def stage_artifacts(story: Story, stage: str) -> list[Path]:
    """そのステージが生成したファイル。存在するものだけを返す。

    ユーザーが用意したファイル (手動サムネイル / スライドショー設定) は
    生成物ではないので対象外。
    """
    ct = story.content_type
    sdir = story_dir(story.title, ct)
    candidates: list[Path] = []

    if stage == "scraped":
        candidates = [raw_content_path(story.title, ct), meta_path(story.title, ct)]
    elif stage == "text_processed":
        candidates = [
            processed_text_path(story.title, ct),
            chunks_path(story.title, ct),
            original_chunks_path(story.title, ct),
            narration_source_path(story.title, ct),
            source_chunks_path(story.title, ct),
        ]
    elif stage == "voice_generated":
        candidates = [
            *audio_dir(story.title, ct).glob("*.wav"),
            narration_path(story.title, ct),
            sdir / "title_narration.wav",
        ]
    elif stage == "images_generated":
        candidates = list(images_dir(story.title, ct).glob("*.png"))
    elif stage == "video_complete":
        candidates = [
            video_path(story.title, ct),
            timestamps_path(story.title, ct),
            *sdir.glob("scroll_subtitle*.txt"),
            # 途中生成物。通常はステージ内で消えるが、失敗時に残ることがある
            sdir / "raw_long.mp4",
            sdir / "raw_short.mp4",
            sdir / "subtitled_long.mp4",
            sdir / "final_with_endscreen.mp4",
            sdir / "endscreen_black.mp4",
        ]
    # youtube_uploaded / report_submitted はファイルを作らない (DB のみ)

    return [p for p in candidates if p.is_file() and _is_deletable(p, sdir)]


def _is_deletable(path: Path, story_root: Path) -> bool:
    """ストーリーの出力ディレクトリ内の、生成物だけを削除対象にする。"""
    try:
        resolved = path.resolve()
        resolved.relative_to(story_root.resolve())
    except (ValueError, OSError):
        log.warning("ストーリーディレクトリ外のパスは削除しない: %s", path)
        return False
    return not any(resolved.name.startswith(n) for n in _USER_OWNED_NAMES)


def plan(story: Story, redo_from: str, delete_artifacts: bool = False,
         clear_youtube: bool = False) -> RewindPlan:
    """やり直しの計画を組み立てる (この時点では何も変更しない)。"""
    stages = stages_for(story.content_type)
    if redo_from not in stages or redo_from == "pending":
        raise ValueError(f"やり直せないステージです: {redo_from}")

    idx = stages.index(redo_from)
    redo_stages = stages[idx:]
    new_stage = prev_stage(redo_from, story.content_type) or "pending"

    artifacts: list[Path] = []
    if delete_artifacts:
        for stage in redo_stages:
            artifacts.extend(stage_artifacts(story, stage))

    return RewindPlan(
        redo_from=redo_from,
        new_stage=new_stage,
        redo_stages=redo_stages,
        artifacts=artifacts,
        clears_youtube=clear_youtube and "youtube_uploaded" in redo_stages,
    )


def apply(story: Story, plan: RewindPlan) -> int:
    """計画を実行する。削除したファイル数を返す。

    ファイル削除 → DB 更新 の順。逆にすると、削除に失敗したときに
    「ステージは戻っているのに古いファイルが残っている」状態になる。
    """
    deleted = 0
    for path in plan.artifacts:
        try:
            path.unlink()
            deleted += 1
        except OSError as e:  # noqa: PERF203 — 1件ずつ失敗を許容する
            log.warning("ファイル削除に失敗: %s (%s)", path, e)

    if plan.clears_youtube:
        db.set_youtube_video_id(story.id, None)
        db.set_scheduled_publish_at(story.id, None)

    db.reset_to_stage(story.id, plan.new_stage, content_type=story.content_type)
    db.add_log(
        "INFO",
        f"ステージを巻き戻し: {plan.redo_from} からやり直し (削除 {deleted} ファイル)",
        plan.new_stage,
        story.id,
    )
    log.info(
        "[rewind] %s: %s → %s からやり直し (削除 %d ファイル, YouTube解除=%s)",
        story.title, plan.new_stage, plan.redo_from, deleted, plan.clears_youtube,
    )
    return deleted
