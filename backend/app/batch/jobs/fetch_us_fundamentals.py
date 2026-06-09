"""米株 fundamentals 低頻度ローテ巡回ジョブ — `.info` を古い順に天井まで焼く（Phase 7(B-1)）。

ADR-031/039（米市場分離）・ADR-033（cadence＝古い順＋夜あたり天井）・ADR-018（部分失敗の握り）。
investigate_dossier の巡回選定（古い順＋夜天井 cap）と同型だが、選定は SQL 側
（repo.list_us_symbols_for_fundamentals）に寄せた。`.info` は 1 銘柄ごとに HTTP で重いため、毎晩
全銘柄は焼かず settings.us_fundamentals_nightly_max 本だけ焼き、約 7 夜で一周する（grill 確定）。

各銘柄:
  - UsEquityAdapter.fetch_fundamentals(symbol) で `.info` を内部列に正規化。
  - repo.upsert_us_stocks で **財務素・業種・名称＋YoY 中継率のみ**を partial update（universe
    同期の symbol/company_name/is_etf を消さない・upsert_us_stocks の partial 規約）。`.info` 提供の
    YoY 率（revenue_growth_yoy/earnings_growth_yoy）は中継列として焼き、calc_us_valuation が
    us_valuation_snapshots へ転記する（ADR-055・統括判断で YoY を活かす方針。`.info` の率は実値で
    捏造ではない）。_US_STOCKS_FUNDAMENTAL_COLS に無いキー（あれば）は書き込み前に捨てる。
  - repo.upsert_fetch_meta('us_fundamentals:<symbol>', today) で per-symbol カーソルを前進（次回は
    最後に焼いた銘柄ほど後回しになる＝list_us_symbols_for_fundamentals が古い順に拾う）。

部分失敗の握り（ADR-018）: 1 銘柄が例外でも他を止めない。失敗があれば ok=False（runner が通知）。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.adapters.us_equity import UsEquityAdapter
from app.batch.runner import JobResult
from app.config import settings
from app.db import repo
from app.db.engine import get_engine

logger = logging.getLogger(__name__)

_SOURCE_PREFIX = "us_fundamentals"  # per-symbol fetch_meta source キー接頭辞（fetch_index 同型）

# us_stocks に焼く列（partial update 対象）。adapter が返す dict のうち us_stocks に実在する列だけ
# を残す。YoY 率（revenue_growth_yoy/earnings_growth_yoy）は中継列として焼く＝`.info` 提供の実値を
# us_valuation_snapshots へ転記するため（ADR-055・統括判断で YoY を活かす方針）。symbol/updated_at
# は別途付与する。adapter が返してもこのタプルに無いキー（あれば）は捨てる。
_US_STOCKS_FUNDAMENTAL_COLS = (
    "company_name",
    "gics_sector",
    "industry",
    "eps",
    "bps",
    "shares_net",
    "dividend_per_share",
    "net_sales",
    "operating_profit",
    "profit",
    "revenue_growth_yoy",  # 売上 YoY（`.info.revenueGrowth`・実値）の中継
    "earnings_growth_yoy",  # 純利益 YoY（`.info.earningsGrowth`・実値）の中継
    "fin_disclosed_date",
)


def _source_key(symbol: str) -> str:
    """シンボルごとの fetch_meta source キー（例: 'us_fundamentals:AAPL'・fetch_index 同型）。"""
    return f"{_SOURCE_PREFIX}:{symbol}"


def run(adapter: UsEquityAdapter | None = None) -> JobResult:
    """古い順に settings.us_fundamentals_nightly_max 本だけ `.info` を焼く（ADR-033）。

    `adapter` 引数でテスト用 fake を注入できる（実 HTTP に出さない＝testing-strategy）。
    1 銘柄が例外でも後続を止めない（ADR-018）。失敗が 1 件でもあれば ok=False。
    """
    cap = settings.us_fundamentals_nightly_max
    try:
        with get_engine().connect() as conn:
            symbols = repo.list_us_symbols_for_fundamentals(conn, cap)
    except Exception as exc:  # noqa: BLE001 — ジョブ境界で握り runner に返す
        logger.exception("fetch_us_fundamentals: 巡回対象の選定に失敗")
        return JobResult(
            name="fetch_us_fundamentals", ok=False, rows=0, detail=f"対象選定失敗: {exc}"
        )

    if not symbols:
        return JobResult(
            name="fetch_us_fundamentals",
            ok=True,
            rows=0,
            detail="us_stocks が空（ユニバース未同期）",
        )

    adapter = adapter or UsEquityAdapter()
    today = date_today()
    now = datetime.now(UTC).isoformat()
    n_ok = 0
    failures: list[str] = []

    for symbol in symbols:
        try:
            snap = adapter.fetch_fundamentals(symbol)
            # us_stocks に実在する列だけ残す（YoY 率など未知キーは捨てる・ADR-055）。
            row = {c: snap.get(c) for c in _US_STOCKS_FUNDAMENTAL_COLS}
            row["symbol"] = symbol
            row["updated_at"] = now
            repo.upsert_us_stocks([row])  # partial update（universe の symbol/name/is_etf を保つ）
            repo.upsert_fetch_meta(_source_key(symbol), today)
            n_ok += 1
        except Exception as exc:  # noqa: BLE001 — 銘柄境界で握り後続銘柄を止めない（ADR-018）
            logger.exception("fetch_us_fundamentals: %s の `.info` 取得に失敗", symbol)
            repo.mark_fetch_attempt_failed(_source_key(symbol))
            failures.append(f"{symbol}: {exc}")

    detail = f"巡回 {len(symbols)} 件中 成功 {n_ok}・失敗 {len(failures)}（夜天井 {cap}）"
    if failures:
        detail += " / 失敗詳細: " + "; ".join(failures[:5])
    return JobResult(name="fetch_us_fundamentals", ok=not failures, rows=n_ok, detail=detail)


def date_today() -> str:
    """today を ISO 文字列で返す（fetch_meta カーソル用・テストで monkeypatch しやすい薄い口）。"""
    from datetime import date

    return date.today().isoformat()
