"""列を分担して書くテーブルの partial UPSERT を固定する（ADR-064 #2・#1/#7/#8）。

`_common._upsert` は既定で衝突キー以外の**全列**を EXCLUDED 更新するため、行 dict に含まれない列
（＝別ジョブが後段で UPDATE 充填する列）を毎晩 NULL に潰していた。ここでは「主要列を書く UPSERT が、
別経路で焼かれた列（valuation_snapshots の DSO/DIO・stocks.edinet_code）を温存する」ことを固定する。

夜間の実順序を再現する:
  ① 主要 UPSERT（calc_valuation / sync_master）が最初に焼く
  ② 別ジョブ（calc_receivables_inventory / edinetdb sweep）が担当列だけ UPDATE
  ③ 翌晩 ① が担当列を含まない行で再 UPSERT → ② の列は温存されねばならない（②は cadence で殆ど skip）
"""

from __future__ import annotations

from sqlalchemy import select

from app.db import repo
from app.db.engine import get_engine
from app.db.schema import stocks, us_valuation_snapshots, valuation_snapshots

_QUALITY = {
    "receivables_turnover_days": 45.0,
    "inventory_turnover_days": 30.0,
    "receivables_growth_yoy": 0.1,
    "inventory_growth_yoy": -0.05,
}


def test_jp_valuation_upsert_preserves_receivables_inventory(temp_db) -> None:
    """#1: calc_valuation の再 UPSERT が calc_receivables_inventory の焼いた #2 列を温存する。"""
    repo.upsert_stocks([{"code": "72030", "company_name": "トヨタ自動車"}])
    # ① 主要列を焼く
    repo.upsert_valuation_snapshots([{"code": "72030", "as_of_date": "2026-06-30", "per": 15.0}])
    # ② 別ジョブが #2 列だけ UPDATE
    with get_engine().begin() as conn:
        assert repo.update_valuation_receivables_inventory(conn, "72030", _QUALITY) == 1
    # ③ 翌晩 #2 列を含まない行で再 UPSERT
    repo.upsert_valuation_snapshots([{"code": "72030", "as_of_date": "2026-07-01", "per": 16.0}])

    with get_engine().connect() as conn:
        row = (
            conn.execute(select(valuation_snapshots).where(valuation_snapshots.c.code == "72030"))
            .mappings()
            .one()
        )
    assert row["per"] == 16.0  # 主要列は更新される
    assert row["as_of_date"] == "2026-07-01"
    assert row["receivables_turnover_days"] == 45.0  # #2 列は温存される（潰れない）
    assert row["inventory_turnover_days"] == 30.0
    assert row["receivables_growth_yoy"] == 0.1
    assert row["inventory_growth_yoy"] == -0.05


def test_us_valuation_upsert_preserves_receivables_inventory(temp_db) -> None:
    """#7: calc_us_valuation の再 UPSERT が calc_us_receivables_inventory の #2 列を温存する。"""
    repo.upsert_us_stocks([{"symbol": "AAPL", "company_name": "Apple", "is_etf": 0}])
    repo.upsert_us_valuation_snapshots(
        [{"symbol": "AAPL", "as_of_date": "2026-06-30", "per": 25.0}]
    )
    with get_engine().begin() as conn:
        assert repo.update_us_valuation_receivables_inventory(conn, "AAPL", _QUALITY) == 1
    repo.upsert_us_valuation_snapshots(
        [{"symbol": "AAPL", "as_of_date": "2026-07-01", "per": 26.0}]
    )

    with get_engine().connect() as conn:
        row = (
            conn.execute(
                select(us_valuation_snapshots).where(us_valuation_snapshots.c.symbol == "AAPL")
            )
            .mappings()
            .one()
        )
    assert row["per"] == 26.0
    assert row["receivables_turnover_days"] == 45.0
    assert row["inventory_turnover_days"] == 30.0
    assert row["receivables_growth_yoy"] == 0.1
    assert row["inventory_growth_yoy"] == -0.05


def test_upsert_stocks_preserves_edinet_code(temp_db) -> None:
    """#8: sync_master の再 UPSERT が edinetdb sweep の解決した edinet_code を温存する。"""
    repo.upsert_stocks([{"code": "72030", "company_name": "トヨタ自動車", "is_etf": 0}])
    # 月次 sweep が edinet_code を解決して焼く
    with get_engine().begin() as conn:
        repo.set_stock_edinet_code(conn, "72030", "E02144")
    # 翌晩 sync_master が edinet_code を含まない行で再 UPSERT（company_name 変化）
    repo.upsert_stocks([{"code": "72030", "company_name": "トヨタ自動車株式会社", "is_etf": 0}])

    with get_engine().connect() as conn:
        row = conn.execute(select(stocks).where(stocks.c.code == "72030")).mappings().one()
    assert row["company_name"] == "トヨタ自動車株式会社"  # 主要列は更新される
    assert row["edinet_code"] == "E02144"  # 別経路の永続キーは温存される（潰れない）
