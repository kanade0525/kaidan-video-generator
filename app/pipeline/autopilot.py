"""オートパイロット: 起動時にワーカーを自動で走らせる。

有効にしておくと、アプリ (コンテナ) が動いている限り スクレイピング → テキスト
処理 → 音声 → 画像 → 動画 → YouTube 投稿 → 使用報告 まで人手なしで流れる。

暴走しないためのブレーキは `stages.STAGE_GATES` 側にある:
  - 1日あたりのアップロード本数上限 (API クォータ対策)
  - 予約投稿が N 日先まで埋まったら停止
なので「在庫が尽きるまで投稿し続ける」のではなく「常に N 日分の予約を保った
まま待機する」挙動になる。
"""

from __future__ import annotations

from app.config import load_config, save_config
from app.pipeline.executor import pipeline, shorts_pipeline
from app.utils.log import get_logger

log = get_logger("kaidan.autopilot")

_CONFIG_KEYS = {
    "long": "autopilot_long_enabled",
    "short": "autopilot_short_enabled",
}


def _pipeline_for(content_type: str):
    return shorts_pipeline if content_type == "short" else pipeline


def is_enabled(content_type: str) -> bool:
    """設定上オートパイロットが有効か。"""
    key = _CONFIG_KEYS.get(content_type)
    if key is None:
        return False
    return bool(load_config().get(key, False))


def is_running(content_type: str) -> bool:
    """実際にワーカーが1つでも動いているか。"""
    status = _pipeline_for(content_type).get_status()
    return any(info["running"] for info in status.values())


def set_enabled(content_type: str, enabled: bool) -> None:
    """オートパイロットを切り替え、設定に永続化してワーカーを起動/停止する。"""
    key = _CONFIG_KEYS.get(content_type)
    if key is None:
        raise ValueError(f"unknown content_type: {content_type}")

    config = load_config()
    config[key] = enabled
    save_config(config)

    pl = _pipeline_for(content_type)
    if enabled:
        pl.start_all()
        log.info("オートパイロット開始: %s", content_type)
    else:
        pl.stop_all()
        log.info("オートパイロット停止: %s", content_type)


def start_if_enabled() -> list[str]:
    """起動時に呼ぶ。有効になっているパイプラインを開始し、その一覧を返す。

    起動直後は前回の異常終了で `:running` のまま残ったストーリーがありうるので、
    先にそれらを入力ステージへ戻してから走らせる。戻さないと永久に拾われない。
    """
    started: list[str] = []
    for content_type in ("long", "short"):
        if not is_enabled(content_type):
            continue
        pl = _pipeline_for(content_type)
        recovered = pl.recover_stale()
        if recovered:
            log.info("スタックしていた %d 件を回復 (%s)", recovered, content_type)
        pl.start_all()
        started.append(content_type)
        log.info("オートパイロット自動開始: %s", content_type)
    return started
