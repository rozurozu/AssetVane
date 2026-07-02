"""calc_receivables_inventory（JP #2 売掛/在庫の質）ジョブの担保（ADR-064・ADR-018）。

未設定なら静かに skip（ok=True）・設定済みなら edinetdb.jp の財務（fake 注入）から既存
valuation_snapshots 行へ #2 列を UPDATE する・cadence（fetch_meta）で再取得を抑える、を固定する。
ネットに出ず一時 SQLite で回す（testing-strategy）。
"""

from __future__ import annotations

from typing import Any

from app.batch.jobs import calc_receivables_inventory
from app.db import repo
from app.db.engine import get_engine


class _FakeAdapter:
    """resolve/get_financials/last_budget を返す fake（edinetdb.jp 非依存）。"""

    def __init__(self, fins: list[dict[str, Any]]) -> None:
        self._fins = fins
        self.last_budget: dict[str, int | None] = {"monthly_remaining": 500}
        self.resolved: list[str] = []

    def resolve_edinet_code(self, sec_code: str) -> str | None:
        self.resolved.append(sec_code)
        return "E99999"

    def get_financials(self, edinet_code: str) -> list[dict[str, Any]]:
        return self._fins


def _seed_stock_with_snapshot(code: str) -> None:
    repo.upsert_stocks([{"code": code, "company_name": "テスト", "updated_at": "2026-06-30"}])
    repo.upsert_valuation_snapshots(
        [{"code": code, "as_of_date": "2026-06-27", "close": 1000.0, "updated_at": "2026-06-30"}]
    )


def test_skip_when_unconfigured(temp_db) -> None:
    """未登録なら ok=True で静かに skip（公式 EDINET の必須扱いと違う・ADR-064）。"""
    result = calc_receivables_inventory.run()
    assert result.ok is True
    assert result.rows == 0
    assert "skip" in result.detail


def test_updates_snapshot_for_watchlist_code(temp_db, monkeypatch) -> None:
    """設定済み＋watchlist 銘柄→ #2 列が valuation_snapshots へ UPDATE（edinet_code も焼く）。"""
    with get_engine().begin() as conn:
        repo.upsert_edinetdb_config(conn, {"api_key": "edb_test", "plan": "free"})
    _seed_stock_with_snapshot("72030")
    repo.add_watchlist("72030", None, 21)

    fins = [
        {
            "fiscal_year": 2024,
            "disclosed_date": "2024-06-18",
            "receivables": 100.0,
            "inventory": 200.0,
            "revenue": 1000.0,
            "gross_profit": 300.0,
            "cost_of_sales": None,
        },
        {
            "fiscal_year": 2025,
            "disclosed_date": "2025-06-18",
            "receivables": 150.0,
            "inventory": 260.0,
            "revenue": 1100.0,
            "gross_profit": 330.0,
            "cost_of_sales": None,
        },
    ]
    fake = _FakeAdapter(fins)
    monkeypatch.setattr(calc_receivables_inventory, "build_edinetdb_adapter", lambda conn: fake)

    result = calc_receivables_inventory.run()
    assert result.ok is True
    assert result.rows == 1

    with get_engine().connect() as conn:
        snap = repo.get_valuation_snapshot(conn, "72030")
        stock = repo.get_stock(conn, "72030")
    assert snap is not None
    assert stock is not None
    assert snap["receivables_growth_yoy"] is not None
    assert abs(snap["receivables_growth_yoy"] - 0.5) < 1e-9
    assert snap["inventory_turnover_days"] is not None
    assert stock["edinet_code"] == "E99999"  # 解決した edinet_code がキャッシュされた


def test_cadence_skips_recently_fetched(temp_db, monkeypatch) -> None:
    """直近に取得済み（fetch_meta）の銘柄は cadence で skip され API を叩かない。"""
    with get_engine().begin() as conn:
        repo.upsert_edinetdb_config(conn, {"api_key": "edb_test", "plan": "free"})
    _seed_stock_with_snapshot("72030")
    repo.add_watchlist("72030", None, 21)
    # 今日取得済みとして記録（interval_days=7 既定の内側）
    from datetime import date

    repo.upsert_fetch_meta("edinetdb_quality:72030", date.today().isoformat())

    fake = _FakeAdapter([])
    monkeypatch.setattr(calc_receivables_inventory, "build_edinetdb_adapter", lambda conn: fake)

    result = calc_receivables_inventory.run()
    assert result.ok is True
    assert result.rows == 0
    assert fake.resolved == []  # cadence skip ＝ resolve も呼ばれない
