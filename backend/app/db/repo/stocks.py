"""銘柄マスタ・日足・fetch_meta・シグナル（Phase 0/1・ADR-002/026）。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.engine import get_engine
from app.db.repo._common import _upsert
from app.db.schema import (
    daily_quotes,
    fetch_meta,
    financials,
    signals,
    stocks,
)


def upsert_stocks(rows: list[dict[str, Any]]) -> int:
    """銘柄マスタを冪等 UPSERT（code 1 行・ADR-002/026）。

    partial=True＝**渡された列だけ**更新する。edinet_code は sync_master
    （_normalize_stock）が持たず、別経路 set_stock_edinet_code /
    bulk_set_stock_edinet_codes（edinetdb sweep・ADR-064）が焼く永続キー。全列
    EXCLUDED 更新だと NIGHTLY 先頭の sync_master が毎晩 edinet_code を NULL に潰し、
    sweep の成果破棄＋edinetdb API 無駄叩きになる（#8）。edinet_code は温存する。
    """
    return _upsert(stocks, rows, index_elements=["code"], partial=True)


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


def get_stock_edinet_codes(conn: Connection, codes: list[str]) -> dict[str, str | None]:
    """指定銘柄コードの edinet_code を {code: edinet_code|None} で返す（#2 の対象解決・ADR-064）。

    存在しない code は dict に含めない（呼び出し側が watchlist/holdings の実在コードを渡す前提）。
    None は「未解決（夜間に解決対象）」を表す。
    """
    if not codes:
        return {}
    rows = conn.execute(
        select(stocks.c.code, stocks.c.edinet_code).where(stocks.c.code.in_(codes))
    ).all()
    return {r.code: r.edinet_code for r in rows}


def set_stock_edinet_code(conn: Connection, code: str, edinet_code: str | None) -> None:
    """stocks.edinet_code を 1 件更新する（#2 の財務取得キー解決・
    W2＝conn 受け commit しない・ADR-064）。

    edinetdb.jp /companies から解決した sec_code↔edinet_code を焼く。呼び出し側（夜間ジョブ）が
    begin() 境界を所有する（jquants_config の upsert と同じ W2 規律）。
    """
    conn.execute(update(stocks).where(stocks.c.code == code).values(edinet_code=edinet_code))


def bulk_set_stock_edinet_codes(conn: Connection, mapping: dict[str, str]) -> int:
    """sec_code→edinet_code の対応を stocks.edinet_code に一括反映する（full-list sweep・W2・
    ADR-064）。

    mapping のうち stocks に実在する code だけ更新される（存在しない sec_code は黙って無視）。
    edinetdb.jp /companies 全件から作った対応表を焼く想定（月数回のスイープ・レート予算節約）。
    更新できた件数を返す。
    """
    n = 0
    for code, ecode in mapping.items():
        res = conn.execute(update(stocks).where(stocks.c.code == code).values(edinet_code=ecode))
        n += res.rowcount or 0
    return n


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
        "last_attempt_ok": 1,  # 前進＝直近試行は成功（空取得＝休場も成功扱い）
    }
    _upsert(fetch_meta, [row], index_elements=["source"])


def upsert_fetch_meta_tx(conn: Connection, source: str, last_fetched_date: str) -> None:
    """`source` の取得済み最終値を conn 注入で前進させる（W2＝呼び出し側が begin を所有・ADR-081）。

    upsert_fetch_meta（自前で begin を開き commit する）の conn 版。reviewer:cursor を
    distill_experience ジョブの begin() 境界内で card 起票と atomic に前進させるために使う
    （別接続の self-commit を外側 begin の最中に呼ぶと WAL で database is locked になり得る）。
    """
    row = {
        "source": source,
        "last_fetched_date": last_fetched_date,
        "updated_at": datetime.now(UTC).isoformat(),
        "last_attempt_ok": 1,
    }
    stmt = sqlite_insert(fetch_meta).values(**row)
    stmt = stmt.on_conflict_do_update(index_elements=["source"], set_=row)
    conn.execute(stmt)


def mark_fetch_attempt_failed(source: str) -> None:
    """`source` の直近取得試行を失敗（last_attempt_ok=0）として記録する（ADR-018）。

    取得失敗（IndexAdapterError 等）を fetch_meta に残し、notify_digest が「今回取れなかった
    指数」を朝の digest に情報行で出せるようにする。差分取得の再開点 last_fetched_date は
    **潰さない**（成功時の最終取得日を保つ）。行が無ければ last_fetched_date=NULL で作る。
    _upsert は全列を EXCLUDED で上書きするため使えず、更新列を絞った専用 UPSERT で書く。
    """
    now = datetime.now(UTC).isoformat()
    stmt = sqlite_insert(fetch_meta).values(source=source, last_attempt_ok=0, updated_at=now)
    stmt = stmt.on_conflict_do_update(
        index_elements=["source"],
        set_={"last_attempt_ok": 0, "updated_at": now},  # last_fetched_date は据え置く
    )
    with get_engine().begin() as conn:
        conn.execute(stmt)


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


def list_jp_universe_codes(conn: Connection) -> set[str]:
    """JP 普通株（ETF/REIT 除外）の証券コード集合を返す（EDINET secCode 突合用・ADR-056 段階C）。

    EDINET 書類一覧クロールで拾った secCode（5 桁）がこの集合に在れば取り込み対象、無ければ skip
    （REIT/ファンド/非上場提出を落とす）。is_etf=1 を除外（NULL は普通株扱い＝coalesce）。
    set で返すのは O(1) 突合のため（クロールは 1 日数百件 × 多数日を回す）。
    """
    stmt = select(stocks.c.code).where(func.coalesce(stocks.c.is_etf, 0) == 0)
    return set(conn.execute(stmt).scalars().all())


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


def get_max_daily_date(conn: Connection) -> str | None:
    """daily_quotes の MAX(date)（as_of の鮮度確認用・spec P2-2）。

    既存 get_max_quote_date の別名（holdings/assets ルータ向け）。
    """
    return get_max_quote_date(conn)
