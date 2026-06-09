"""sync_us_universe ジョブの単体テスト（一時 SQLite・fake adapter・Phase 7(B-1)・ADR-031/039）。

担保: fake UsEquityAdapter から得たユニバースを repo.upsert_us_stocks で焼き、list_us_stocks で
往復できること（JobResult.ok・冪等再実行で重複しない・partial update が財務素を消さない）。
実 HTTP には出ない（fetch_universe を fake で差し替え＝testing-strategy）。
"""

from __future__ import annotations

from typing import Any

from app.batch.jobs import sync_us_universe
from app.db import repo
from app.db.engine import get_engine


class _FakeAdapter:
    """fetch_universe だけ持つ fake（UsEquityAdapter の公開 API のうち job が使う口）。"""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def fetch_universe(self) -> list[dict[str, Any]]:
        return self._rows


def test_sync_us_universe_roundtrip(temp_db: None) -> None:
    """ユニバース同期 → list_us_stocks の往復（symbol/company_name/is_etf が焼ける）。"""
    fake = _FakeAdapter(
        [
            {"symbol": "AAPL", "company_name": "Apple Inc.", "is_etf": 0},
            {"symbol": "QQQ", "company_name": "Invesco QQQ Trust", "is_etf": 1},
        ]
    )
    result = sync_us_universe.run(adapter=fake)  # type: ignore[arg-type]
    assert result.ok is True
    assert result.rows == 2

    with get_engine().connect() as conn:
        rows = repo.list_us_stocks(conn)
    by_symbol = {r["symbol"]: r for r in rows}
    assert set(by_symbol) == {"AAPL", "QQQ"}
    assert by_symbol["QQQ"]["is_etf"] == 1
    assert by_symbol["AAPL"]["company_name"] == "Apple Inc."


def test_sync_us_universe_preserves_fundamentals(temp_db: None) -> None:
    """universe 同期は財務素を NULL 上書きしない（upsert_us_stocks の partial update を担保）。"""
    # 先に fundamentals 巡回相当で eps を焼いておく。
    repo.upsert_us_stocks(
        [{"symbol": "AAPL", "eps": 6.5, "gics_sector": "Technology", "updated_at": "t"}]
    )
    # その後 universe 同期が symbol/company_name/is_etf だけで再 UPSERT。
    fake = _FakeAdapter([{"symbol": "AAPL", "company_name": "Apple Inc.", "is_etf": 0}])
    result = sync_us_universe.run(adapter=fake)  # type: ignore[arg-type]
    assert result.ok is True

    with get_engine().connect() as conn:
        row = repo.get_us_stock(conn, "AAPL")
    assert row is not None
    assert row["company_name"] == "Apple Inc."  # universe 側で更新された
    assert row["eps"] == 6.5  # 財務素は消えていない（partial update）
    assert row["gics_sector"] == "Technology"
