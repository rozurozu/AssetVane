"""売掛/在庫の質シグナルの下ごしらえ＋組み立て（ADR-064 #2・backend-service-quant-pattern）。

設計の真実: docs/decisions.md ADR-064（#2 売掛/在庫の質）・ADR-014/016（事実はコード・解釈は LLM）。

正規化済みの財務時系列（adapters/edinetdb._normalize_financial／US は同形の正規化）
と quant.valuation
（純関数）の間に立ち、valuation_snapshots/us_valuation_snapshots の #2 列（DSO/DIO・
受取債権/在庫 YoY）
を 1 銘柄ぶん組み立てる。採用規律: 最新年（revenue がある最大 fiscal_year）を当期、その前年（
fiscal_year
−1）を YoY 基準とする。売上原価（COGS）は cost_of_sales 優先・無ければ revenue − gross_profit で導出
（近年の行は gross_profit を持つことが多い・実機確認）。導出できなければ DIO は None（捏造しない）。
数値計算そのものは quant.valuation に委ね、ここは下ごしらえとオーケストレーションのみ。
"""

from __future__ import annotations

from typing import Any

from app.quant import valuation


def _cogs(row: dict[str, Any]) -> float | None:
    """売上原価を決める（cost_of_sales 優先・無ければ revenue − gross_profit・ADR-064）。

    どちらも取れない（古い年・サマリのみ）なら None（DIO は None になる＝捏造しない）。
    """
    direct = row.get("cost_of_sales")
    if direct is not None:
        return direct
    revenue = row.get("revenue")
    gross_profit = row.get("gross_profit")
    if revenue is not None and gross_profit is not None:
        cogs = revenue - gross_profit
        return cogs if cogs > 0 else None
    return None


def compute_quality_from_financials(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """財務時系列から #2 の質シグナル（DSO/DIO・受取債権/在庫 YoY）を組む（DB 非依存・ADR-064）。

    rows は正規化済み（
    各 row に fiscal_year/disclosed_date/receivables/inventory/revenue/gross_profit/
    cost_of_sales）。当期＝revenue がある最大 fiscal_year・YoY 基準＝その前年（fiscal_year−1）
    。前年行が
    無ければ YoY は None（>1 年差の増減率は誤解を招くため exact −1 のみ・growth_yoy と同方針）。
    当期が取れない／受取債権も在庫も流動資産も総負債も無いなら None（焼くものが無い）。戻り値は
    valuation_snapshots の #2 列（4 つ）＋ 清原式 net_cash（ADR-079）＋ 監査用 fin_disclosed_date。
    """
    typed = [r for r in rows if isinstance(r.get("fiscal_year"), int)]
    with_rev = [r for r in typed if r.get("revenue") is not None]
    if not with_rev:
        return None
    curr = max(with_rev, key=lambda r: r["fiscal_year"])
    # 受取債権・在庫・流動資産・総負債のいずれも無ければ焼くものが無い（net_cash も #2 も出ない）。
    # BS だけある銘柄（受取債権/在庫は空でも流動資産/総負債はある）で net_cash を拾えるよう緩めた
    # （ADR-079。#2 列は None のままで既存挙動を壊さない＝元々 None だった行を None で埋めるだけ）。
    if (
        curr.get("receivables") is None
        and curr.get("inventory") is None
        and curr.get("current_assets") is None
        and curr.get("total_liabilities") is None
    ):
        return None

    prev_year = curr["fiscal_year"] - 1
    prev = next((r for r in typed if r["fiscal_year"] == prev_year), None)

    receivables = curr.get("receivables")
    inventory = curr.get("inventory")
    revenue = curr.get("revenue")
    return {
        "receivables_turnover_days": valuation.receivables_turnover_days(receivables, revenue),
        "inventory_turnover_days": valuation.inventory_turnover_days(inventory, _cogs(curr)),
        "receivables_growth_yoy": valuation.growth_yoy(
            receivables, prev.get("receivables") if prev else None
        ),
        "inventory_growth_yoy": valuation.growth_yoy(
            inventory, prev.get("inventory") if prev else None
        ),
        # 清原式ネットキャッシュ（ADR-079・full 化）。JP/US とも investment_securities があれば
        # フル式、欠落時のみ簡略式（保守側）へ。比率（÷時価総額）は焼かず read-time 導出（repo）。
        "net_cash": valuation.net_cash(
            curr.get("current_assets"),
            curr.get("investment_securities"),
            curr.get("total_liabilities"),
        ),
        "fin_disclosed_date": curr.get("disclosed_date"),
    }
