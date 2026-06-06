"""score_ai_alpha ジョブの検証 — 正常/冪等/未配置skip/読込失敗（Phase 5・ADR-018）。

担保（phase5-spec.md §4.4/§9）:
- モデルをスタブし、temp_db に仕込んだ財務/日足から特徴量→推論→signals(ai_alpha) を UPSERT。
- 2 回 run しても冪等（UNIQUE date,code,signal_type で重複しない）。
- モデル未配置は ok=True で静かに skip（通知しない＝ok=False にしない）。
- 読込失敗（ModelLoadError）は ok=False（runner が通知）。
"""

from __future__ import annotations

import numpy as np

from app.batch.jobs import score_ai_alpha
from app.db import repo
from app.db.engine import get_engine
from app.ml import model_store
from app.ml.model_store import ModelLoadError, ModelMeta
from app.quant.ml.features import FEATURE_NAMES

_META = ModelMeta(
    model_id="ai_alpha-test",
    trained_at="2026-06-15",
    feature_names=list(FEATURE_NAMES),
    lib_version="test",
    target="excess_return_60d",
    notes="",
)


class _StubModel:
    def predict(self, x):  # noqa: ANN001 — テスト用スタブ
        return np.arange(len(x), dtype=float)


def _seed_data() -> None:
    """2 銘柄分の stocks / daily_quotes / financials を temp_db に仕込む。"""
    repo.upsert_stocks(
        [
            {
                "code": c,
                "company_name": f"テスト{c}",
                "sector33_code": "0050",
                "sector17_code": "1",
                "market_code": "0111",
                "is_etf": 0,
                "updated_at": "2025-06-01T00:00:00",
            }
            for c in ("70030", "80040")
        ]
    )
    dates = ["2025-05-26", "2025-05-27", "2025-05-28", "2025-05-29", "2025-06-01"]
    quotes = []
    for c in ("70030", "80040"):
        for i, d in enumerate(dates):
            px = 1000.0 + i * 10
            quotes.append(
                {
                    "code": c,
                    "date": d,
                    "open": px,
                    "high": px,
                    "low": px,
                    "close": px,
                    "volume": 100000.0,
                    "adj_close": px,
                }
            )
    repo.upsert_daily_quotes(quotes)
    fins = []
    for c in ("70030", "80040"):
        fins += [
            {
                "code": c,
                "disclosed_date": "2024-05-10",
                "fiscal_period": "FY",
                "net_sales": 1000,
                "operating_profit": 100,
                "profit": 80,
                "eps": 50,
                "bps": 500,
                "dividend_per_share": None,
                "shares_outstanding": None,
                "treasury_shares": None,
            },
            {
                "code": c,
                "disclosed_date": "2025-05-10",
                "fiscal_period": "FY",
                "net_sales": 1200,
                "operating_profit": 150,
                "profit": 96,
                "eps": 60,
                "bps": 550,
                "dividend_per_share": None,
                "shares_outstanding": None,
                "treasury_shares": None,
            },
        ]
    repo.upsert_financials(fins)


def _count_ai_alpha() -> int:
    with get_engine().connect() as conn:
        return len(repo.get_signals(conn, None, "ai_alpha", limit=100))


def test_normal_scores_and_upserts(temp_db, monkeypatch) -> None:
    """正常配置 → ok=True・全銘柄を ai_alpha スコアリングして signals に焼く。"""
    _seed_data()
    monkeypatch.setattr(model_store, "is_configured", lambda *a, **k: True)
    monkeypatch.setattr(model_store, "load_active", lambda *a, **k: (_StubModel(), _META))

    result = score_ai_alpha.run()
    assert result.ok is True
    assert result.rows == 2
    assert _count_ai_alpha() == 2


def test_idempotent(temp_db, monkeypatch) -> None:
    """2 回 run しても signals(ai_alpha) は重複しない（UNIQUE 冪等）。"""
    _seed_data()
    monkeypatch.setattr(model_store, "is_configured", lambda *a, **k: True)
    monkeypatch.setattr(model_store, "load_active", lambda *a, **k: (_StubModel(), _META))

    score_ai_alpha.run()
    score_ai_alpha.run()
    assert _count_ai_alpha() == 2


def test_not_configured_skips_quietly(temp_db, monkeypatch) -> None:
    """モデル未配置は ok=True で静かに skip（通知しない＝失敗扱いにしない）。"""
    _seed_data()
    monkeypatch.setattr(model_store, "is_configured", lambda *a, **k: False)

    result = score_ai_alpha.run()
    assert result.ok is True
    assert result.rows == 0
    assert "未配置" in result.detail
    assert _count_ai_alpha() == 0


def test_broken_model_marks_failed(temp_db, monkeypatch) -> None:
    """配置済みだが読込失敗は ok=False（runner が Discord 通知）。"""
    _seed_data()
    monkeypatch.setattr(model_store, "is_configured", lambda *a, **k: True)

    def _raise(*a, **k):
        raise ModelLoadError("壊れた pkl")

    monkeypatch.setattr(model_store, "load_active", _raise)

    result = score_ai_alpha.run()
    assert result.ok is False
    assert "読込失敗" in result.detail
