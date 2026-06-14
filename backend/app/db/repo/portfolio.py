"""portfolios/transactions/holdings/cash/external_assets（Phase 2・ADR-001/002/019）。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, and_, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.engine import get_engine
from app.db.schema import (
    cash,
    daily_quotes,
    external_assets,
    holdings,
    portfolios,
    stocks,
    transactions,
)

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


def get_transaction(conn: Connection, txn_id: int) -> dict[str, Any] | None:
    """transactions の 1 行を id で引く。存在しなければ None（spec P2-2・ADR-019）。

    編集・削除の存在確認と、所属 portfolio_id の取得に使う。読み取りなので commit しない。
    """
    stmt = select(transactions).where(transactions.c.id == txn_id)
    row = conn.execute(stmt).mappings().first()
    return dict(row) if row else None


def update_transaction(conn: Connection, txn_id: int, row: dict[str, Any]) -> None:
    """transactions の id 行を更新する（spec P2-2・ADR-019）。

    row には code/side/shares/price/fee/traded_at を含める（portfolio_id は不変）。
    commit はしない。取引更新と holdings 再導出を atomic にするため、呼び出し側が
    `with get_engine().begin()` で境界を所有する（ADR-019）。
    """
    stmt = transactions.update().where(transactions.c.id == txn_id).values(**row)
    conn.execute(stmt)


def delete_transaction(conn: Connection, txn_id: int) -> None:
    """transactions の id 行を削除する（spec P2-2・ADR-019）。

    commit はしない。取引削除と holdings 再導出を atomic にするため、呼び出し側が
    `with get_engine().begin()` で境界を所有する（ADR-019）。
    """
    conn.execute(transactions.delete().where(transactions.c.id == txn_id))


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
