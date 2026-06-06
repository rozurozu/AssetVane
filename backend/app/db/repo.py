"""クエリ（SQLAlchemy Core）。

書き込みは PK 衝突時更新の UPSERT で冪等にする（再取得で重複しない＝Phase 0 完了条件・ADR-002）。
読み取りは API ルータから呼ぶ。戻り値は素の dict（ルータ側で Pydantic に詰める）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, Table, and_, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.engine import get_engine
from app.db.schema import (
    advisor_journal,
    asset_snapshots,
    cash,
    daily_quotes,
    dossier_sources,
    external_assets,
    fetch_meta,
    financials,
    general_news,
    holdings,
    index_quotes,
    llm_usage,
    notifications,
    policy,
    portfolios,
    proposals,
    screening_filters,
    signals,
    stock_dossiers,
    stocks,
    transactions,
    valuation_snapshots,
    watchlist,
)


def _upsert(table: Table, rows: list[dict[str, Any]], index_elements: list[str]) -> int:
    """rows を UPSERT する。衝突キー以外の列を EXCLUDED で更新（冪等）。"""
    if not rows:
        return 0
    stmt = sqlite_insert(table)
    update_cols = {
        col.name: stmt.excluded[col.name] for col in table.columns if col.name not in index_elements
    }
    stmt = stmt.on_conflict_do_update(index_elements=index_elements, set_=update_cols)
    with get_engine().begin() as conn:
        conn.execute(stmt, rows)
    return len(rows)


def upsert_stocks(rows: list[dict[str, Any]]) -> int:
    return _upsert(stocks, rows, index_elements=["code"])


def upsert_daily_quotes(rows: list[dict[str, Any]]) -> int:
    return _upsert(daily_quotes, rows, index_elements=["code", "date"])


def list_stocks(conn: Connection, q: str | None = None) -> list[dict[str, Any]]:
    stmt = select(stocks)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(stocks.c.code.like(like) | stocks.c.company_name.like(like))
    stmt = stmt.order_by(stocks.c.code)
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def get_stock(conn: Connection, code: str) -> dict[str, Any] | None:
    row = conn.execute(select(stocks).where(stocks.c.code == code)).mappings().first()
    return dict(row) if row else None


def get_quotes(
    conn: Connection,
    code: str,
    from_: str | None = None,
    to: str | None = None,
) -> list[dict[str, Any]]:
    stmt = select(daily_quotes).where(daily_quotes.c.code == code)
    if from_:
        stmt = stmt.where(daily_quotes.c.date >= from_)
    if to:
        stmt = stmt.where(daily_quotes.c.date <= to)
    stmt = stmt.order_by(daily_quotes.c.date)
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


# --- fetch_meta（差分取得の進捗管理・Phase 1・spec §3.2） ---


def upsert_fetch_meta(source: str, last_fetched_date: str) -> None:
    """`source` の取得済み最終営業日を前進させる（冪等・spec §3.2）。

    `updated_at` は「いつ最後にバッチが回ったか」を運用で見るため、関数内で UTC now を入れる。
    途中で落ちても翌回は続きから回せる（ADR-018 部分失敗からの再開）。
    """
    row = {
        "source": source,
        "last_fetched_date": last_fetched_date,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    _upsert(fetch_meta, [row], index_elements=["source"])


def get_fetch_meta(conn: Connection, source: str) -> dict[str, Any] | None:
    """`source` の 1 行 or None（spec §3.2）。

    last_fetched_date が None / 行が未存在なら「初回」扱い（full_backfill を促す）。
    """
    row = conn.execute(select(fetch_meta).where(fetch_meta.c.source == source)).mappings().first()
    return dict(row) if row else None


def get_max_quote_date(conn: Connection) -> str | None:
    """SELECT MAX(date) FROM daily_quotes（spec §3.2）。

    fetch_meta 不在時の自己修復フォールバック（既に取得済みの最終営業日を実データから割り出す）。
    """
    return conn.execute(select(func.max(daily_quotes.c.date))).scalar()


def list_stock_codes(conn: Connection) -> list[str]:
    """stocks の全 code（calc_signals / 進捗ログ用・spec §3.2）。"""
    rows = conn.execute(select(stocks.c.code).order_by(stocks.c.code)).scalars().all()
    return list(rows)


def get_max_financial_disclosed_date(conn: Connection) -> str | None:
    """SELECT MAX(disclosed_date) FROM financials（fetch_financials の自己修復用・ADR-031）。"""
    return conn.execute(select(func.max(financials.c.disclosed_date))).scalar()


# --- signals（シグナル事前計算・Phase 1・spec §3.2・ADR-002・ADR-026） ---


def upsert_signals(rows: list[dict[str, Any]]) -> int:
    """signals を冪等 UPSERT する（spec §3.2）。

    `(date, code, signal_type)` の UNIQUE で衝突解決し、同じ夜の再実行でも重複しない。
    rows の `payload` は呼び出し側（calc_signals）が json.dumps 済みの JSON 文字列。
    repo は変換せずそのまま UPSERT するだけ（契約・厳守）。
    """
    return _upsert(signals, rows, index_elements=["date", "code", "signal_type"])


def get_latest_signal_date(conn: Connection, signal_type: str | None = None) -> str | None:
    """signals の最新算出日を返す（spec §3.2）。

    `signal_type` 指定時はその type に絞って MAX(date) を取る（全 type なら絞らない）。
    """
    stmt = select(func.max(signals.c.date))
    if signal_type:
        stmt = stmt.where(signals.c.signal_type == signal_type)
    return conn.execute(stmt).scalar()


def get_signals(
    conn: Connection,
    date: str | None,
    signal_type: str | None,
    code: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """signals を stocks に LEFT JOIN し company_name を補完して返す（spec §3.2・契約・厳守）。

    並び順は date 降順 → score 降順。`date` が None のときは get_latest_signal_date で
    最新算出日を自動採用する。各行 dict は code/company_name/signal_type/score/payload/date を含む。
    `payload` は **生の TEXT 文字列のまま**返す（json.loads はルータの責務）。
    """
    if date is None:
        date = get_latest_signal_date(conn, signal_type)
        if date is None:
            return []

    stmt = (
        select(
            signals.c.code,
            stocks.c.company_name,
            signals.c.signal_type,
            signals.c.score,
            signals.c.payload,
            signals.c.date,
        )
        .select_from(signals.outerjoin(stocks, signals.c.code == stocks.c.code))
        .where(signals.c.date == date)
    )
    if signal_type:
        stmt = stmt.where(signals.c.signal_type == signal_type)
    if code:
        stmt = stmt.where(signals.c.code == code)
    stmt = stmt.order_by(signals.c.date.desc(), signals.c.score.desc()).limit(limit)
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


# ===== Phase 2: Portfolio Optimizer（phase2-spec.md §2・ADR-002） =====


def upsert_index_quotes(rows: list[dict[str, Any]]) -> int:
    """index_quotes を冪等 UPSERT する（phase2-spec.md §3.1・ADR-002）。

    衝突キー: (symbol, date)。IndexAdapter が内部列名に正規化済みの行を受け取る。
    """
    return _upsert(index_quotes, rows, index_elements=["symbol", "date"])


def upsert_financials(rows: list[dict[str, Any]]) -> int:
    """financials を冪等 UPSERT する（phase2-spec.md §3.2・ADR-002・0005_financials）。

    衝突キー: (code, disclosed_date, fiscal_period)。JQuantsAdapter が正規化済みの行を受け取る。
    """
    return _upsert(financials, rows, index_elements=["code", "disclosed_date", "fiscal_period"])


def get_financials(conn: Connection, code: str, limit: int = 8) -> list[dict[str, Any]]:
    """指定銘柄の financials を disclosed_date 降順で返す（get_financials Tool 用・spec §4.4）。

    各行 dict は disclosed_date/fiscal_period/net_sales/operating_profit/profit/eps/bps を含む。
    `limit` は直近 N 件（既定 8 = 約 2 年分の四半期）。素の dict 返し（変換はルータ/handler）。
    """
    stmt = (
        select(
            financials.c.disclosed_date,
            financials.c.fiscal_period,
            financials.c.net_sales,
            financials.c.operating_profit,
            financials.c.profit,
            financials.c.eps,
            financials.c.bps,
        )
        .where(financials.c.code == code)
        .order_by(financials.c.disclosed_date.desc())
        .limit(limit)
    )
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


# --- バリュエーション・スクリーニング（ADR-031・0007_screening） ---


def _latest_financials_subquery(only_with_bps: bool):
    """銘柄ごと disclosed_date 降順で 1 番目の行を取る window サブクエリを組む（ADR-031）。

    only_with_bps=True は BPS が入る通期(FY)行のみを対象にし、PER/PBR 用の実績 EPS/BPS を拾う
    （四半期は EPS が累計・BPS が空のため＝実機確認 2026-06）。
    """
    rn = (
        func.row_number()
        .over(
            partition_by=financials.c.code,
            order_by=financials.c.disclosed_date.desc(),
        )
        .label("rn")
    )
    base = select(
        financials.c.code,
        financials.c.disclosed_date,
        financials.c.eps,
        financials.c.bps,
        financials.c.dividend_per_share,
        financials.c.shares_outstanding,
        financials.c.treasury_shares,
        rn,
    )
    if only_with_bps:
        base = base.where(financials.c.bps.isnot(None))
    return base.subquery()


def get_latest_financials_by_code(conn: Connection) -> dict[str, dict[str, Any]]:
    """銘柄ごと最新開示行（配当・株数用）を {code: {...}} で返す（ADR-031）。"""
    sub = _latest_financials_subquery(only_with_bps=False)
    rows = conn.execute(select(sub).where(sub.c.rn == 1)).mappings().all()
    return {r["code"]: dict(r) for r in rows}


def get_latest_annual_financials_by_code(conn: Connection) -> dict[str, dict[str, Any]]:
    """銘柄ごと最新の通期(FY)行（実績 EPS/BPS 用）を {code: {...}} で返す（ADR-031）。"""
    sub = _latest_financials_subquery(only_with_bps=True)
    rows = conn.execute(select(sub).where(sub.c.rn == 1)).mappings().all()
    return {r["code"]: dict(r) for r in rows}


def upsert_valuation_snapshots(rows: list[dict[str, Any]]) -> int:
    """valuation_snapshots を冪等 UPSERT（code 1 行・最新のみ保持・ADR-002/031）。"""
    return _upsert(valuation_snapshots, rows, index_elements=["code"])


# screen_stocks が受け付ける数値レンジのキー → (列, 比較演算子) の対応
_SCREEN_RANGE_FIELDS = ("per", "pbr", "market_cap", "dividend_yield")
# sort_by に許す列名（外側サブクエリの列）。安全な allowlist。
_SCREEN_SORT_COLS = {
    "per",
    "pbr",
    "market_cap",
    "dividend_yield",
    "per_sector_pctile",
    "market_cap_rank",
    "code",
}


def screen_stocks(conn: Connection, criteria: dict[str, Any]) -> list[dict[str, Any]]:
    """valuation_snapshots × stocks を絞り込み・整列して返す（読み取り時計算・ADR-026/031）。

    業種内パーセンタイル（per_sector_pctile）と時価総額順位（market_cap_rank）は ~4000 行への
    window 関数で都度算出する。criteria は薄い辞書（router が Pydantic から作る）:
      per_min/per_max・pbr_min/pbr_max・market_cap_min/max・dividend_yield_min/max（絶対レンジ）、
      sector33_code・market_code（完全一致）、exclude_etf(bool)、
      per_sector_pctile_max（業種内で安い割合・0..1）、market_cap_rank_max（時価総額 上位 N）、
      sort_by・sort_dir('asc'|'desc')・limit・offset。
    戻り値は素 dict（company_name/sector33_code/market_code/is_etf を stocks から JOIN 補完）。
    """
    v = valuation_snapshots
    s = stocks
    # 内側: スナップショット × 銘柄属性 ＋ window ランク列
    per_sector_pctile = (
        func.percent_rank()
        .over(partition_by=s.c.sector33_code, order_by=v.c.per)
        .label("per_sector_pctile")
    )
    market_cap_rank = (
        func.row_number().over(order_by=v.c.market_cap.desc()).label("market_cap_rank")
    )
    inner = (
        select(
            v.c.code,
            s.c.company_name,
            s.c.sector33_code,
            s.c.market_code,
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
            per_sector_pctile,
            market_cap_rank,
        )
        .select_from(v.join(s, v.c.code == s.c.code))
        .subquery()
    )

    conds = []
    # 絶対レンジ（min/max）
    for field in _SCREEN_RANGE_FIELDS:
        col = inner.c[field]
        lo = criteria.get(f"{field}_min")
        hi = criteria.get(f"{field}_max")
        if lo is not None:
            conds.append(col >= lo)
        if hi is not None:
            conds.append(col <= hi)
    # 完全一致・ETF 除外
    if criteria.get("sector33_code"):
        conds.append(inner.c.sector33_code == criteria["sector33_code"])
    if criteria.get("market_code"):
        conds.append(inner.c.market_code == criteria["market_code"])
    if criteria.get("exclude_etf"):
        conds.append(inner.c.is_etf == 0)
    # ランク系（業種内で安い割合・時価総額 上位 N）
    if criteria.get("per_sector_pctile_max") is not None:
        conds.append(inner.c.per_sector_pctile <= criteria["per_sector_pctile_max"])
    if criteria.get("market_cap_rank_max") is not None:
        conds.append(inner.c.market_cap_rank <= criteria["market_cap_rank_max"])

    stmt = select(inner)
    if conds:
        stmt = stmt.where(and_(*conds))

    # 整列（allowlist・既定は時価総額降順）
    sort_by = criteria.get("sort_by") or "market_cap"
    if sort_by not in _SCREEN_SORT_COLS:
        sort_by = "market_cap"
    sort_col = inner.c[sort_by]
    stmt = stmt.order_by(sort_col.asc() if criteria.get("sort_dir") == "asc" else sort_col.desc())

    limit = int(criteria.get("limit") or 200)
    limit = max(1, min(limit, 1000))  # 暴走防止の上限
    stmt = stmt.limit(limit)
    if criteria.get("offset"):
        stmt = stmt.offset(int(criteria["offset"]))

    return [dict(r) for r in conn.execute(stmt).mappings().all()]


# --- 保存スクリーニング条件（screening_filters・CRUD・ADR-001/031） ---


def list_screening_filters(conn: Connection) -> list[dict[str, Any]]:
    """保存フィルタを更新日時降順で返す（criteria_json は生の文字列・パースは router）。"""
    stmt = select(screening_filters).order_by(screening_filters.c.updated_at.desc())
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def get_screening_filter(conn: Connection, filter_id: int) -> dict[str, Any] | None:
    row = (
        conn.execute(select(screening_filters).where(screening_filters.c.id == filter_id))
        .mappings()
        .first()
    )
    return dict(row) if row else None


def insert_screening_filter(name: str, criteria_json: str) -> int:
    """保存フィルタを 1 件作成し id を返す（W1・単発・自前 begin）。"""
    now = datetime.now(UTC).isoformat()
    with get_engine().begin() as conn:
        result = conn.execute(
            screening_filters.insert().values(
                name=name, criteria_json=criteria_json, created_at=now, updated_at=now
            )
        )
    return int(result.inserted_primary_key[0])


def update_screening_filter(filter_id: int, name: str, criteria_json: str) -> int:
    """保存フィルタを更新（W1）。更新行数を返す（0 なら未存在）。"""
    now = datetime.now(UTC).isoformat()
    with get_engine().begin() as conn:
        result = conn.execute(
            screening_filters.update()
            .where(screening_filters.c.id == filter_id)
            .values(name=name, criteria_json=criteria_json, updated_at=now)
        )
    return int(result.rowcount)


def delete_screening_filter(filter_id: int) -> int:
    """保存フィルタを削除（W1）。削除行数を返す。"""
    with get_engine().begin() as conn:
        result = conn.execute(screening_filters.delete().where(screening_filters.c.id == filter_id))
    return int(result.rowcount)


def upsert_asset_snapshots(rows: list[dict[str, Any]]) -> int:
    """asset_snapshots を冪等 UPSERT する（phase2-spec.md §3.3・ADR-002）。

    衝突キー: date（1 日 1 行）。snapshot_assets ジョブが計算済み行を渡す。
    """
    return _upsert(asset_snapshots, rows, index_elements=["date"])


def get_index_quotes(
    conn: Connection,
    symbol: str,
    from_: str | None = None,
    to: str | None = None,
) -> list[dict[str, Any]]:
    """指定シンボルの index_quotes を date 昇順で返す（backtest のベンチ用・spec §4.4）。"""
    stmt = select(index_quotes).where(index_quotes.c.symbol == symbol)
    if from_:
        stmt = stmt.where(index_quotes.c.date >= from_)
    if to:
        stmt = stmt.where(index_quotes.c.date <= to)
    stmt = stmt.order_by(index_quotes.c.date)
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def get_asset_snapshots(
    conn: Connection,
    limit: int = 365,
) -> list[dict[str, Any]]:
    """asset_snapshots を date 昇順で返す（資産推移トレンド用・spec §5 P2-7）。

    `limit` は最新 N 日分（既定 365 日）。スパークライン用途なので古い日付側を切り捨てる。
    """
    # date 降順で limit 行取ってから date 昇順に並べ直す（最新 N 日を昇順表示）。
    subq = select(asset_snapshots).order_by(asset_snapshots.c.date.desc()).limit(limit).subquery()
    stmt = select(subq).order_by(subq.c.date)
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def list_holding_codes(conn: Connection, portfolio_id: int) -> list[str]:
    """holdings の code 一覧を返す（fetch_financials の対象銘柄特定用・spec §3.2）。"""
    rows = (
        conn.execute(
            select(holdings.c.code)
            .where(holdings.c.portfolio_id == portfolio_id)
            .order_by(holdings.c.code)
        )
        .scalars()
        .all()
    )
    return list(rows)


# ===== Phase 2: portfolios / transactions / holdings / cash / external_assets =====
# （phase2-spec.md §5・ADR-001・ADR-002・ADR-019）


def list_portfolios(conn: Connection) -> list[dict[str, Any]]:
    """portfolios を portfolio_id 昇順で返す（spec P2-1）。

    先頭行が既定ポートフォリオとなる（裁定 L-9: id 固定にしない）。
    """
    stmt = select(portfolios).order_by(portfolios.c.portfolio_id)
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def insert_transaction(conn: Connection, row: dict[str, Any]) -> int:
    """transactions に 1 行挿入し、発行された id を返す（spec P2-2・ADR-002）。

    row には portfolio_id/code/side/shares/price/fee/traded_at を含める。
    commit はしない。取引記録と holdings 再導出を atomic にするため、呼び出し側が
    `with get_engine().begin()` で境界を所有する（ADR-019）。
    """
    stmt = transactions.insert().values(**row)
    result = conn.execute(stmt)
    return int(result.lastrowid)


def list_transactions(conn: Connection, portfolio_id: int) -> list[dict[str, Any]]:
    """portfolio_id の transactions を traded_at 昇順で返す（spec P2-2・ADR-019）。

    holdings 再計算で時系列順に適用するため昇順取得する。
    """
    stmt = (
        select(transactions)
        .where(transactions.c.portfolio_id == portfolio_id)
        .order_by(transactions.c.traded_at, transactions.c.id)
    )
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def replace_holdings(conn: Connection, portfolio_id: int, rows: list[dict[str, Any]]) -> None:
    """portfolio の holdings を入れ替える（削除 + 一括挿入・ADR-019）。

    rows には portfolio_id/code/shares/avg_cost を含める。shares > 0 の行のみ渡すこと。
    commit はしない。transactions と同じトランザクションで呼び、中間状態が見えないようにする。
    """
    conn.execute(holdings.delete().where(holdings.c.portfolio_id == portfolio_id))
    if rows:
        conn.execute(holdings.insert(), rows)


def list_holdings(conn: Connection, portfolio_id: int) -> list[dict[str, Any]]:
    """holdings を stocks に LEFT JOIN して company_name・sector33_code 付きで返す（spec P2-2）。

    holdings JOIN stocks で company_name と sector33_code を補完する
    （行レベルに名前を焼かない流儀）。
    """
    stmt = (
        select(
            holdings.c.id,
            holdings.c.portfolio_id,
            holdings.c.code,
            stocks.c.company_name,
            stocks.c.sector33_code,
            holdings.c.shares,
            holdings.c.avg_cost,
        )
        .select_from(holdings.outerjoin(stocks, holdings.c.code == stocks.c.code))
        .where(holdings.c.portfolio_id == portfolio_id)
        .order_by(holdings.c.code)
    )
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def get_cash(conn: Connection) -> dict[str, Any] | None:
    """cash テーブルの先頭行（1 行のみ運用）を返す。存在しない場合は None（spec P2-3）。"""
    stmt = select(cash).order_by(cash.c.id).limit(1)
    row = conn.execute(stmt).mappings().first()
    return dict(row) if row else None


def upsert_cash(balance: float) -> dict[str, Any]:
    """cash を更新（先頭行があれば更新・なければ挿入）し、更新後行を返す（spec P2-3・ADR-002）。

    1 行のみ運用。id=1 への INSERT OR REPLACE で冪等にする。
    """
    updated_at = datetime.now(UTC).isoformat()
    # SQLite の INSERT OR REPLACE で id=1 行を upsert する（単一行運用）
    stmt = sqlite_insert(cash).values(id=1, balance=balance, updated_at=updated_at)
    update_cols = {"balance": stmt.excluded["balance"], "updated_at": stmt.excluded["updated_at"]}
    stmt = stmt.on_conflict_do_update(index_elements=["id"], set_=update_cols)
    with get_engine().begin() as conn:
        conn.execute(stmt)
    # 更新後行を返す（engine から再取得して戻す）
    with get_engine().connect() as conn:
        row = conn.execute(select(cash).where(cash.c.id == 1)).mappings().first()
    return dict(row) if row else {"id": 1, "balance": balance, "updated_at": updated_at}


def list_external_assets(conn: Connection) -> list[dict[str, Any]]:
    """external_assets を id 昇順で返す（spec P2-4）。"""
    stmt = select(external_assets).order_by(external_assets.c.id)
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def insert_external_asset(row: dict[str, Any]) -> int:
    """external_assets に 1 行挿入し id を返す（spec P2-4・ADR-002）。"""
    stmt = external_assets.insert().values(**row)
    with get_engine().begin() as conn:
        result = conn.execute(stmt)
    return int(result.lastrowid)


def update_external_asset(asset_id: int, row: dict[str, Any]) -> dict[str, Any] | None:
    """external_assets の id 行を更新し、更新後行を返す。存在しない場合は None（spec P2-4）。"""
    stmt = external_assets.update().where(external_assets.c.id == asset_id).values(**row)
    with get_engine().begin() as conn:
        result = conn.execute(stmt)
        if result.rowcount == 0:
            return None
    with get_engine().connect() as conn:
        updated = (
            conn.execute(select(external_assets).where(external_assets.c.id == asset_id))
            .mappings()
            .first()
        )
    return dict(updated) if updated else None


def delete_external_asset(asset_id: int) -> bool:
    """external_assets の id 行を削除し、削除できたか bool を返す（spec P2-4）。"""
    stmt = external_assets.delete().where(external_assets.c.id == asset_id)
    with get_engine().begin() as conn:
        result = conn.execute(stmt)
    return result.rowcount > 0


def get_latest_closes(conn: Connection, codes: list[str]) -> dict[str, dict[str, Any]]:
    """各 code の MAX(date) の close を返す（holdings 評価額計算用・spec P2-2）。

    返却: {code: {"date": str, "close": float}}。
    close が存在しない code はキー自体が含まれない。
    """
    if not codes:
        return {}

    # サブクエリで各 code の最新 date を取り、本クエリで close を引く
    from sqlalchemy import and_

    subq = (
        select(daily_quotes.c.code, func.max(daily_quotes.c.date).label("max_date"))
        .where(daily_quotes.c.code.in_(codes))
        .group_by(daily_quotes.c.code)
        .subquery()
    )
    stmt = select(
        daily_quotes.c.code,
        daily_quotes.c.date,
        daily_quotes.c.close,
    ).join(
        subq,
        and_(
            daily_quotes.c.code == subq.c.code,
            daily_quotes.c.date == subq.c.max_date,
        ),
    )
    result: dict[str, dict[str, Any]] = {}
    for r in conn.execute(stmt).mappings().all():
        result[r["code"]] = {"date": r["date"], "close": r["close"]}
    return result


def get_max_daily_date(conn: Connection) -> str | None:
    """daily_quotes の MAX(date)（as_of の鮮度確認用・spec P2-2）。

    既存 get_max_quote_date の別名（holdings/assets ルータ向け）。
    """
    return get_max_quote_date(conn)


# ===== Phase 3: AI Advisor 状態（phase3-spec.md §8.3・ADR-011/013/018/028/029） =====
#
# [書き込みのトランザクション規律] 以下の write 関数は引数の `conn` 上で execute するだけで、
# commit はしない。呼び出し側（service.py / ルータ）が `with get_engine().begin() as conn:` で
# 包むこと（複数 write を 1 トランザクションで原子化するため＝policy 更新＋journal snapshot 等）。
# read 関数は get_conn()（engine.connect）でも begin() でも動く。


def get_policy(conn: Connection) -> dict[str, Any] | None:
    """policy の 1 行を素の dict で返す（無ければ None・spec §8.3）。

    値の変換（no_leverage int↔bool・sector_caps/exclusions JSON↔型）はルータ層の責務。
    既定値のマージは services/policy.py（DEFAULT_POLICY）が担う（本関数は生の行のみ）。
    """
    row = conn.execute(select(policy).order_by(policy.c.id).limit(1)).mappings().first()
    return dict(row) if row else None


def upsert_policy(conn: Connection, fields: dict[str, Any]) -> None:
    """policy を 1 行運用で upsert する（id 固定・ADR-013・spec §8.3）。

    fields は変更したい列のみ（部分更新可）。id は常に 1 に固定する。
    `updated_at` は呼び出し側で詰めても良いが、未指定なら UTC now を入れる。
    """
    payload = {k: v for k, v in fields.items() if k != "id"}
    payload.setdefault("updated_at", datetime.now(UTC).isoformat())
    stmt = sqlite_insert(policy).values(id=1, **payload)
    update_cols = {col: stmt.excluded[col] for col in payload}
    stmt = stmt.on_conflict_do_update(index_elements=["id"], set_=update_cols)
    conn.execute(stmt)


def insert_journal(conn: Connection, **fields: Any) -> int:
    """advisor_journal に 1 行挿入し、発行された id を返す（spec §8.3・ADR-029）。

    fields: date / source / situation_briefing / observations / proposal /
    proposed_policy_change / policy_snapshot / llm_model / created_at。
    JSON 列（situation_briefing 等）は呼び出し側で json.dumps 済みの文字列を渡す。
    """
    fields.setdefault("created_at", datetime.now(UTC).isoformat())
    fields.setdefault("source", "nightly")
    result = conn.execute(advisor_journal.insert().values(**fields))
    return int(result.lastrowid)


def get_journal(conn: Connection, journal_id: int) -> dict[str, Any] | None:
    """advisor_journal の 1 行を返す（situation_briefing 込み・GET /journal/{id}・spec §8.2）。"""
    row = (
        conn.execute(select(advisor_journal).where(advisor_journal.c.id == journal_id))
        .mappings()
        .first()
    )
    return dict(row) if row else None


def list_journal(
    conn: Connection, from_: str | None = None, to: str | None = None
) -> list[dict[str, Any]]:
    """advisor_journal を date 降順で返す（spec §8.2）。

    重い situation_briefing は一覧では返さない（必要なら get_journal で別途取得）。
    """
    cols = [
        advisor_journal.c.id,
        advisor_journal.c.date,
        advisor_journal.c.source,
        advisor_journal.c.observations,
        advisor_journal.c.proposal,
        advisor_journal.c.proposed_policy_change,
        advisor_journal.c.policy_snapshot,
        advisor_journal.c.llm_model,
        advisor_journal.c.created_at,
    ]
    stmt = select(*cols)
    if from_:
        stmt = stmt.where(advisor_journal.c.date >= from_)
    if to:
        stmt = stmt.where(advisor_journal.c.date <= to)
    stmt = stmt.order_by(advisor_journal.c.date.desc(), advisor_journal.c.id.desc())
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def get_recent_journal_summary(conn: Connection, n: int = 1) -> str | None:
    """直近 n 件の journal observations を連結した要約文を返す（文脈・連続性・spec §8.3）。

    プロンプトの「直近の投資日記」層に差すための軽い文字列。無ければ None。
    """
    stmt = (
        select(advisor_journal.c.date, advisor_journal.c.observations)
        .order_by(advisor_journal.c.date.desc(), advisor_journal.c.id.desc())
        .limit(n)
    )
    rows = conn.execute(stmt).mappings().all()
    if not rows:
        return None
    parts = [f"{r['date']}: {r['observations']}" for r in rows if r["observations"]]
    return "\n".join(parts) if parts else None


def insert_proposal(conn: Connection, **fields: Any) -> int:
    """proposals に 1 行挿入し id を返す（spec §8.3・ADR-001/019）。

    fields: created_date / kind / body / rationale / status / outcome /
    journal_id / depends_on。body は呼び出し側で json.dumps 済みの文字列。
    """
    fields.setdefault("status", "pending")
    result = conn.execute(proposals.insert().values(**fields))
    return int(result.lastrowid)


def list_proposals(conn: Connection, status: str | None = None) -> list[dict[str, Any]]:
    """proposals を created_date 降順で返す（status 指定で絞り込み・spec §8.2）。"""
    stmt = select(proposals)
    if status:
        stmt = stmt.where(proposals.c.status == status)
    stmt = stmt.order_by(proposals.c.created_date.desc(), proposals.c.id.desc())
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def get_proposal(conn: Connection, proposal_id: int) -> dict[str, Any] | None:
    """proposals の 1 行を返す（無ければ None・spec §8.3）。"""
    row = conn.execute(select(proposals).where(proposals.c.id == proposal_id)).mappings().first()
    return dict(row) if row else None


def update_proposal_status(
    conn: Connection,
    proposal_id: int,
    status: str,
    outcome: str | None = None,
    resolved_at: str | None = None,
) -> None:
    """proposals.status を遷移する（approved/rejected・spec §8.3）。

    resolved_at 未指定なら UTC now を入れる。outcome は任意。
    """
    values: dict[str, Any] = {
        "status": status,
        "resolved_at": resolved_at or datetime.now(UTC).isoformat(),
    }
    if outcome is not None:
        values["outcome"] = outcome
    conn.execute(proposals.update().where(proposals.c.id == proposal_id).values(**values))


# --- llm_usage（LLM コストガードレール台帳・ADR-028・spec §7.1） ---


def insert_llm_usage(conn: Connection, **fields: Any) -> int:
    """llm_usage に 1 行（per-call）積む（ADR-028・spec §7.1）。

    fields: created_at / source / model / tokens_in / tokens_out / cost_usd。
    cost_usd は OpenRouter の usage.cost。Ollama は 0。
    """
    fields.setdefault("created_at", datetime.now(UTC).isoformat())
    fields.setdefault("cost_usd", 0.0)
    result = conn.execute(llm_usage.insert().values(**fields))
    return int(result.lastrowid)


def sum_llm_cost_month(conn: Connection, year_month: str) -> float:
    """指定年月（'YYYY-MM'）の cost_usd 合計を返す（当月ガード判定・spec §7.1）。

    created_at（ISO8601）の先頭 7 文字でマッチする。行が無ければ 0.0。
    """
    stmt = select(func.coalesce(func.sum(llm_usage.c.cost_usd), 0.0)).where(
        llm_usage.c.created_at.like(f"{year_month}%")
    )
    return float(conn.execute(stmt).scalar() or 0.0)


# ===== Phase 4: Stock Dossier（phase4-spec.md §2/§3・ADR-020） =====

# 書き込み規約: upsert_dossier / upsert_dossier_source は investigate_stock パイプラインが
# 複数ソース＋ドシエ本体を 1 トランザクションに束ねて atomic に書くため、conn を受け取り
# commit はしない（W2・呼び出し側が `with get_engine().begin() as conn:` で境界を所有）。
# 一方 watchlist の add/remove は API からの単発書き込みなので repo が自前で begin する（W1）。


def upsert_dossier(
    conn: Connection,
    *,
    code: str,
    summary_md: str | None,
    key_facts: str | None,
    last_investigated_at: str | None,
    updated_at: str | None,
) -> None:
    """stock_dossiers を 1 銘柄 1 行で UPSERT する（code 衝突で更新・ADR-020/ADR-002・spec §2.2）。

    living document なので code conflict は do_update（summary_md 等を上書きしていく）。
    key_facts は呼び出し側で json.dumps 済みの文字列を渡す（JSON 化/パースは router/service）。
    commit はしない。呼び出し側が `with get_engine().begin() as conn:` で境界を所有する（W2）。
    """
    stmt = sqlite_insert(stock_dossiers).values(
        code=code,
        summary_md=summary_md,
        key_facts=key_facts,
        last_investigated_at=last_investigated_at,
        updated_at=updated_at,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["code"],
        set_={
            "summary_md": stmt.excluded.summary_md,
            "key_facts": stmt.excluded.key_facts,
            "last_investigated_at": stmt.excluded.last_investigated_at,
            "updated_at": stmt.excluded.updated_at,
        },
    )
    conn.execute(stmt)


def upsert_dossier_source(
    conn: Connection,
    *,
    code: str,
    url: str,
    title: str | None = None,
    summary: str | None = None,
    published_at: str | None = None,
    source_type: str | None = None,
    processed_at: str | None = None,
    extraction_status: str | None = None,
) -> None:
    """dossier_sources に 1 ソースを取り込む（url 衝突なら skip・ADR-020/ADR-002・spec §2.3）。

    本文は保存せず summary と url のみ（ADR-020）。既定は「既存 url なら skip」＝
    on_conflict_do_nothing（spec §2.3 の「実装は『既存なら skip』を既定」に従う）。
    processed_at 未指定なら UTC now を入れる。
    extraction_status は取得レベル（'summarized'/'description'/'headline'）＝本文取得の成否を
    記録する（NewsAdapter の 3 段フォールバックのどの段まで届いたか・計画/ADR-020）。
    commit はしない。呼び出し側が `with get_engine().begin() as conn:` で境界を所有する（W2）。
    """
    stmt = sqlite_insert(dossier_sources).values(
        code=code,
        source_type=source_type,
        url=url,
        title=title,
        summary=summary,
        published_at=published_at,
        processed_at=processed_at or datetime.now(UTC).isoformat(),
        extraction_status=extraction_status,
    )
    stmt = stmt.on_conflict_do_nothing(index_elements=["url"])  # 既存 url は無視（skip）
    conn.execute(stmt)


def dossier_source_exists(conn: Connection, url: str) -> bool:
    """url が dossier_sources に既存か返す（URL 重複排除の存在確認・spec §3）。"""
    stmt = select(dossier_sources.c.id).where(dossier_sources.c.url == url).limit(1)
    return conn.execute(stmt).first() is not None


def get_dossier(conn: Connection, code: str) -> dict[str, Any] | None:
    """stock_dossiers の 1 行を素の dict で返す（無ければ None・spec §2.2/§5.2）。

    key_facts（JSON TEXT）はパースせず生のまま返す（json.loads は router の責務）。
    sources の JOIN はしない（一覧は list_dossier_sources で別途取得）。
    """
    row = (
        conn.execute(select(stock_dossiers).where(stock_dossiers.c.code == code)).mappings().first()
    )
    return dict(row) if row else None


def list_dossier_sources(conn: Connection, code: str) -> list[dict[str, Any]]:
    """指定銘柄の dossier_sources を published_at 降順で返す（ソース台帳・spec §5.2）。

    本文は持たない（summary と url のみ＝ADR-020）。published_at が NULL の行は末尾に寄る。
    """
    stmt = (
        select(dossier_sources)
        .where(dossier_sources.c.code == code)
        .order_by(dossier_sources.c.published_at.desc(), dossier_sources.c.id.desc())
    )
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


# ===== ADR-034: 一般ニュース台帳（銘柄に紐づかない別系統・general_news） =====

# 書き込み規約: upsert_general_news は夜間ジョブ fetch_general_news が全カテゴリの記事を
# 1 トランザクションに束ねて atomic に書くため、conn を受け取り commit はしない（W2・
# 呼び出し側が `with get_engine().begin() as conn:` で境界を所有＝upsert_dossier_source と同じ）。


def upsert_general_news(conn: Connection, rows: list[dict[str, Any]]) -> int:
    """一般ニュース記事を一括 UPSERT する（url 衝突なら skip・ADR-034/ADR-002）。

    本文は保存せず summary と url のみ（ADR-020 の流儀）。既定は「既存 url なら skip」＝
    on_conflict_do_nothing（再取得の二重取り込みを防ぐ冪等キー）。fetched_at 未指定行は
    UTC now を補う。返り値は受理を試みた行数（skip 含む・呼び出し側のログ用）。
    commit はしない。呼び出し側が `with get_engine().begin() as conn:` で境界を所有する（W2）。
    """
    if not rows:
        return 0
    now_iso = datetime.now(UTC).isoformat()
    for row in rows:
        stmt = sqlite_insert(general_news).values(
            category=row.get("category"),
            url=row["url"],
            title=row.get("title"),
            summary=row.get("summary"),
            published_at=row.get("published_at"),
            fetched_at=row.get("fetched_at") or now_iso,
            source_type=row.get("source_type"),
            extraction_status=row.get("extraction_status"),
        )
        stmt = stmt.on_conflict_do_nothing(index_elements=["url"])  # 既存 url は無視（skip）
        conn.execute(stmt)
    return len(rows)


def list_general_news(conn: Connection, *, since: str | None = None) -> list[dict[str, Any]]:
    """一般ニュースを発行日降順で返す（ADR-034・widget と Tool が消費）。

    since 指定（'YYYY-MM-DD'）で `published_at >= since` に絞る（直近分のみ眺める用途）。
    published_at が NULL の行は末尾へ寄る。本文は持たない（summary と url のみ＝ADR-020）。
    """
    stmt = select(general_news)
    if since is not None:
        stmt = stmt.where(general_news.c.published_at >= since)
    stmt = stmt.order_by(general_news.c.published_at.desc(), general_news.c.id.desc())
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def list_watchlist(conn: Connection) -> list[dict[str, Any]]:
    """watchlist を company_name・last_investigated_at 付きで返す（spec §2.1/§5.1）。

    company_name は stocks JOIN、last_investigated_at は stock_dossiers LEFT JOIN で補う
    （行レベルに焼かず読むときに結合＝repo 規約）。dossier 未作成の銘柄は
    last_investigated_at が None で返る。stale 判定は per-row の interval_days 基準（ADR-033）で
    上位（router/service）が行う。
    """
    stmt = (
        select(
            watchlist.c.id,
            watchlist.c.code,
            stocks.c.company_name,
            watchlist.c.note,
            watchlist.c.added_at,
            watchlist.c.interval_days,
            stock_dossiers.c.last_investigated_at,
        )
        .select_from(
            watchlist.outerjoin(stocks, watchlist.c.code == stocks.c.code).outerjoin(
                stock_dossiers, watchlist.c.code == stock_dossiers.c.code
            )
        )
        .order_by(watchlist.c.id)
    )
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def add_watchlist(code: str, note: str | None = None, interval_days: int = 21) -> dict[str, Any]:
    """watchlist に 1 銘柄を追加し、その行（dict）を返す（spec §5.1・ADR-002/ADR-033）。

    UNIQUE(code) 衝突時は do_nothing（既存を重複として扱う＝spec §5.1）。挿入でも既存でも
    最終的に code に対応する行を読み直して返す（重複時も既存行を返す）。重複の扱い
    （メッセージ等）は router の責務。added_at 未指定なら UTC now を入れる。
    interval_days は銘柄ごとの調査間隔（既定 21・stale 起点＝ADR-033）。
    """
    added_at = datetime.now(UTC).isoformat()
    stmt = sqlite_insert(watchlist).values(
        code=code, note=note, added_at=added_at, interval_days=interval_days
    )
    stmt = stmt.on_conflict_do_nothing(index_elements=["code"])
    with get_engine().begin() as conn:
        conn.execute(stmt)
    with get_engine().connect() as conn:
        row = conn.execute(select(watchlist).where(watchlist.c.code == code)).mappings().first()
    return dict(row) if row else {}


def set_watchlist_interval(code: str, interval_days: int) -> None:
    """watchlist の銘柄の調査間隔（interval_days）を更新する（ADR-033・間隔設定 UI/PATCH）。

    code で UPDATE する単発の単純な書き込み（1 文で閉じる）なので repo が自前で begin する
    （W1・add/remove_watchlist と同じ流儀）。存在しない code は影響行 0 で静かに終わる
    （存在確認・エラー化は router の責務）。
    """
    with get_engine().begin() as conn:
        conn.execute(
            watchlist.update().where(watchlist.c.code == code).values(interval_days=interval_days)
        )


def remove_watchlist(watchlist_id: int) -> None:
    """watchlist の id 行を削除する（spec §5.1・DELETE /watchlist/{id}）。"""
    with get_engine().begin() as conn:
        conn.execute(watchlist.delete().where(watchlist.c.id == watchlist_id))


# ===== Phase 6: Signal Beacon（phase6-spec.md §2/§3・ADR-007/018・0010_notifications） =====


def notification_exists(conn: Connection, notify_key: str, channel: str) -> bool:
    """notify_key が既に送信済みか返す（二重送信防止の存在確認・spec §3）。

    send_once が送信前にこれを見て True なら送らない（冪等＝ADR-002/018）。
    """
    stmt = (
        select(notifications.c.notify_key)
        .where(notifications.c.notify_key == notify_key)
        .where(notifications.c.channel == channel)
        .limit(1)
    )
    return conn.execute(stmt).first() is not None


def record_notification(notify_key: str, channel: str, sent_at: str) -> None:
    """送信済みを記録する（spec §3・冪等 UPSERT）。

    (notify_key, channel) 衝突時は sent_at を更新するだけ（再記録は実質 no-op）。単発の書き込みは
    repo が自前で begin する（W1・add_watchlist と同じ流儀）。送信成功 → 記録の間で落ちると稀に
    再送するが、digest は同日同キーなので翌実行で重複しない（at-least-once 受容・spec §3 注）。
    """
    _upsert(
        notifications,
        [{"notify_key": notify_key, "channel": channel, "sent_at": sent_at}],
        index_elements=["notify_key", "channel"],
    )


def list_signals_for_alert(conn: Connection, date: str) -> list[dict[str, Any]]:
    """指定日の signals を stocks に LEFT JOIN し company_name 付きで全件返す（spec §3・⑧の素）。

    notify_digest がこの全件を Python 側で閾値（score/payload.ratio）抽出 → score 降順 Top N に
    絞る（AI に計算させない＝ADR-014/016）。`payload` は生の TEXT 文字列のまま返す（json.loads は
    ジョブの責務）。並び順は score 降順（ジョブの Top N 切り詰めに合わせる）。
    """
    stmt = (
        select(
            signals.c.code,
            stocks.c.company_name,
            signals.c.signal_type,
            signals.c.score,
            signals.c.payload,
            signals.c.date,
        )
        .select_from(signals.outerjoin(stocks, signals.c.code == stocks.c.code))
        .where(signals.c.date == date)
        .order_by(signals.c.score.desc())
    )
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def get_journal_for_date(conn: Connection, date: str) -> dict[str, Any] | None:
    """指定日の夜の分析AI journal（最新 1 行）を返す（当日提案プッシュの素・spec §3）。

    proposal / proposed_policy_change を digest に要約引用する（Phase 3 生成済み文をそのまま使う
    ＝AI に再計算させない・ADR-014/016）。source='nightly' を優先し、同日複数あれば id 降順で
    最新を採る。無ければ None。
    """
    stmt = (
        select(advisor_journal)
        .where(advisor_journal.c.date == date)
        .where(advisor_journal.c.source == "nightly")
        .order_by(advisor_journal.c.id.desc())
        .limit(1)
    )
    row = conn.execute(stmt).mappings().first()
    return dict(row) if row else None
