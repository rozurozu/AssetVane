"""クエリ（SQLAlchemy Core）。

書き込みは PK 衝突時更新の UPSERT で冪等にする（再取得で重複しない＝Phase 0 完了条件・ADR-002）。
読み取りは API ルータから呼ぶ。戻り値は素の dict（ルータ側で Pydantic に詰める）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, Table, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.engine import get_engine
from app.db.schema import (
    asset_snapshots,
    cash,
    daily_quotes,
    external_assets,
    fetch_meta,
    financials,
    holdings,
    index_quotes,
    portfolios,
    signals,
    stocks,
    transactions,
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


def insert_transaction(row: dict[str, Any]) -> int:
    """transactions に 1 行挿入し、発行された id を返す（spec P2-2・ADR-002）。

    row には portfolio_id/code/side/shares/price/fee/traded_at を含める。
    書き込みは engine.begin() トランザクション内（ADR-002）。
    """
    stmt = transactions.insert().values(**row)
    with get_engine().begin() as conn:
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


def replace_holdings(portfolio_id: int, rows: list[dict[str, Any]]) -> None:
    """portfolio の holdings を入れ替える（削除 + 一括挿入・ADR-019）。

    rows には portfolio_id/code/shares/avg_cost を含める。shares > 0 の行のみ渡すこと。
    DELETE + INSERT をトランザクションで包み、中間状態が見えないようにする（ADR-002）。
    """
    with get_engine().begin() as conn:
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
    from datetime import UTC, datetime

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
