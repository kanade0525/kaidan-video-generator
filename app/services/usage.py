"""API 利用量の計測とコスト概算。

課金が事後にしか分からず、gemini-2.5-pro の**思考トークン**を見落として
5,000 円のクレジットを使い切ったことがある。呼び出しごとに実測値を記録し、
上限を超えたら止められるようにする。

料金は 2026-09 時点の standard ティア (https://ai.google.dev/gemini-api/docs/pricing)。
モデルが増えたり改定されたら `MODEL_PRICING` を更新すること。ここに無いモデルは
コスト 0 として記録され、トークン数だけ残る (金額を勝手に推測しない)。
"""

from __future__ import annotations

from dataclasses import dataclass

from app import database as db
from app.utils.log import get_logger

log = get_logger("kaidan.usage")


@dataclass(frozen=True)
class ModelPrice:
    """1M トークンあたりの USD 単価。画像モデルは1枚あたりの固定額。

    `output_per_1m` は**思考トークンを含む**。gemini-2.5-pro のような思考
    モデルでは、見えている応答文よりも思考のほうが桁違いに多いことがある。
    """
    input_per_1m: float = 0.0
    output_per_1m: float = 0.0
    per_image: float = 0.0
    thinking: bool = False


MODEL_PRICING: dict[str, ModelPrice] = {
    # テキスト
    "gemini-2.5-pro": ModelPrice(input_per_1m=1.25, output_per_1m=10.0, thinking=True),
    "gemini-2.5-flash": ModelPrice(input_per_1m=0.30, output_per_1m=2.50, thinking=True),
    "gemini-2.5-flash-lite": ModelPrice(input_per_1m=0.10, output_per_1m=0.40),
    # 画像 (トークンではなく枚数課金)
    "gemini-3-pro-image": ModelPrice(per_image=0.134),
    "gemini-3.1-flash-image": ModelPrice(per_image=0.067),
    "gemini-3.1-flash-lite-image": ModelPrice(per_image=0.0336),
    "gemini-2.5-flash-image": ModelPrice(per_image=0.039),
}


def price_for(model: str) -> ModelPrice | None:
    """モデルの単価。前方一致も見る (日付サフィックス付きの ID に対応)。"""
    if model in MODEL_PRICING:
        return MODEL_PRICING[model]
    for name, price in MODEL_PRICING.items():
        if model.startswith(name):
            return price
    return None


def is_thinking_model(model: str) -> bool:
    """思考トークンが出力として課金されるモデルか。

    見積もりを出すときは、このモデルなら出力を保守的に多めに見ること。
    """
    price = price_for(model)
    return bool(price and price.thinking)


def estimate_cost(
    model: str, input_tokens: int = 0, output_tokens: int = 0, images: int = 0,
) -> float:
    """概算費用 (USD)。単価が未登録のモデルは 0 を返す (推測しない)。"""
    price = price_for(model)
    if price is None:
        log.warning("単価が未登録のモデル: %s (コストは0として記録)", model)
        return 0.0
    return (
        input_tokens / 1_000_000 * price.input_per_1m
        + output_tokens / 1_000_000 * price.output_per_1m
        + images * price.per_image
    )


def _int(value) -> int:
    return int(value) if isinstance(value, (int, float)) else 0


def record_response(
    response, model: str, purpose: str, story_id: int | None = None,
    images: int = 0,
) -> float:
    """Gemini のレスポンスから実測トークンを取り出して記録する。

    `usage_metadata` が無い/壊れていても、記録の失敗で本処理を止めない。
    戻り値は概算費用 (USD)。
    """
    input_tokens = output_tokens = thinking_tokens = 0
    try:
        usage = getattr(response, "usage_metadata", None)
        if usage is not None:
            input_tokens = _int(getattr(usage, "prompt_token_count", 0))
            output_tokens = _int(getattr(usage, "candidates_token_count", 0))
            # 思考トークンは candidates とは別に出る。課金は出力扱いなので合算する。
            thinking_tokens = _int(getattr(usage, "thoughts_token_count", 0))
            output_tokens += thinking_tokens
    except Exception as e:  # noqa: BLE001 — 計測の失敗で生成を止めない
        log.warning("利用量の読み取りに失敗 (%s): %s", model, e)

    cost = estimate_cost(model, input_tokens, output_tokens, images)
    try:
        db.record_api_usage(
            model=model, purpose=purpose, story_id=story_id,
            input_tokens=input_tokens, output_tokens=output_tokens,
            thinking_tokens=thinking_tokens, images=images, cost_usd=cost,
        )
    except Exception as e:  # noqa: BLE001 — DB 障害で生成を止めない
        log.warning("利用量の記録に失敗: %s", e)

    detail = f"in={input_tokens} out={output_tokens}"
    if thinking_tokens:
        detail += f" (うち思考 {thinking_tokens})"
    if images:
        detail += f" 画像={images}枚"
    log.info("[usage] %s / %s: %s → $%.4f", model, purpose, detail, cost)
    return cost


def record_image(model: str, purpose: str, story_id: int | None = None) -> float:
    """画像1枚の生成を記録する (枚数課金なのでトークンは見ない)。"""
    cost = estimate_cost(model, images=1)
    try:
        db.record_api_usage(
            model=model, purpose=purpose, story_id=story_id, images=1, cost_usd=cost,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("利用量の記録に失敗: %s", e)
    log.info("[usage] %s / %s: 画像1枚 → $%.4f", model, purpose, cost)
    return cost
