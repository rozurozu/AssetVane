"""バリュエーション・スナップショットの下ごしらえ＋組み立て（ADR-014/016・ADR-031）。

設計の真実: docs/decisions.md ADR-031（スクリーナー設計）。

repo（最新終値・最新財務）と quant.valuation（純関数）の間に立ち、全銘柄ぶんの
valuation_snapshots 行を組み立てる。採用行の選定規律（実機確認 2026-06）:
- PER/PBR の実績 EPS/BPS は **最新の通期(FY)行**から（四半期は EPS が累計・BPS が空）。
- 配当（予想年間）と発行済株式数/自己株式は **最新の開示行**から。
- 時価総額の株数は 発行済 − 自己株。
数値計算そのものは quant.valuation に委ね、ここは下ごしらえとオーケストレーションのみ。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection

from app.db import repo
from app.quant import valuation


def _shares_net(latest: dict[str, Any] | None) -> float | None:
    """発行済 − 自己株（時価総額の株数）。発行済が無ければ None。自己株欠損は 0 扱い。"""
    if not latest:
        return None
    shares = latest.get("shares_outstanding")
    if shares is None:
        return None
    treasury = latest.get("treasury_shares") or 0.0
    return shares - treasury


def build_valuation_snapshots(conn: Connection) -> list[dict[str, Any]]:
    """価格のある全銘柄について valuation_snapshots 行を組んで返す（焼くのは呼び出し側）。

    PER/PBR は最新FY行の実績 EPS/BPS、配当/株数は最新開示行を採用する（ADR-031）。
    財務が無い銘柄（ETF 等）も価格があれば行は作る（各指標は None・スクリーナー側で除外可）。
    """
    codes = repo.list_stock_codes(conn)
    closes = repo.get_latest_closes(conn, codes)  # {code: {date, close}}（各 code の最新営業日）
    if not closes:
        return []
    latest_fin = repo.get_latest_financials_by_code(conn)  # 配当・株数
    annual_fin = repo.get_latest_annual_financials_by_code(conn)  # 実績 EPS/BPS（FY）
    now = datetime.now(UTC).isoformat()

    rows: list[dict[str, Any]] = []
    for code, price in closes.items():
        close = price["close"]
        as_of = price["date"]
        latest = latest_fin.get(code)
        annual = annual_fin.get(code)
        eps = annual.get("eps") if annual else None
        bps = annual.get("bps") if annual else None
        dps = latest.get("dividend_per_share") if latest else None
        shares_net = _shares_net(latest)

        metrics = valuation.compute_valuation(
            close=close, eps=eps, bps=bps, dividend_per_share=dps, shares_net=shares_net
        )
        rows.append(
            {
                "code": code,
                "as_of_date": as_of,
                "close": close,
                "eps": eps,
                "bps": bps,
                "dividend_per_share": dps,
                "shares_net": shares_net,
                "per": metrics["per"],
                "pbr": metrics["pbr"],
                "market_cap": metrics["market_cap"],
                "dividend_yield": metrics["dividend_yield"],
                "fin_disclosed_date": (annual or latest or {}).get("disclosed_date"),
                "updated_at": now,
            }
        )
    return rows
