"""fetch_financials ジョブの単体テスト（phase2-spec.md §8・ネット非依存）。

JQuantsAdapter.fetch_financials をスタブ化し、holdings に銘柄が入った状態で
fetch_financials.run が financials を UPSERT することを検証する。
実 API は叩かない。`temp_db` フィクスチャを使い本物 DB に触れない。
"""

from __future__ import annotations

from unittest.mock import patch

from sqlalchemy import func, select

from app.batch.jobs import fetch_financials
from app.db import repo
from app.db.engine import get_engine
from app.db.schema import financials as financials_table

# テスト用マスタデータ
_STOCK_A = {
    "code": "72030",
    "company_name": "トヨタ自動車",
    "sector33_code": "3700",
    "sector17_code": "6",
    "market_code": "0111",
    "is_etf": 0,
    "updated_at": "2026-06-02T00:00:00+00:00",
}

# テスト用財務データ（JQuantsAdapter.fetch_financials の戻り値形式）
_SAMPLE_FINANCIALS = [
    {
        "code": "72030",
        "disclosed_date": "2026-05-10",
        "fiscal_period": "FY2025",
        "net_sales": 45000000000.0,
        "operating_profit": 3500000000.0,
        "profit": 2800000000.0,
        "eps": 850.5,
        "bps": 7200.0,
    },
    {
        "code": "72030",
        "disclosed_date": "2025-11-15",
        "fiscal_period": "2Q2025",
        "net_sales": 22000000000.0,
        "operating_profit": 1800000000.0,
        "profit": 1400000000.0,
        "eps": 420.0,
        "bps": 6800.0,
    },
]


def _insert_holding(portfolio_id: int = 1, code: str = "72030") -> None:
    """holdings テーブルに保有行を直接挿入する（テスト用ヘルパ）。"""
    from app.db.schema import holdings, portfolios

    # portfolios seed が必要（schema.py の create_schema ではシードを入れないため手動挿入）
    with get_engine().begin() as conn:
        # portfolio が存在しなければ挿入
        from sqlalchemy import insert

        conn.execute(
            insert(portfolios).prefix_with("OR IGNORE"),
            [
                {
                    "portfolio_id": portfolio_id,
                    "name": "Default",
                    "created_at": "2026-06-03T00:00:00+00:00",
                }
            ],
        )
        conn.execute(
            insert(holdings).prefix_with("OR IGNORE"),
            [{"portfolio_id": portfolio_id, "code": code, "shares": 100.0, "avg_cost": 3000.0}],
        )


def test_fetch_financials_run_upserts_rows(temp_db) -> None:
    """fetch_financials.run が financials を UPSERT する。"""
    repo.upsert_stocks([_STOCK_A])
    _insert_holding()

    with patch("app.batch.jobs.fetch_financials.JQuantsAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.fetch_financials.return_value = _SAMPLE_FINANCIALS

        result = fetch_financials.run()

    assert result.ok is True
    assert result.rows == 2

    # financials に 2 行入っている
    with get_engine().connect() as conn:
        count = conn.execute(select(func.count()).select_from(financials_table)).scalar()
    assert count == 2


def test_fetch_financials_run_idempotent(temp_db) -> None:
    """fetch_financials.run を 2 回実行しても行数が増えない（UPSERT 冪等）。"""
    repo.upsert_stocks([_STOCK_A])
    _insert_holding()

    with patch("app.batch.jobs.fetch_financials.JQuantsAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.fetch_financials.return_value = _SAMPLE_FINANCIALS
        fetch_financials.run()

    with patch("app.batch.jobs.fetch_financials.JQuantsAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.fetch_financials.return_value = _SAMPLE_FINANCIALS
        result = fetch_financials.run()

    assert result.ok is True

    with get_engine().connect() as conn:
        count = conn.execute(select(func.count()).select_from(financials_table)).scalar()
    assert count == 2  # 重複しない


def test_fetch_financials_run_no_holdings(temp_db) -> None:
    """保有が 0 件の場合、0 行で ok を返す。"""
    # holdings に何も挿入しない

    with patch("app.batch.jobs.fetch_financials.JQuantsAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        result = fetch_financials.run()

    assert result.ok is True
    assert result.rows == 0
    # fetch_financials は呼ばれない
    instance.fetch_financials.assert_not_called()


def test_fetch_financials_run_advances_fetch_meta(temp_db) -> None:
    """fetch_financials.run 後に fetch_meta が today まで前進している。"""
    repo.upsert_stocks([_STOCK_A])
    _insert_holding()

    with patch("app.batch.jobs.fetch_financials.JQuantsAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.fetch_financials.return_value = _SAMPLE_FINANCIALS
        fetch_financials.run()

    from datetime import date

    today = date.today().isoformat()
    with get_engine().connect() as conn:
        meta = repo.get_fetch_meta(conn, "financials")
    assert meta is not None
    assert meta["last_fetched_date"] == today


def test_fetch_financials_run_adapter_error_returns_failure(temp_db) -> None:
    """JQuantsAdapter がエラーを投げた場合、ok=False の JobResult を返す。"""
    repo.upsert_stocks([_STOCK_A])
    _insert_holding()

    from app.adapters.jquants import JQuantsError

    with patch("app.batch.jobs.fetch_financials.JQuantsAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.fetch_financials.side_effect = JQuantsError("API 失敗")

        result = fetch_financials.run()

    assert result.ok is False
    assert "72030" in result.detail
