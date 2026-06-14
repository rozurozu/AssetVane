"""FX レート・米株保有の JPY 合算素（Phase 7(B-2)・ADR-057）。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Connection, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.repo._common import _upsert
from app.db.schema import (
    fx_rates,
    us_holdings,
    us_stocks,
    us_transactions,
)

# ===== FX レート・米株保有（ADR-057・Phase 7(B-2)・data-model.md「FX/米株保有」節） =====
# 米株保有を JPY 資産概要へ合算する素。FX は (date,pair) で冪等 UPSERT、米株取引→保有は日本株
# transactions→holdings をミラーするが、単一ユーザー（ADR-001）ゆえ portfolio で割らず symbol
# 単位で導出する（recalc_us_holdings は services/us_holdings.py が担う＝ADR-019/057）。


def upsert_fx_rates(rows: list[dict[str, Any]]) -> int:
    """fx_rates を (date,pair) 冪等 UPSERT する（ADR-002/057）。

    衝突キー: (date,pair)。fetch_fx_rates ジョブが [{date,pair,rate}] を渡す。再取得で重複しない。
    """
    return _upsert(fx_rates, rows, index_elements=["date", "pair"])


def get_latest_fx_rate(conn: Connection, pair: str = "USDJPY") -> dict[str, Any] | None:
    """指定ペアの最新（MAX date）の FX レートを返す。無ければ None（ADR-057）。

    返却: {date, pair, rate}。資産概要・snapshot_assets が現レートで米株評価額を JPY 換算する。
    """
    stmt = select(fx_rates).where(fx_rates.c.pair == pair).order_by(fx_rates.c.date.desc()).limit(1)
    row = conn.execute(stmt).mappings().first()
    return dict(row) if row else None


def get_fx_rate_on(conn: Connection, pair: str, date: str) -> dict[str, Any] | None:
    """約定日 date 以前で最も新しい FX レートを返す。無ければ None（ADR-057）。

    返却: {date, pair, rate}。取引登録時に約定時レートを焼くのに使う。約定日が休場（土日祝）で
    その日の行が無くても、直近営業日のレートに倒れる（date <= 指定日の MAX）。
    """
    stmt = (
        select(fx_rates)
        .where(fx_rates.c.pair == pair)
        .where(fx_rates.c.date <= date)
        .order_by(fx_rates.c.date.desc())
        .limit(1)
    )
    row = conn.execute(stmt).mappings().first()
    return dict(row) if row else None


def insert_us_transaction(conn: Connection, row: dict[str, Any]) -> int:
    """us_transactions に 1 行挿入し、発行された id を返す（ADR-019/057・insert_transaction 同型）。

    row には symbol/side/shares/price/fee/traded_at/fx_rate/note を含める。commit はしない。
    取引記録と us_holdings 再導出を atomic にするため、呼び出し側が begin() 境界を所有する。
    """
    result = conn.execute(us_transactions.insert().values(**row))
    return int(result.lastrowid)


def list_us_transactions(conn: Connection, symbol: str | None = None) -> list[dict[str, Any]]:
    """us_transactions を traded_at 昇順で返す（ADR-019/057・list_transactions 同型）。

    symbol=None なら全件（一覧表示用）、symbol 指定ならその銘柄のみ（recalc_us_holdings 用）。
    holdings 再計算で時系列順に適用するため昇順取得する。us_stocks を LEFT JOIN し company_name を
    補完する（行レベルに名前を焼かない流儀・list_holdings 同型）。
    """
    stmt = (
        select(
            us_transactions.c.id,
            us_transactions.c.symbol,
            us_stocks.c.company_name,
            us_transactions.c.side,
            us_transactions.c.shares,
            us_transactions.c.price,
            us_transactions.c.fee,
            us_transactions.c.traded_at,
            us_transactions.c.fx_rate,
            us_transactions.c.note,
        )
        .select_from(
            us_transactions.outerjoin(us_stocks, us_transactions.c.symbol == us_stocks.c.symbol)
        )
        .order_by(us_transactions.c.traded_at, us_transactions.c.id)
    )
    if symbol is not None:
        stmt = stmt.where(us_transactions.c.symbol == symbol)
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def get_us_transaction(conn: Connection, txn_id: int) -> dict[str, Any] | None:
    """us_transactions の 1 行を id で引く。存在しなければ None（ADR-019/057）。

    削除の存在確認と、再導出対象 symbol の取得に使う。読み取りなので commit しない。
    """
    row = (
        conn.execute(select(us_transactions).where(us_transactions.c.id == txn_id))
        .mappings()
        .first()
    )
    return dict(row) if row else None


def update_us_transaction(conn: Connection, txn_id: int, row: dict[str, Any]) -> None:
    """us_transactions の id 行を更新する（ADR-019/057・update_transaction 同型・C-14）。

    row には symbol/side/shares/price/fee/traded_at/fx_rate/note を含める。
    commit はしない。取引更新と us_holdings 再導出を atomic にするため、呼び出し側が
    `with get_engine().begin()` で境界を所有する（W2）。
    """
    conn.execute(us_transactions.update().where(us_transactions.c.id == txn_id).values(**row))


def delete_us_transaction(conn: Connection, txn_id: int) -> None:
    """us_transactions の id 行を削除する（ADR-019/057）。

    commit はしない。取引削除と us_holdings 再導出を atomic にするため、呼び出し側が begin() 境界を
    所有する。
    """
    conn.execute(us_transactions.delete().where(us_transactions.c.id == txn_id))


def upsert_us_holding(conn: Connection, row: dict[str, Any]) -> None:
    """us_holdings の 1 銘柄を symbol 冪等 UPSERT する（ADR-019/057）。

    row には symbol/shares/avg_cost/avg_cost_jpy を含める。commit はしない（取引と同じ begin() 内で
    呼ぶ）。symbol UNIQUE を衝突キーにし、shares/avg_cost/avg_cost_jpy を EXCLUDED で更新する。
    """
    stmt = sqlite_insert(us_holdings).values(**row)
    stmt = stmt.on_conflict_do_update(
        index_elements=["symbol"],
        set_={
            "shares": stmt.excluded["shares"],
            "avg_cost": stmt.excluded["avg_cost"],
            "avg_cost_jpy": stmt.excluded["avg_cost_jpy"],
        },
    )
    conn.execute(stmt)


def delete_us_holding(conn: Connection, symbol: str) -> None:
    """us_holdings の symbol 行を削除する（全売却で shares<=0 になった銘柄・ADR-019/057）。

    commit はしない（取引と同じ begin() 内で呼ぶ）。
    """
    conn.execute(us_holdings.delete().where(us_holdings.c.symbol == symbol))


def list_us_holdings(conn: Connection) -> list[dict[str, Any]]:
    """us_holdings を us_stocks に LEFT JOIN して company_name・gics_sector 付きで返す（ADR-057）。

    単一ユーザー（ADR-001）ゆえ portfolio で割らず全保有を symbol 昇順で返す。行レベルに名前を
    焼かない流儀で us_stocks から company_name/gics_sector を補完する（list_holdings 同型）。
    """
    stmt = (
        select(
            us_holdings.c.id,
            us_holdings.c.symbol,
            us_stocks.c.company_name,
            us_stocks.c.gics_sector,
            us_holdings.c.shares,
            us_holdings.c.avg_cost,
            us_holdings.c.avg_cost_jpy,
        )
        .select_from(us_holdings.outerjoin(us_stocks, us_holdings.c.symbol == us_stocks.c.symbol))
        .order_by(us_holdings.c.symbol)
    )
    return [dict(r) for r in conn.execute(stmt).mappings().all()]
