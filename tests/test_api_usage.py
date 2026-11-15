"""API 利用量の計測とコスト上限のテスト。

実障害: gemini-2.5-pro の思考トークンを見積もりに入れ忘れ、447件の校正で
5,000円のクレジットを一晩で使い切った。使用量は請求画面でしか分からず、
走っている最中は誰も気づけなかった。
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import app.config as config_module
import app.database as db
from app.services import usage


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "u.db")
    monkeypatch.setattr(db._local, "conn", None, raising=False)
    db.init_db()


@pytest.fixture
def user_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "CONFIG_PATH", tmp_path / "config.toml")

    def _set(**values):
        config_module.save_config(values)

    return _set


class TestPricing:
    def test_thinking_models_are_flagged(self):
        """思考モデルは出力トークンが跳ねるので、見積もり時に区別が要る。"""
        assert usage.is_thinking_model("gemini-2.5-pro") is True
        assert usage.is_thinking_model("gemini-2.5-flash") is True
        assert usage.is_thinking_model("gemini-3.1-flash-image") is False

    def test_pro_costs_far_more_than_flash(self):
        """今回の出費の本質。同じ仕事で pro は flash の数倍になる。"""
        args = dict(input_tokens=4_000, output_tokens=11_000)
        pro = usage.estimate_cost("gemini-2.5-pro", **args)
        flash = usage.estimate_cost("gemini-2.5-flash", **args)
        assert pro > flash * 3

    def test_image_models_are_priced_per_image(self):
        assert usage.estimate_cost("gemini-3.1-flash-image", images=1) == pytest.approx(0.067)
        assert usage.estimate_cost("gemini-3-pro-image", images=2) == pytest.approx(0.268)

    def test_unknown_model_costs_zero_not_a_guess(self):
        """単価が分からないモデルの金額を勝手に推測しない。"""
        assert usage.estimate_cost("some-future-model", input_tokens=10**6) == 0.0

    def test_versioned_model_id_matches_by_prefix(self):
        assert usage.price_for("gemini-2.5-flash-002") is not None


class TestRecordResponse:
    def _response(self, prompt=1000, candidates=500, thoughts=0):
        return SimpleNamespace(usage_metadata=SimpleNamespace(
            prompt_token_count=prompt,
            candidates_token_count=candidates,
            thoughts_token_count=thoughts,
        ))

    def test_thinking_tokens_are_billed_as_output(self, fresh_db):
        """思考トークンを出力に合算しないと、見積もりが今回と同じ轍を踏む。"""
        usage.record_response(
            self._response(candidates=500, thoughts=9_500),
            "gemini-2.5-pro", "ai_proofread",
        )
        row = db.get_api_usage_summary("2000-01-01")[0]
        assert row["thinking_tokens"] == 9_500
        assert row["output_tokens"] == 10_000

    def test_cost_reflects_thinking(self, fresh_db):
        cheap = usage.estimate_cost("gemini-2.5-pro", output_tokens=500)
        real = usage.record_response(
            self._response(candidates=500, thoughts=9_500),
            "gemini-2.5-pro", "ai_proofread",
        )
        assert real > cheap * 10

    def test_missing_metadata_does_not_raise(self, fresh_db):
        assert usage.record_response(SimpleNamespace(), "gemini-2.5-pro", "x") == 0.0

    def test_db_failure_does_not_raise(self, monkeypatch):
        """計測の失敗で生成そのものを止めない。"""
        monkeypatch.setattr(
            db, "record_api_usage",
            lambda **kw: (_ for _ in ()).throw(RuntimeError("db down")),
        )
        usage.record_response(self._response(), "gemini-2.5-pro", "x")

    def test_record_image_counts_one(self, fresh_db):
        cost = usage.record_image("gemini-3.1-flash-image", "thumbnail")
        assert cost == pytest.approx(0.067)
        assert db.get_api_usage_summary("2000-01-01")[0]["images"] == 1


class TestCostQueries:
    def test_sums_only_within_window(self, fresh_db):
        usage.record_image("gemini-3.1-flash-image", "thumbnail")
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        assert db.get_api_cost_since(past) == pytest.approx(0.067)
        assert db.get_api_cost_since(future) == 0.0

    def test_summary_groups_by_model_and_purpose(self, fresh_db):
        usage.record_image("gemini-3.1-flash-image", "thumbnail")
        usage.record_image("gemini-3.1-flash-image", "thumbnail")
        usage.record_image("gemini-3-pro-image", "thumbnail")
        rows = db.get_api_usage_summary("2000-01-01")
        assert len(rows) == 2
        assert rows[0]["cost_usd"] >= rows[1]["cost_usd"]   # 高い順

    def test_recent_usage_is_listed(self, fresh_db):
        usage.record_image("gemini-3.1-flash-image", "thumbnail")
        assert db.get_recent_api_usage(10)[0]["model"] == "gemini-3.1-flash-image"


class TestCostGate:
    def _gate(self, spent, user_config, monkeypatch, limit=3.0):
        from app.pipeline.stages import api_cost_gate

        user_config(api_daily_cost_limit_usd=limit)
        monkeypatch.setattr("app.database.get_api_cost_since", lambda since: spent)
        return api_cost_gate("short")

    def test_allows_under_limit(self, user_config, monkeypatch):
        assert self._gate(1.0, user_config, monkeypatch)[0] is True

    def test_blocks_at_limit(self, user_config, monkeypatch):
        allowed, reason = self._gate(3.0, user_config, monkeypatch)
        assert allowed is False
        assert "API費用が上限" in reason

    def test_zero_disables_the_limit(self, user_config, monkeypatch):
        assert self._gate(999.0, user_config, monkeypatch, limit=0)[0] is True

    def test_text_stage_is_gated_too(self):
        """テキスト処理も課金する。今回の出費はここから出た。"""
        from app.pipeline.stages import STAGE_GATES, api_cost_gate

        assert STAGE_GATES["text_processed"] is api_cost_gate

    def test_generation_gate_checks_cost_before_inventory(self, user_config, monkeypatch):
        from app.pipeline.stages import generation_gate

        user_config(api_daily_cost_limit_usd=1.0, autopilot_max_ready_videos=100)
        monkeypatch.setattr("app.database.get_api_cost_since", lambda since: 5.0)
        allowed, reason = generation_gate("short")
        assert allowed is False
        assert "API費用" in reason
