"""投資信託（取引→保有の本格管理・ADR-054）。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, and_, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.engine import get_engine
from app.db.repo._common import _upsert
from app.db.schema import (
    fund_holdings,
    fund_navs,
    fund_transactions,
    funds,
)

# ===== 投資信託（ADR-054: 専用テーブル・株と同じ「取引→導出」構造で本格管理） =====
#
# [単位の約束] nav / price / avg_cost はすべて「10,000 口あたりの円」。評価額は
# units/10000 * nav で算出する（services 側で換算・data-model.md §投資信託）。
# [書き込みのトランザクション規律] fund_transactions の write 関数（insert/update/delete）と
# replace_fund_holdings は引数の `conn` 上で execute するだけで commit しない。呼び出し側
# （routers/funds.py）が `with get_engine().begin() as conn:` で包み、取引記録と
# fund_holdings 再導出を 1 トランザクションに原子化する（W2・ADR-019/054）。一方 funds マスタと
# fund_navs の UPSERT は単発・冪等なので repo 自前 begin（W1・ADR-002）。


def list_funds(conn: Connection) -> list[dict[str, Any]]:
    """funds マスタを isin 昇順で返す（ADR-054）。読み取りなので commit しない。"""
    stmt = select(funds).order_by(funds.c.isin)
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def upsert_fund(isin: str, name: str, assoc_code: str | None = None) -> dict[str, Any]:
    """funds マスタを 1 件 UPSERT し、更新後行を返す（ADR-054・ADR-002）。

    isin 衝突時は name/assoc_code/updated_at を更新する冪等 UPSERT（W1・自前 begin）。
    updated_at は本関数で UTC now を入れる。

    協会コード（assoc_code）は NAV 取得に必須（投信総合検索ライブラリー associFundCd が無いと
    NAV CSV が空になる）。シグネチャは既定 None を維持するが、未指定の弾き（422）は router 層
    （FundIn の必須検証）が担う。
    """
    updated_at = datetime.now(UTC).isoformat()
    stmt = sqlite_insert(funds).values(
        isin=isin, name=name, assoc_code=assoc_code, updated_at=updated_at
    )
    update_cols = {
        "name": stmt.excluded["name"],
        "assoc_code": stmt.excluded["assoc_code"],
        "updated_at": stmt.excluded["updated_at"],
    }
    stmt = stmt.on_conflict_do_update(index_elements=["isin"], set_=update_cols)
    with get_engine().begin() as conn:
        conn.execute(stmt)
    with get_engine().connect() as conn:
        row = conn.execute(select(funds).where(funds.c.isin == isin)).mappings().first()
    return (
        dict(row)
        if row
        else {"isin": isin, "name": name, "assoc_code": assoc_code, "updated_at": updated_at}
    )


def delete_fund(isin: str) -> bool:
    """funds マスタの 1 件を削除し、削除できたか bool を返す（ADR-054）。"""
    with get_engine().begin() as conn:
        result = conn.execute(funds.delete().where(funds.c.isin == isin))
    return result.rowcount > 0


def upsert_fund_navs(rows: list[dict[str, Any]]) -> int:
    """fund_navs を冪等 UPSERT する（ADR-054・ADR-002）。

    衝突キー: (isin, date)。rows は {isin, date, nav}。再取得しても重複しない（W1・自前 begin）。
    """
    return _upsert(fund_navs, rows, index_elements=["isin", "date"])


def get_latest_fund_navs(conn: Connection, isins: list[str]) -> dict[str, dict[str, Any]]:
    """各 isin の MAX(date) の nav を返す（投信評価額計算用・ADR-054）。

    返却: {isin: {"date": str, "nav": float}}。空 isins は空 dict。
    nav が存在しない isin はキー自体が含まれない（get_latest_closes と同方針）。
    """
    if not isins:
        return {}

    subq = (
        select(fund_navs.c.isin, func.max(fund_navs.c.date).label("max_date"))
        .where(fund_navs.c.isin.in_(isins))
        .group_by(fund_navs.c.isin)
        .subquery()
    )
    stmt = select(fund_navs.c.isin, fund_navs.c.date, fund_navs.c.nav).join(
        subq,
        and_(fund_navs.c.isin == subq.c.isin, fund_navs.c.date == subq.c.max_date),
    )
    result: dict[str, dict[str, Any]] = {}
    for r in conn.execute(stmt).mappings().all():
        result[r["isin"]] = {"date": r["date"], "nav": r["nav"]}
    return result


def get_fund_nav_series(conn: Connection, isin: str, limit: int = 365) -> list[dict[str, Any]]:
    """指定 isin の {date, nav} を date 昇順・最新 limit 件で返す（ADR-054）。

    NAV 推移チャート用。date 降順で limit 行取ってから date 昇順に並べ直す
    （get_asset_snapshots と同方針＝最新 N 日を昇順表示）。
    """
    subq = (
        select(fund_navs.c.date, fund_navs.c.nav)
        .where(fund_navs.c.isin == isin)
        .order_by(fund_navs.c.date.desc())
        .limit(limit)
        .subquery()
    )
    stmt = select(subq).order_by(subq.c.date)
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def list_fund_transactions(conn: Connection, portfolio_id: int) -> list[dict[str, Any]]:
    """portfolio_id の fund_transactions を traded_at 昇順で返す（ADR-054・ADR-019）。

    fund_holdings 再計算で時系列順に適用するため昇順取得する（list_transactions と同方針）。
    """
    stmt = (
        select(fund_transactions)
        .where(fund_transactions.c.portfolio_id == portfolio_id)
        .order_by(fund_transactions.c.traded_at, fund_transactions.c.id)
    )
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def get_fund_transaction(conn: Connection, txn_id: int) -> dict[str, Any] | None:
    """fund_transactions の 1 行を id で引く。存在しなければ None（ADR-054・ADR-019）。

    編集・削除の存在確認と、所属 portfolio_id の取得に使う。読み取りなので commit しない。
    """
    stmt = select(fund_transactions).where(fund_transactions.c.id == txn_id)
    row = conn.execute(stmt).mappings().first()
    return dict(row) if row else None


def insert_fund_transaction(conn: Connection, row: dict[str, Any]) -> dict[str, Any]:
    """fund_transactions に 1 行挿入し、発行行を返す（ADR-054・ADR-019）。

    row には portfolio_id/isin/side/units/price/fee/traded_at を含める。
    commit はしない。取引記録と fund_holdings 再導出を atomic にするため、呼び出し側が
    `with get_engine().begin()` で境界を所有する（W2・ADR-019）。
    """
    result = conn.execute(fund_transactions.insert().values(**row))
    txn_id = int(result.lastrowid)
    inserted = (
        conn.execute(select(fund_transactions).where(fund_transactions.c.id == txn_id))
        .mappings()
        .first()
    )
    return dict(inserted) if inserted else {"id": txn_id, **row}


def update_fund_transaction(
    conn: Connection, txn_id: int, row: dict[str, Any]
) -> dict[str, Any] | None:
    """fund_transactions の id 行を更新し、更新後行を返す。存在しなければ None（ADR-054）。

    row には isin/side/units/price/fee/traded_at を含める（portfolio_id は不変）。
    commit はしない。取引更新と fund_holdings 再導出を atomic にするため、呼び出し側が
    `with get_engine().begin()` で境界を所有する（W2・ADR-019）。
    """
    result = conn.execute(
        fund_transactions.update().where(fund_transactions.c.id == txn_id).values(**row)
    )
    if result.rowcount == 0:
        return None
    updated = (
        conn.execute(select(fund_transactions).where(fund_transactions.c.id == txn_id))
        .mappings()
        .first()
    )
    return dict(updated) if updated else None


def delete_fund_transaction(conn: Connection, txn_id: int) -> bool:
    """fund_transactions の id 行を削除し、削除できたか bool を返す（ADR-054・ADR-019）。

    commit はしない。取引削除と fund_holdings 再導出を atomic にするため、呼び出し側が
    `with get_engine().begin()` で境界を所有する（W2・ADR-019）。
    """
    result = conn.execute(fund_transactions.delete().where(fund_transactions.c.id == txn_id))
    return result.rowcount > 0


def replace_fund_holdings(conn: Connection, portfolio_id: int, rows: list[dict[str, Any]]) -> None:
    """portfolio の fund_holdings を入れ替える（削除 + 一括挿入・ADR-019/054）。

    rows には portfolio_id/isin/units/avg_cost を含める。units > 0 の行のみ渡すこと。
    commit はしない。fund_transactions と同じトランザクションで呼び、中間状態が見えないようにする。
    """
    conn.execute(fund_holdings.delete().where(fund_holdings.c.portfolio_id == portfolio_id))
    if rows:
        conn.execute(fund_holdings.insert(), rows)


def list_fund_holdings(conn: Connection, portfolio_id: int) -> list[dict[str, Any]]:
    """fund_holdings を funds に LEFT JOIN して name 付きで返す（ADR-054・list_holdings 流儀）。

    行レベルに名前を焼かず、読むときに funds.name を補完する（repo 規約）。isin 昇順。
    """
    stmt = (
        select(
            fund_holdings.c.id,
            fund_holdings.c.portfolio_id,
            fund_holdings.c.isin,
            funds.c.name,
            fund_holdings.c.units,
            fund_holdings.c.avg_cost,
        )
        .select_from(fund_holdings.outerjoin(funds, fund_holdings.c.isin == funds.c.isin))
        .where(fund_holdings.c.portfolio_id == portfolio_id)
        .order_by(fund_holdings.c.isin)
    )
    return [dict(r) for r in conn.execute(stmt).mappings().all()]
