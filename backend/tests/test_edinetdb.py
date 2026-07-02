"""adapters/edinetdb の正規化境界 `_normalize_financial` の純粋テスト（ADR-010/064/079）。

ネットに出ず、edinetdb.jp のレスポンス 1 行を模したサンプル dict で「外部キー名→内部列名」の
写像を固定する（testing-strategy）。特に清原式ネットキャッシュ（ADR-079）の投資有価証券
（`investment_securities`＝OpenAPI `/v1/openapi.yaml` に実在）を JP でも拾えることを検証する。
"""

from __future__ import annotations

from app.adapters.edinetdb import _normalize_financial


def _raw() -> dict:
    """edinetdb.jp `/companies/{ec}/financials` の 1 行を模したサンプル（BS 項目込み）。"""
    return {
        "fiscal_year": 2025,
        "submit_date": "2025-06-18 15:00",
        "accounting_standard": "JP GAAP",
        "trade_receivables": 100.0,
        "inventories": 200.0,
        "revenue": 1000.0,
        "gross_profit": 300.0,
        "cost_of_sales": 700.0,
        "current_assets": 8000.0,
        "investment_securities": 2000.0,
        "total_liabilities": 3000.0,
        "cash": 1500.0,
    }


def test_normalize_maps_investment_securities() -> None:
    # 清原式 full 化（ADR-079）: JP でも投資有価証券を実値で拾う（欠落→簡略式ではなくフル式へ）。
    got = _normalize_financial(_raw())
    assert got["investment_securities"] == 2000.0


def test_normalize_maps_kiyohara_bs_fields() -> None:
    # 清原式ネットキャッシュに要る BS 3 項目＋#2 の売掛/在庫が同一行から同一単位で正規化される。
    got = _normalize_financial(_raw())
    assert got["current_assets"] == 8000.0
    assert got["total_liabilities"] == 3000.0
    assert got["cash"] == 1500.0
    assert got["receivables"] == 100.0
    assert got["inventory"] == 200.0


def test_normalize_investment_securities_missing_is_none() -> None:
    # 投資有価証券が欠落（IFRS 銘柄・古い年）なら None → services で簡略式（保守側）へ倒れる。
    raw = _raw()
    del raw["investment_securities"]
    got = _normalize_financial(raw)
    assert got["investment_securities"] is None
    # 他の BS 項目が取れていれば net_cash は簡略式で出せる（current/total_liabilities は健在）。
    assert got["current_assets"] == 8000.0
    assert got["total_liabilities"] == 3000.0
