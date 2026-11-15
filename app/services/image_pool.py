"""Curated image pools for Shorts (本編シーン用).

本編シーンを毎回 AI 生成すると当たり外れが大きく、課金も嵩む。良い画像を
`assets/shorts/scene/` に置いておけば、以降はそこから決定的に選ぶだけで済み、
生成待ちも API 課金も発生しない。

選択はプールからのランダム。作品数がプール枚数を超えても、同じ画像が規則的に
並ぶことがない。
"""

from __future__ import annotations

import random
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps

from app.utils.log import get_logger

log = get_logger("kaidan.image_pool")

POOL_ROOT = Path("assets/shorts")
# 1枚目 (サムネイル) はタイトル文字を絵に焼き込むため作品ごとに固有で、
# 使い回せない。プールの対象は本編シーンだけ。
SCENE = "scene"

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def pool_dir(kind: str) -> Path:
    """プールのディレクトリ (無ければ作る)。kind は "scene"。"""
    d = POOL_ROOT / kind
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_pool(kind: str) -> list[Path]:
    """プール内の画像を名前順で返す。隠しファイルは除外。"""
    d = POOL_ROOT / kind
    if not d.exists():
        return []
    return sorted(
        p for p in d.iterdir()
        if p.is_file()
        and p.suffix.lower() in _IMAGE_SUFFIXES
        and not p.name.startswith(".")
    )


def pick(kind: str, key: str, offset: int = 0) -> Path | None:
    """プールから 1 枚ランダムに選ぶ。プールが空なら None。

    以前はタイトルのハッシュで決定的に選んでいたが、作品数がプール枚数を
    大きく超えると同じ画像が規則的に繰り返される。ランダムなら偏りが
    散らばるので、そちらを採用している。

    `key` / `offset` は呼び出し側の互換のために残してあるだけで、選択には
    使わない (再実行のたびに違う画像になる)。
    """
    files = list_pool(kind)
    if not files:
        return None
    return random.choice(files)


def load_fitted(path: Path, width: int, height: int) -> bytes:
    """プール画像を目的の解像度にカバーフィットして PNG バイト列で返す。

    はみ出す分は中央基準で切り落とす。動画パイプラインは偶数サイズの固定解像度を
    前提にしているので、ここで必ず正規化しておく。
    """
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    img = ImageOps.fit(img, (width, height), method=Image.LANCZOS, centering=(0.5, 0.5))
    if img.mode != "RGB":
        img = img.convert("RGB")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def save_to_pool(kind: str, filename: str, data: bytes) -> Path:
    """アップロードされた画像をプールへ保存する。

    同名ファイルがあれば `-1`, `-2` … を付けて衝突を避ける。拡張子が画像で
    ない場合は `.png` に正規化する。
    """
    d = pool_dir(kind)
    stem = Path(filename).stem or "image"
    suffix = Path(filename).suffix.lower()
    if suffix not in _IMAGE_SUFFIXES:
        suffix = ".png"
    # ディレクトリトラバーサル対策: 名前部分だけを使う
    stem = Path(stem).name.replace("/", "_").replace("\\", "_")

    target = d / f"{stem}{suffix}"
    n = 1
    while target.exists():
        target = d / f"{stem}-{n}{suffix}"
        n += 1
    target.write_bytes(data)
    log.info("画像プールに追加: %s", target)
    return target


def delete_from_pool(kind: str, name: str) -> bool:
    """プールから 1 枚削除する。プール外のパスは拒否する。"""
    d = pool_dir(kind).resolve()
    target = (d / Path(name).name).resolve()
    if target.parent != d or not target.is_file():
        log.warning("プール外のパスは削除しない: %s", target)
        return False
    target.unlink()
    log.info("画像プールから削除: %s", target)
    return True
