"""米国株マスタ・日足・バリュエ・スクリーニング（Phase 7(B-1)・提示専用・ADR-031/039/048）。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Connection, and_, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.engine import get_engine
from app.db.repo._common import _upsert
from app.db.schema import (
    fetch_meta,
    us_daily_quotes,
    us_stocks,
    us_valuation_snapshots,
)

# ===== Phase 7(B-1): 米国株（提示専用・ADR-031/039・us_stocks マスタ） =====
# 日本株 stocks の upsert_stocks / list_stocks / get_stock をミラーした米株版（既存無改変）。
# us_stocks は別系統で stocks に存在しないため、業種/名称はこの表に持ち JOIN では補わない。


def upsert_us_stocks(rows: list[dict[str, Any]]) -> int:
    """us_stocks を冪等 UPSERT（symbol 1 行・ADR-002/031）。**渡された列だけ**を更新する。

    universe 同期（symbol/company_name/is_etf）と fundamentals 巡回（財務素・業種・updated_at）が
    この関数を共有するため、行に含まれない列は既存値を保たねばならない（universe 同期が財務素を
    NULL で上書きすると焼けた fundamentals が消える）。汎用 _upsert は table の全列を EXCLUDED で
    更新し、executemany で行に無い列は NULL になるため partial update が壊れる。よってここでは
    rows に現れた列の和集合だけを on_conflict_do_update の対象にする（symbol は更新しない）。
    """
    if not rows:
        return 0
    present_cols = {k for r in rows for k in r}
    update_cols = [c for c in present_cols if c != "symbol"]
    stmt = sqlite_insert(us_stocks)
    set_ = {name: stmt.excluded[name] for name in update_cols}
    # 更新対象が無い（symbol だけ）場合は DO NOTHING で重複挿入を握る（冪等）。
    if set_:
        stmt = stmt.on_conflict_do_update(index_elements=["symbol"], set_=set_)
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=["symbol"])
    with get_engine().begin() as conn:
        conn.execute(stmt, rows)
    return len(rows)


def list_us_stocks(conn: Connection, q: str | None = None) -> list[dict[str, Any]]:
    """米株マスタ一覧（symbol/company_name の部分一致フィルタ可・list_stocks 同型）。"""
    stmt = select(us_stocks)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(us_stocks.c.symbol.like(like) | us_stocks.c.company_name.like(like))
    stmt = stmt.order_by(us_stocks.c.symbol)
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def get_us_stock(conn: Connection, symbol: str) -> dict[str, Any] | None:
    """米株 1 銘柄を symbol で引く（get_stock 同型・無ければ None）。"""
    row = conn.execute(select(us_stocks).where(us_stocks.c.symbol == symbol)).mappings().first()
    return dict(row) if row else None


# ----- 米株 OHLCV・バリュエーション（fetch/calc/screen ＝ウェーブ2・ADR-031/039/048） -----
# 日本株の upsert_daily_quotes / get_quotes / get_latest_closes / upsert_valuation_snapshots /
# _valuation_inner_subquery / screen_stocks / get_valuation_snapshot をミラーした米株版（既存
# 無改変）。partition は sector33_code ではなく gics_sector（Yahoo `.info.sector`・ADR-055）。


def upsert_us_daily_quotes(conn: Connection, rows: list[dict[str, Any]]) -> int:
    """us_daily_quotes を (symbol,date) 冪等 UPSERT（ADR-002・upsert_daily_quotes 同型）。

    W2 寄りに conn を受けて execute だけ行い commit しない（呼び出し側＝バッチが begin を所有し、
    1 バッチ分の OHLCV を 1 トランザクションに束ねる）。re-取得・再実行で重複しない。
    """
    if not rows:
        return 0
    stmt = sqlite_insert(us_daily_quotes)
    update_cols = {
        c: stmt.excluded[c] for c in ("open", "high", "low", "close", "volume", "adj_close")
    }
    stmt = stmt.on_conflict_do_update(index_elements=["symbol", "date"], set_=update_cols)
    conn.execute(stmt, rows)
    return len(rows)


def upsert_us_valuation_snapshots(rows: list[dict[str, Any]]) -> int:
    """us_valuation_snapshots を symbol 冪等 UPSERT（最新のみ保持・valuation 同型・ADR-031）。"""
    return _upsert(us_valuation_snapshots, rows, index_elements=["symbol"])


def get_us_quotes(
    conn: Connection,
    symbol: str,
    from_: str | None = None,
    to: str | None = None,
) -> list[dict[str, Any]]:
    """米株日足を date 昇順で返す（チャート用・get_quotes 同型）。"""
    stmt = select(us_daily_quotes).where(us_daily_quotes.c.symbol == symbol)
    if from_:
        stmt = stmt.where(us_daily_quotes.c.date >= from_)
    if to:
        stmt = stmt.where(us_daily_quotes.c.date <= to)
    stmt = stmt.order_by(us_daily_quotes.c.date)
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def get_latest_us_closes(
    conn: Connection, symbols: list[str] | None = None
) -> dict[str, dict[str, Any]]:
    """各 symbol の MAX(date) の close を返す（valuation 計算用・get_latest_closes 同型）。

    返却: {symbol: {"date": str, "close": float}}。close が無い symbol はキーに現れない。
    symbols=None なら全銘柄（build_us_valuation_snapshots は全銘柄を畳むため None で呼ぶ）。
    """
    subq = select(us_daily_quotes.c.symbol, func.max(us_daily_quotes.c.date).label("max_date"))
    if symbols is not None:
        if not symbols:
            return {}
        subq = subq.where(us_daily_quotes.c.symbol.in_(symbols))
    subq = subq.group_by(us_daily_quotes.c.symbol).subquery()

    stmt = select(
        us_daily_quotes.c.symbol,
        us_daily_quotes.c.date,
        us_daily_quotes.c.close,
    ).join(
        subq,
        and_(
            us_daily_quotes.c.symbol == subq.c.symbol,
            us_daily_quotes.c.date == subq.c.max_date,
        ),
    )
    result: dict[str, dict[str, Any]] = {}
    for r in conn.execute(stmt).mappings().all():
        result[r["symbol"]] = {"date": r["date"], "close": r["close"]}
    return result


def list_us_symbols_for_fundamentals(conn: Connection, limit: int) -> list[str]:
    """fundamentals 巡回対象を「最終取得が古い順（未取得最優先）」に limit 件返す（ADR-033）。

    investigate_dossier._select_targets の SQL 版。fetch_meta の source キー
    'us_fundamentals:<symbol>' を us_stocks に LEFT JOIN し、last_fetched_date が NULL（未取得）を
    最優先、次いで古い順に並べる。
    夜あたり天井（settings.us_fundamentals_nightly_max）を呼び出し側が limit で渡す。
    """
    if limit <= 0:
        return []
    src = "us_fundamentals:" + us_stocks.c.symbol
    meta_join = fetch_meta.c.source == src
    # NULL（未取得）を最優先にするため、NULL を空文字に畳んで昇順（'' が最小＝先頭）。
    order_key = func.coalesce(fetch_meta.c.last_fetched_date, "")
    stmt = (
        select(us_stocks.c.symbol)
        .select_from(us_stocks.join(fetch_meta, meta_join, isouter=True))
        .order_by(order_key.asc(), us_stocks.c.symbol.asc())
        .limit(limit)
    )
    return list(conn.execute(stmt).scalars().all())


# screen_us_stocks が受け付ける数値レンジのキー（{field}_min / {field}_max）。日本株の
# _SCREEN_RANGE_FIELDS と同列（YoY は None になり得るが NULL は絞り込みで自然に除外される）。
_US_SCREEN_RANGE_FIELDS = (
    "per",
    "pbr",
    "market_cap",
    "dividend_yield",
    "roe",
    "operating_margin",
    "net_margin",
    "revenue_growth_yoy",
    "op_growth_yoy",
    "profit_growth_yoy",
    "eps_growth_yoy",
)
# sort_by に許す列名（外側サブクエリの列）。安全な allowlist（_SCREEN_SORT_COLS 同型・米株名）。
_US_SCREEN_SORT_COLS = {
    "per",
    "pbr",
    "market_cap",
    "dividend_yield",
    "roe",
    "operating_margin",
    "net_margin",
    "revenue_growth_yoy",
    "op_growth_yoy",
    "profit_growth_yoy",
    "eps_growth_yoy",
    "gics_sector_pctile",
    "market_cap_rank",
    "symbol",
}


def _us_valuation_inner_subquery():
    """us_valuation_snapshots × us_stocks ＋ window ランク列の内側サブクエリ（valuation 版同型）。

    GICS sector 内パーセンタイル（gics_sector_pctile・per 昇順＝安いほど低い）と時価総額順位
    （market_cap_rank・降順 1 位が最大）を都度算出する。日本株は sector33_code で partition するが、
    米株は Yahoo `.info.sector`（gics_sector）で partition する（ADR-055）。company_name/
    gics_sector/industry/is_etf は us_stocks 側に持つため JOIN で補う（日本株 stocks JOIN と同様）。
    """
    v = us_valuation_snapshots
    s = us_stocks
    gics_sector_pctile = (
        func.percent_rank()
        .over(partition_by=s.c.gics_sector, order_by=v.c.per)
        .label("gics_sector_pctile")
    )
    market_cap_rank = (
        func.row_number().over(order_by=v.c.market_cap.desc()).label("market_cap_rank")
    )
    return (
        select(
            v.c.symbol,
            s.c.company_name,
            s.c.gics_sector,
            s.c.industry,
            s.c.is_etf,
            v.c.as_of_date,
            v.c.close,
            v.c.eps,
            v.c.bps,
            v.c.dividend_per_share,
            v.c.per,
            v.c.pbr,
            v.c.market_cap,
            v.c.dividend_yield,
            v.c.roe,
            v.c.operating_margin,
            v.c.net_margin,
            v.c.revenue_growth_yoy,
            v.c.op_growth_yoy,
            v.c.profit_growth_yoy,
            v.c.eps_growth_yoy,
            gics_sector_pctile,
            market_cap_rank,
        )
        .select_from(v.join(s, v.c.symbol == s.c.symbol))
        .subquery()
    )


def get_us_valuation_snapshot(conn: Connection, symbol: str) -> dict[str, Any] | None:
    """米株 1 銘柄のバリュエーション事実（PER/PBR/ROE/利益率/成長率＋GICS 内ランク）を返す。

    screen_us_stocks と同じ window ランクを共有し symbol で 1 行に絞る（valuation 版同型）。
    未焼成・未上場なら None。数値は夜間 calc_us_valuation が焼いた事実で verdict は持たない
    （ADR-014）。
    """
    inner = _us_valuation_inner_subquery()
    row = conn.execute(select(inner).where(inner.c.symbol == symbol)).mappings().first()
    return dict(row) if row else None


def screen_us_stocks(conn: Connection, criteria: dict[str, Any]) -> list[dict[str, Any]]:
    """us_valuation_snapshots × us_stocks を絞り込み・整列して返す（読み取り時計算・screen 同型）。

    GICS sector 内パーセンタイル（gics_sector_pctile）と時価総額順位（market_cap_rank）は window
    関数で都度算出する。criteria は薄い辞書（router/Tool が Pydantic から作る）:
      {field}_min/{field}_max（per/pbr/market_cap/dividend_yield/roe/operating_margin/net_margin/
      *_growth_yoy の絶対レンジ）、gics_sector（完全一致）、exclude_etf(bool)、
      gics_sector_pctile_max（GICS 内で安い割合・0..1）、market_cap_rank_max（時価総額 上位 N）、
      sort_by・sort_dir('asc'|'desc')・limit・offset。
    戻り値は素 dict（company_name/gics_sector/industry/is_etf を us_stocks から JOIN 補完）。
    """
    inner = _us_valuation_inner_subquery()

    conds = []
    # 絶対レンジ（min/max）。YoY 等の NULL 列は比較で自然に除外される。
    for field in _US_SCREEN_RANGE_FIELDS:
        col = inner.c[field]
        lo = criteria.get(f"{field}_min")
        hi = criteria.get(f"{field}_max")
        if lo is not None:
            conds.append(col >= lo)
        if hi is not None:
            conds.append(col <= hi)
    # 完全一致・ETF 除外
    if criteria.get("gics_sector"):
        conds.append(inner.c.gics_sector == criteria["gics_sector"])
    if criteria.get("exclude_etf"):
        conds.append(inner.c.is_etf == 0)
    # ランク系（GICS 内で安い割合・時価総額 上位 N）
    if criteria.get("gics_sector_pctile_max") is not None:
        conds.append(inner.c.gics_sector_pctile <= criteria["gics_sector_pctile_max"])
    if criteria.get("market_cap_rank_max") is not None:
        conds.append(inner.c.market_cap_rank <= criteria["market_cap_rank_max"])

    stmt = select(inner)
    if conds:
        stmt = stmt.where(and_(*conds))

    # 整列（allowlist・既定は時価総額降順）
    sort_by = criteria.get("sort_by") or "market_cap"
    if sort_by not in _US_SCREEN_SORT_COLS:
        sort_by = "market_cap"
    sort_col = inner.c[sort_by]
    stmt = stmt.order_by(sort_col.asc() if criteria.get("sort_dir") == "asc" else sort_col.desc())

    limit = int(criteria.get("limit") or 200)
    limit = max(1, min(limit, 1000))  # 暴走防止の上限
    stmt = stmt.limit(limit)
    if criteria.get("offset"):
        stmt = stmt.offset(int(criteria["offset"]))

    return [dict(r) for r in conn.execute(stmt).mappings().all()]
