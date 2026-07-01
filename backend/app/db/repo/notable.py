"""注目候補（confluence ゲート）と AI 選別（notable_picks）のクエリ（ADR-067）。

設計の真実: docs/decisions.md ADR-067・docs/phase-specs/phase6-spec.md。

夜 digest の「注目シグナル」を作り直す（ADR-067）。ここは candidate builder（services/notable.py）が
使う読み取りクエリ群と、夜の分析AI が submit_notable_stocks で選んだ銘柄の永続を持つ。
戻り値は素の dict（backend-repo-pattern）。書き込みは接続注入で commit しない（W2＝呼び出し側が
begin() 境界を所有）。UPSERT で冪等（ADR-002）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, and_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.schema import daily_quotes, news, notable_picks, signals, stocks

# --- 候補ビルダーの読み取りクエリ ---


def list_signals_with_sector_for_date(conn: Connection, date: str) -> list[dict[str, Any]]:
    """指定日の signals を stocks に LEFT JOIN し company_name＋sector17_code 付きで全件返す。

    candidate builder が signal_type ごと（momentum/volume_spike/lead_lag）に材料次元を組むのに使う
    （ADR-067）。lead_lag 行は code が業種 ETF の 5 桁コードで stocks に無いため company_name/
    sector17_code は NULL になる（builder が sector17→ETF 対応表で解決する）。payload は生の TEXT の
    まま返す（json.loads は builder の責務）。
    """
    stmt = (
        select(
            signals.c.code,
            stocks.c.company_name,
            stocks.c.sector17_code,
            signals.c.signal_type,
            signals.c.score,
            signals.c.payload,
        )
        .select_from(signals.outerjoin(stocks, signals.c.code == stocks.c.code))
        .where(signals.c.date == date)
    )
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def get_recent_adj_closes_by_codes(
    conn: Connection, codes: list[str], *, since: str
) -> dict[str, list[float | None]]:
    """指定銘柄群の adj_close を since 以降で code ごとに日付昇順で返す（ADR-067 材料①）。

    戻り値は `{code: [adj_close 昇順]}`。builder が末尾 2 本から quant.notable.daily_move_pct で当日
    大幅変動を出す（分割影響を避けるため close でなく adj_close を使う）。codes 空なら空 dict。
    """
    if not codes:
        return {}
    stmt = (
        select(daily_quotes.c.code, daily_quotes.c.date, daily_quotes.c.adj_close)
        .where(and_(daily_quotes.c.code.in_(list(codes)), daily_quotes.c.date >= since))
        .order_by(daily_quotes.c.code, daily_quotes.c.date)
    )
    out: dict[str, list[float | None]] = {}
    for r in conn.execute(stmt).mappings():
        out.setdefault(r["code"], []).append(r["adj_close"])
    return out


def get_stocks_basic_map(conn: Connection, codes: list[str]) -> dict[str, dict[str, Any]]:
    """指定銘柄群の company_name/sector17_code を `{code: {...}}` で返す（ADR-067）。

    候補ユニバースに signals 由来でない銘柄（保有/ウォッチ/ニュース起点）が入るため、それらの
    表示名と業種（材料④リードラグ判定に要る sector17）を一括で引く。codes 空なら空 dict。
    """
    if not codes:
        return {}
    stmt = select(stocks.c.code, stocks.c.company_name, stocks.c.sector17_code).where(
        stocks.c.code.in_(list(codes))
    )
    return {
        r["code"]: {"company_name": r["company_name"], "sector17_code": r["sector17_code"]}
        for r in conn.execute(stmt).mappings()
    }


def list_recent_polarity_stock_news(
    conn: Connection, *, fetched_since: str
) -> list[dict[str, Any]]:
    """直近取り込みの stock 層ニュースのうち polarity が pos/neg の行を返す（ADR-067 材料③）。

    fetched_at >= fetched_since で絞り、fetched_at 降順（同値 id 降順）で返す。builder は code ごと
    最新 1 件を採って材料③（ニュース）＋ニュース起点枠に使う（neutral は材料に数えない＝方向のある
    材料だけ）。code NULL 行は除外（stock 層で code 必須）。本文は持たない（要約/見出し/URL のみ）。
    """
    stmt = (
        select(
            news.c.code,
            news.c.title,
            news.c.summary,
            news.c.url,
            news.c.polarity,
            news.c.published_at,
            news.c.fetched_at,
        )
        .where(
            and_(
                news.c.level == "stock",
                news.c.code.isnot(None),
                news.c.polarity.in_(["positive", "negative"]),
                news.c.fetched_at >= fetched_since,
            )
        )
        .order_by(news.c.fetched_at.desc(), news.c.id.desc())
    )
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


# --- AI 選別（notable_picks）の永続・読み取り ---


def upsert_notable_pick(
    conn: Connection, *, date: str, code: str, reason: str | None, source: str = "nightly"
) -> None:
    """夜の分析AI が選んだ注目銘柄 1 件を notable_picks に UPSERT する（ADR-067・冪等）。

    UNIQUE(date,code,source) 衝突時は reason/created_at を更新する（同じ晩の再実行で重複しない＝
    ADR-002）。接続注入で commit しない（W2＝呼び出し側 persist が begin() 境界を所有）。
    """
    now = datetime.now(UTC).isoformat()
    stmt = sqlite_insert(notable_picks).values(
        date=date, code=code, reason=reason, source=source, created_at=now
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["date", "code", "source"],
        set_={"reason": reason, "created_at": now},
    )
    conn.execute(stmt)


def list_notable_picks_for_date(
    conn: Connection, date: str, *, source: str = "nightly"
) -> list[dict[str, Any]]:
    """指定日・source の notable_picks を company_name 付き・起票順で返す（ADR-067）。

    notify_digest が digest 本文の「注目シグナル（AI 選別）」を組むのに使う。並びは id 昇順＝夜AI が
    submit_notable_stocks に並べた順（＝関連度順）を保つ。company_name は LEFT JOIN で補う。
    """
    stmt = (
        select(
            notable_picks.c.code,
            stocks.c.company_name,
            notable_picks.c.reason,
        )
        .select_from(notable_picks.outerjoin(stocks, notable_picks.c.code == stocks.c.code))
        .where(and_(notable_picks.c.date == date, notable_picks.c.source == source))
        .order_by(notable_picks.c.id)
    )
    return [dict(r) for r in conn.execute(stmt).mappings().all()]
