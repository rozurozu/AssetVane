"""calc_receivables_inventory（#2 売掛/在庫＋清原式 net_cash）の担保（ADR-064/079/083）。

未設定なら静かに skip（ok=True）・設定済みなら edinetdb.jp の財務（fake 注入）から既存
valuation_snapshots 行へ #2 列＋net_cash を UPDATE する・cadence（fetch_meta）で再取得を抑える・
母集団は全普通株で full_backfill は watchlist 外の net_cash NULL も焼く（ADR-083）・選別純関数
（初回一括／開示差分）を固定する。ネットに出ず一時 SQLite で回す（testing-strategy）。
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
            # 清原式ネットキャッシュの BS 項目（JP は投資有価証券なし＝簡略式・ADR-079）
            "current_assets": 8000.0,
            "total_liabilities": 3000.0,
            "cash": 500.0,
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
    # 清原式ネットキャッシュ（JP 簡略式）: 8000 − 3000 = 5000（投資有価証券項なし・ADR-079）
    assert snap["net_cash"] == 5000.0
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


# 清原式ネットキャッシュの full BS 行（JP 簡略式: current − total = 8000 − 3000 = 5000・ADR-079）
_FULL_BS_FINS = [
    {
        "fiscal_year": 2025,
        "disclosed_date": "2025-06-18",
        "receivables": 150.0,
        "inventory": 260.0,
        "revenue": 1100.0,
        "gross_profit": 330.0,
        "cost_of_sales": None,
        "current_assets": 8000.0,
        "total_liabilities": 3000.0,
        "cash": 500.0,
    },
]


def test_select_targets_full_backfill_picks_missing_net_cash() -> None:
    """初回一括: net_cash NULL かつ cadence 外だけを対象にする純関数（ADR-083）。"""
    targets = calc_receivables_inventory._select_targets(
        ["1", "2", "3", "4"],
        full_backfill=True,
        net_cash_missing={"1", "2", "3"},  # 4 は焼済み（NULL でない）
        last_fetched={"2": "2026-07-01"},  # 2 は interval_days=7 内で最近焼いた
        disclosed={},
        today="2026-07-05",
        interval_days=7,
    )
    # 1: NULL・未取得→焼く / 2: NULL だが cadence 内→skip / 3: NULL・未取得→焼く / 4: 焼済み→skip
    assert targets == ["1", "3"]


def test_select_targets_differential_new_disclosure_only() -> None:
    """定常: 初回（未取得）か 新規開示（disclosed > 前回焼き）だけを対象にする純関数（ADR-083）。"""
    targets = calc_receivables_inventory._select_targets(
        ["1", "2", "3"],
        full_backfill=False,
        net_cash_missing=set(),
        last_fetched={"1": "2026-06-01", "2": "2026-06-01"},  # 3 は未取得
        disclosed={"1": "2026-06-20", "2": "2026-05-01"},  # 1 は新規開示・2 は前回焼きより古い
        today="2026-07-05",
        interval_days=7,
    )
    # 1: 開示>前回焼き→焼く / 2: 開示<前回焼き→skip / 3: 初回（未取得）→焼く
    assert targets == ["1", "3"]


def test_full_backfill_burns_universe_missing_net_cash(temp_db, monkeypatch) -> None:
    """full_backfill=True は watchlist 外でも net_cash NULL の全普通株を焼く（ADR-083）。"""
    with get_engine().begin() as conn:
        repo.upsert_edinetdb_config(conn, {"api_key": "edb_test", "plan": "pro"})
    _seed_stock_with_snapshot("61980")  # watchlist にも holdings にも入れない発掘対象
    fake = _FakeAdapter(_FULL_BS_FINS)
    monkeypatch.setattr(calc_receivables_inventory, "build_edinetdb_adapter", lambda conn: fake)

    result = calc_receivables_inventory.run(full_backfill=True)
    assert result.ok is True
    assert result.rows == 1

    with get_engine().connect() as conn:
        snap = repo.get_valuation_snapshot(conn, "61980")
    assert snap is not None
    assert snap["net_cash"] == 5000.0  # 全ユニバース化で watchlist 外でも焼けた（ADR-083）


def test_differential_skips_already_burned_without_new_disclosure(temp_db, monkeypatch) -> None:
    """定常: 既に net_cash が焼けていて新規開示も無い銘柄は再取得しない（予算節約・ADR-083）。"""
    with get_engine().begin() as conn:
        repo.upsert_edinetdb_config(conn, {"api_key": "edb_test", "plan": "pro"})
    _seed_stock_with_snapshot("61980")
    # 既に焼けている状態を作る（net_cash NOT NULL・fetch_meta あり・開示は前回焼きより古い）
    with get_engine().begin() as conn:
        repo.update_valuation_receivables_inventory(conn, "61980", {"net_cash": 5000.0})
    repo.upsert_fetch_meta("edinetdb_quality:61980", "2026-07-01")
    fake = _FakeAdapter(_FULL_BS_FINS)
    monkeypatch.setattr(calc_receivables_inventory, "build_edinetdb_adapter", lambda conn: fake)

    result = calc_receivables_inventory.run()  # 定常（full_backfill=False）
    assert result.ok is True
    assert result.rows == 0
    assert fake.resolved == []  # 開示差分なし＝叩かない
