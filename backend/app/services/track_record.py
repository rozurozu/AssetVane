"""AI 過去提案の市場結果採点のオーケストレーション（ADR-077・テーマ A）。

設計の真実: docs/decisions.md ADR-077・tasks/hermes-transfer-2026-07-02.md。

repo（採点対象・価格・ベンチ）と quant.outcome（純関数）の間に立ち、buy/sell 提案（proposals・
ADR-052）と注目選別（notable_picks・ADR-067）を提案日終値起点の N 営業日後 実現/超過リターンで
採点し proposal_outcomes へ冪等 UPSERT する（score_pending_outcomes）。Tool get_track_record と
将来の画面 API が同じ成績を組めるよう集計も 1 か所に置く（同値は共有 service に一本化＝
backend-service-quant-pattern）。

計算境界（ADR-014/016）: 数値計算そのものは quant.outcome に委ね、ここは下ごしらえ（価格源の
振り分け・系列の受け渡し）と組み立てだけ。horizon の値（20/60）・価格源/ベンチ symbol の
振り分けは手法パラメータとしてここに置く（ADR-027）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import Connection

from app.db import repo
from app.quant.outcome import classify_hit, compute_horizon_outcome
from app.services.freshness import is_delayed

logger = logging.getLogger(__name__)

# 採点する保有営業日数（short catalyst=20≈1 ヶ月／medium thesis=60≈ML の対 TOPIX 60 日規約）。
_HORIZONS: tuple[int, ...] = (20, 60)
# 市場ごとのベンチ symbol（index_quotes・^TPX=TOPIX/JP・^SPX=S&P500/US＝portfolio.py 同値）。
_BENCHMARK_SYMBOL: dict[str, str] = {"JP": "^TPX", "US": "^SPX"}
# 確信度の canonical 集合（ADR-084）。採点境界のガード＝body の想定外値を NULL に倒し、
# proposal_outcomes.conviction には canonical か NULL しか入れない（集計バケットを汚さない）。
_CONVICTIONS: frozenset[str] = frozenset({"high", "medium", "low"})


def _prices_for(
    conn: Connection, cache: dict[tuple[str, str], list[dict[str, Any]]], market: str, code: str
) -> list[dict[str, Any]]:
    """(market, code) の adj_close 系列を取得しキャッシュする（同一 code の複数 horizon で共有）。

    market='US' は us_daily_quotes、それ以外は daily_quotes を引く（価格源の振り分け）。
    """
    key = (market, code)
    if key not in cache:
        if market == "US":
            cache[key] = repo.get_us_quotes(conn, code)
        else:
            cache[key] = repo.get_quotes(conn, code)
    return cache[key]


def _benchmark_for(
    conn: Connection, cache: dict[str, list[dict[str, Any]]], market: str
) -> list[dict[str, Any]]:
    """市場のベンチ（^TPX/^SPX）の close 系列を取得しキャッシュする（欠測なら空リスト）。"""
    symbol = _BENCHMARK_SYMBOL[market]
    if symbol not in cache:
        cache[symbol] = repo.get_index_quotes(conn, symbol)
    return cache[symbol]


def _score_one(
    conn: Connection,
    prices_cache: dict[tuple[str, str], list[dict[str, Any]]],
    bench_cache: dict[str, list[dict[str, Any]]],
    final_keys: set[tuple[str, int, int]],
    *,
    origin_kind: str,
    origin_id: int,
    source: str,
    kind: str,
    code: str,
    market: str,
    entry_date: str,
    conviction: str | None = None,
) -> tuple[int, int]:
    """1 提案 × 未 final の horizon を採点し UPSERT する（戻り値 = (upserted, finalized) 件数）。

    final 済みの (origin_kind, origin_id, horizon) は再採点しない（ADR-077・採点入口の有界化）。
    残り horizon が 0 本なら価格系列すら取らず即 return し、重い価格取得＋採点を丸ごと回避する。
    """
    remaining = [h for h in _HORIZONS if (origin_kind, origin_id, h) not in final_keys]
    if not remaining:
        return 0, 0  # 全 horizon final 済み → 価格取得もせず丸ごとスキップ
    prices = _prices_for(conn, prices_cache, market, code)
    benchmark = _benchmark_for(conn, bench_cache, market)
    benchmark_symbol = _BENCHMARK_SYMBOL[market]

    upserted = 0
    finalized = 0
    for horizon in remaining:
        out = compute_horizon_outcome(prices, benchmark, entry_date=entry_date, horizon=horizon)
        hit = classify_hit(kind, out["excess_return"], out["realized_return"])
        repo.upsert_proposal_outcome(
            conn,
            origin_kind=origin_kind,
            origin_id=origin_id,
            source=source,
            kind=kind,
            code=code,
            market=market,
            entry_date=entry_date,
            conviction=conviction,  # 提案時の確信度を非正規化コピー（ADR-084・notable は None）
            horizon=horizon,
            entry_priced_date=out["entry_priced_date"],
            entry_price=out["entry_price"],
            as_of_date=out["as_of_date"],
            exit_price=out["exit_price"],
            realized_return=out["realized_return"],
            benchmark_symbol=benchmark_symbol,
            excess_return=out["excess_return"],
            benchmark_fallback=1 if out["benchmark_fallback"] else 0,
            hit=None if hit is None else int(hit),
            status=out["status"],
        )
        upserted += 1
        if out["status"] == "final":
            finalized += 1
    return upserted, finalized


def score_pending_outcomes(conn: Connection) -> dict[str, int]:
    """全 buy/sell 提案＋notable を市場結果で採点し proposal_outcomes を冪等 UPSERT（ADR-077）。

    接続規約（W2）: commit はしない。呼び出し側（score_proposal_outcomes ジョブ）が
    `with get_engine().begin()` で境界を所有する。戻り値は件数サマリ（upserted/finalized）＝
    「今晩実際に採点した件数」（final スキップ分は数えない＝毎晩の実処理量が見える）。

    採点入口の有界化（ADR-077）: 既 final の (origin_kind, origin_id, horizon) は再採点しない。
    冒頭で既 final キー集合を 1 回引き、pending（horizon 未経過）だけを採点することで、母集団を
    horizon 未経過分に有界化する（提案が溜まっても毎晩の処理量が青天井にならない・結果値は不変）。
    """
    final_keys = repo.list_finalized_outcome_keys(conn)
    prices_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}
    bench_cache: dict[str, list[dict[str, Any]]] = {}
    upserted = 0
    finalized = 0

    for row in repo.list_scorable_trade_proposals(conn):
        body = row.get("body")
        try:
            parsed = json.loads(body) if body else {}
        except (ValueError, TypeError):
            logger.warning("score: proposal %s の body が JSON でない。skip", row.get("id"))
            continue
        code = parsed.get("code")
        market = parsed.get("market")
        if not code or market not in _BENCHMARK_SYMBOL:
            logger.warning(
                "score: proposal %s の code/market が不明（%s）。skip", row.get("id"), parsed
            )
            continue
        # conviction は persist 正規化済みだが想定外値は NULL に倒す（ADR-084・ガード）。
        raw_conviction = parsed.get("conviction")
        conviction = raw_conviction if raw_conviction in _CONVICTIONS else None
        u, f = _score_one(
            conn,
            prices_cache,
            bench_cache,
            final_keys,
            origin_kind="proposal",
            origin_id=int(row["id"]),
            source=row.get("source") or "chat",  # journal 由来 NULL は chat に倒す（ADR-077）
            kind=str(row["kind"]),
            code=str(code),
            market=str(market),
            entry_date=str(row["created_date"]),
            conviction=conviction,
        )
        upserted += u
        finalized += f

    for pick in repo.list_scorable_notable_picks(conn):
        u, f = _score_one(
            conn,
            prices_cache,
            bench_cache,
            final_keys,
            origin_kind="notable",
            origin_id=int(pick["id"]),
            source=pick.get("source") or "nightly",
            kind="notable",
            code=str(pick["code"]),
            market="JP",  # notable_picks は JP ユニバース限定（ADR-067）
            entry_date=str(pick["date"]),
        )
        upserted += u
        finalized += f

    return {"upserted": upserted, "finalized": finalized}


def _round(value: float | None, digits: int = 4) -> float | None:
    """float を丸める（None は素通し・表示ノイズを抑える）。"""
    return None if value is None else round(float(value), digits)


def get_track_record(
    conn: Connection,
    *,
    source: str | None = None,
    kind: str | None = None,
    horizon: int | None = None,
    recent_limit: int = 10,
) -> dict[str, Any]:
    """final の採点成績を集計＋直近個別で組んで返す（ADR-077・Tool/画面が同じ事実を引く）。

    返り値は JSON-safe な素の型のみ（Decimal は repo で Float 化・hit は bool・ADR-014/025）。
    summary の集計軸 = source × kind × horizon。加えて calibration（確信度キャリブレーション・
    ADR-084）を kind×conviction×horizon で返す（directional のみ）。
    数値は count と併記する（少サンプルの解釈は AI＝ADR-014）。
    """
    summary = [
        {
            "source": r["source"],
            "kind": r["kind"],
            "horizon": r["horizon"],
            "count": int(r["count"]),
            "hit_rate": _round(r["hit_rate"]),  # notable 群は None（非方向）
            "avg_realized_return": _round(r["avg_realized_return"]),
            "avg_excess_return": _round(r["avg_excess_return"]),
            "n_benchmark_fallback": int(r["n_benchmark_fallback"] or 0),
        }
        for r in repo.aggregate_track_record(conn, source=source, kind=kind, horizon=horizon)
    ]
    recent = [
        {
            "origin_kind": r["origin_kind"],
            "source": r["source"],
            "kind": r["kind"],
            "code": r["code"],
            "company_name": r["company_name"],
            "market": r["market"],
            "entry_date": r["entry_date"],
            "horizon": r["horizon"],
            "as_of_date": r["as_of_date"],
            "realized_return": _round(r["realized_return"]),
            "excess_return": _round(r["excess_return"]),
            "benchmark_symbol": r["benchmark_symbol"],
            "hit": None if r["hit"] is None else bool(r["hit"]),
        }
        for r in repo.list_recent_final_outcomes(
            conn, source=source, kind=kind, horizon=horizon, limit=recent_limit
        )
    ]
    # 確信度キャリブレーション（ADR-084）: buy/sell を conviction×horizon で横並び集計する。
    # source/kind/horizon では絞らず全母集団を出す（高確信ほど当たるかの比較が目的）。
    # summary=全体の成績・calibration=確信度の較正。
    calibration = [
        {
            "kind": r["kind"],
            "conviction": r["conviction"],
            "horizon": r["horizon"],
            "count": int(r["count"]),
            "hit_rate": _round(r["hit_rate"]),
            "avg_realized_return": _round(r["avg_realized_return"]),
            "avg_excess_return": _round(r["avg_excess_return"]),
        }
        for r in repo.aggregate_calibration(conn)
    ]
    as_of = repo.latest_final_as_of(conn)
    return {
        "as_of": as_of,
        "is_delayed": is_delayed(as_of),
        "summary": summary,
        "calibration": calibration,
        "recent": recent,
        "pending_count": repo.count_pending_outcomes(conn),
    }
