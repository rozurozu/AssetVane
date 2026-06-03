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
from app.db.schema import daily_quotes, fetch_meta, signals, stocks


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
