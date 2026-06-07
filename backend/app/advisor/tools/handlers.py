"""Tool handler 群 — LLM 引数 → quant/data 実関数 → 返却 dict の薄い橋渡し（ADR-014）。

設計の真実: docs/phase-specs/phase3-spec.md §4.4・§4.5。

各 handler は「引数 dict を schemas.py で検証 → quant/data の実関数を呼ぶ → spec §4.4 の dict を
返す」だけ。ロジック・計算は持たない（ADR-014・レイヤ分離）。例外は handler 内で握り
`{"error": "..."}` を返す（dispatch ループを落とさない）。

DB は読み取り接続 `with get_engine().connect() as conn:` で開く（書き込みはしない）。
metrics/optimize/asset-overview の組み立ては既存ルータ（routers/portfolio.py・assets.py・
signals.py）と同じオーケストレーションを踏襲する（計算経路を一致させる・ADR-014）。
"""

from __future__ import annotations

import datetime
import json
import logging
from typing import Any

import pandas as pd
from sqlalchemy import Connection

from app.adapters.news import fetch_news
from app.advisor.dossier import investigate_stock
from app.advisor.tools.schemas import (
    FetchNewsArgs,
    GetAssetOverviewArgs,
    GetDossierArgs,
    GetFinancialsArgs,
    GetGeneralNewsArgs,
    GetIndicatorsArgs,
    GetLeadLagArgs,
    GetPortfolioMetricsArgs,
    GetSignalsArgs,
    InvestigateStockArgs,
    OptimizePortfolioArgs,
    ScreenStocksArgs,
    SubmitJournalArgs,
    coerce_policy_change,
)
from app.db import repo
from app.db.engine import get_engine
from app.quant import (
    compute_indicators,
    compute_portfolio_metrics,
    optimize_portfolio,
)
from app.services.policy import get_policy
from app.services.portfolio import (
    build_price_panel,
    current_stock_weights,
    portfolio_deviations,
    value_holdings,
)

logger = logging.getLogger(__name__)

# Free プランは 12 週間遅延（ADR-008）。signals/indicators 等は遅延扱いを True 固定にする
# （既存ルータの流儀＝routers/signals.py・portfolio.py・assets.py に合わせる）。
_IS_DELAYED = True

# get_signals のトップ date が today からこの日数以上前なら遅延扱い（routers/signals.py と同値）。
_DELAY_THRESHOLD_DAYS = 7


def _parse_payload(raw: Any) -> dict[str, Any]:
    """signals.payload（生 TEXT）を dict にする。壊れていたら空 dict（落とさない）。"""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _signals_is_delayed(date_str: str | None) -> bool:
    """signals のトップ date が遅延境界を超えていれば True（routers/signals.py と同式）。"""
    if not date_str:
        return False
    try:
        d = datetime.date.fromisoformat(date_str)
    except ValueError:
        return False
    return (datetime.date.today() - d).days >= _DELAY_THRESHOLD_DAYS


def _resolve_portfolio_id(conn: Connection, portfolio_id: int | None) -> int | None:
    """portfolio_id 省略時は先頭ポートフォリオで解決する（routers/portfolio.py L-9）。"""
    if portfolio_id is not None:
        return portfolio_id
    rows = repo.list_portfolios(conn)
    return int(rows[0]["portfolio_id"]) if rows else None


# ---------------------------------------------------------------------------
# Phase 1 Tool
# ---------------------------------------------------------------------------


async def handle_get_indicators(args: dict[str, object]) -> dict[str, Any]:
    """get_indicators（spec §4.4）。日足→compute_indicators→平坦な指標 dict。"""
    try:
        code = GetIndicatorsArgs.model_validate(args).code
        with get_engine().connect() as conn:
            quotes = repo.get_quotes(conn, code)
        df = pd.DataFrame(quotes)
        result = compute_indicators(df)
        return {
            "code": code,
            "as_of": result.get("as_of"),
            "adj_close": result.get("adj_close"),
            "sma25": result.get("sma25"),
            "sma75": result.get("sma75"),
            "rsi14": result.get("rsi14"),
            "vol_ma20": result.get("vol_ma20"),
            "is_delayed": _IS_DELAYED,
        }
    except Exception as exc:
        logger.exception("handle_get_indicators 失敗")
        return {"error": str(exc)}


async def handle_get_signals(args: dict[str, object]) -> dict[str, Any]:
    """get_signals（spec §4.4）。routers/signals.py の組み立てを踏襲。"""
    try:
        parsed = GetSignalsArgs.model_validate(args)
        with get_engine().connect() as conn:
            resolved = (
                parsed.date
                if parsed.date is not None
                else repo.get_latest_signal_date(conn, parsed.type)
            )
            rows = repo.get_signals(conn, resolved, parsed.type, code=parsed.code)
        signals = [
            {
                "code": row["code"],
                "company_name": row.get("company_name"),
                "signal_type": row["signal_type"],
                "score": row["score"],
                "payload": _parse_payload(row.get("payload")),
            }
            for row in rows
        ]
        return {
            "date": resolved,
            "is_delayed": _signals_is_delayed(resolved),
            "signals": signals,
        }
    except Exception as exc:
        logger.exception("handle_get_signals 失敗")
        return {"error": str(exc)}


async def handle_screen_stocks(args: dict[str, object]) -> dict[str, Any]:
    """screen_stocks（spec §4.4）。signals を criteria で濾し indicators を平坦化。"""
    try:
        c = ScreenStocksArgs.model_validate(args)
        limit = c.limit if c.limit is not None and c.limit > 0 else 100
        with get_engine().connect() as conn:
            resolved = repo.get_latest_signal_date(conn, c.signal_type)
            # min_score 後の打ち切りで件数が痩せないよう、JOIN 取得は広めに取ってから濾す。
            rows = repo.get_signals(conn, resolved, c.signal_type, limit=max(limit * 4, limit))
            # sector33_code 絞りは signals に業種が無いため stocks を別途引いて解決する。
            sector_codes: set[str] | None = None
            if c.sector33_code:
                stock_rows = repo.list_stocks(conn)
                sector_codes = {
                    s["code"] for s in stock_rows if s.get("sector33_code") == c.sector33_code
                }

        items: list[dict[str, Any]] = []
        for row in rows:
            if c.min_score is not None and row["score"] < c.min_score:
                continue
            if sector_codes is not None and row["code"] not in sector_codes:
                continue
            items.append(
                {
                    "code": row["code"],
                    "company_name": row.get("company_name"),
                    "signal_type": row["signal_type"],
                    "score": row["score"],
                    "indicators": _parse_payload(row.get("payload")),
                }
            )
            if len(items) >= limit:
                break

        return {
            "date": resolved,
            "is_delayed": _signals_is_delayed(resolved),
            "items": items,
        }
    except Exception as exc:
        logger.exception("handle_screen_stocks 失敗")
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Phase 2 Tool
# ---------------------------------------------------------------------------


async def handle_get_portfolio_metrics(args: dict[str, object]) -> dict[str, Any]:
    """get_portfolio_metrics（spec §4.4）。routers/portfolio.py の metrics 経路を踏襲。"""
    try:
        pid_arg = GetPortfolioMetricsArgs.model_validate(args).portfolio_id
        with get_engine().connect() as conn:
            portfolio_id = _resolve_portfolio_id(conn, pid_arg)
            if portfolio_id is None:
                return {"error": "ポートフォリオが存在しません。"}

            holdings_rows = repo.list_holdings(conn, portfolio_id)
            codes = [h["code"] for h in holdings_rows]
            price_panel = build_price_panel(conn, codes)
            latest_closes = repo.get_latest_closes(conn, codes) if codes else {}
            valued = value_holdings(holdings_rows, latest_closes)
            weights = current_stock_weights(valued)
            labels = {h["code"]: h.get("company_name") or h["code"] for h in holdings_rows}
            policy = get_policy(conn)
            result = compute_portfolio_metrics(price_panel, weights, policy, labels)
            # deviations は asset-overview と同値にするため共有ヘルパで上書き（決定6・B-12）。
            deviations = portfolio_deviations(conn, portfolio_id)

        corr = result.get("correlation") or {"codes": [], "labels": [], "matrix": []}
        return {
            "portfolio_id": portfolio_id,
            "as_of": result.get("as_of"),
            "is_delayed": bool(result.get("is_delayed", _IS_DELAYED)),
            "annual_return": result.get("annual_return"),
            "annual_volatility": result.get("annual_volatility"),
            "sharpe": result.get("sharpe"),
            "max_drawdown": result.get("max_drawdown"),
            "lookback_days": result.get("lookback_days"),
            "correlation": corr,
            "deviations": deviations,
        }
    except Exception as exc:
        logger.exception("handle_get_portfolio_metrics 失敗")
        return {"error": str(exc)}


async def handle_optimize_portfolio(args: dict[str, object]) -> dict[str, Any]:
    """optimize_portfolio（spec §4.4）。routers/portfolio.py の optimize 経路を踏襲。"""
    try:
        pid_arg = OptimizePortfolioArgs.model_validate(args).portfolio_id
        with get_engine().connect() as conn:
            portfolio_id = _resolve_portfolio_id(conn, pid_arg)
            if portfolio_id is None:
                return {"error": "ポートフォリオが存在しません。"}

            holdings_rows = repo.list_holdings(conn, portfolio_id)
            codes = [h["code"] for h in holdings_rows]
            price_panel = build_price_panel(conn, codes)
            latest_closes = repo.get_latest_closes(conn, codes) if codes else {}
            valued = value_holdings(holdings_rows, latest_closes)
            weights = current_stock_weights(valued)
            policy = get_policy(conn)
            sectors = {h["code"]: h.get("sector33_code") or "" for h in holdings_rows}
            code_to_name = {h["code"]: h.get("company_name") for h in holdings_rows}

        result = optimize_portfolio(
            price_panel=price_panel,
            policy=policy,
            sectors=sectors,
            objective="max_sharpe",
            current_weights=weights if weights else None,
        )
        weights_out = [
            {
                "code": w["code"],
                "company_name": code_to_name.get(w["code"]),
                "current_weight": w.get("current_weight"),
                "target_weight": float(w["target_weight"]),
                "delta": float(w["delta"]),
            }
            for w in result.get("weights", [])
        ]
        return {
            "portfolio_id": portfolio_id,
            "as_of": result.get("as_of"),
            "is_delayed": bool(result.get("is_delayed", _IS_DELAYED)),
            "objective": result.get("objective", "max_sharpe"),
            "cash_weight": float(result.get("cash_weight", 0.0)),
            "weights": weights_out,
            "expected_annual_return": result.get("expected_annual_return"),
            "expected_annual_volatility": result.get("expected_annual_volatility"),
            "expected_sharpe": result.get("expected_sharpe"),
            "constraints_applied": result.get("constraints_applied", {}),
            "infeasible": bool(result.get("infeasible", False)),
        }
    except Exception as exc:
        logger.exception("handle_optimize_portfolio 失敗")
        return {"error": str(exc)}


async def handle_get_financials(args: dict[str, object]) -> dict[str, Any]:
    """get_financials（spec §4.4）。repo.get_financials → {code, items}。"""
    try:
        code = GetFinancialsArgs.model_validate(args).code
        with get_engine().connect() as conn:
            rows = repo.get_financials(conn, code)
        return {"code": code, "items": [dict(r) for r in rows]}
    except Exception as exc:
        logger.exception("handle_get_financials 失敗")
        return {"error": str(exc)}


async def handle_get_asset_overview(args: dict[str, object]) -> dict[str, Any]:
    """get_asset_overview（spec §4.4）。routers/assets.py の asset-overview 経路を踏襲。"""
    try:
        GetAssetOverviewArgs.model_validate(args)  # 引数なし（余分な引数を弾く検証のみ）
        with get_engine().connect() as conn:
            portfolios = repo.list_portfolios(conn)
            portfolio_id: int | None = portfolios[0]["portfolio_id"] if portfolios else None

            stock_value = 0.0
            pnl = 0.0
            as_of: str | None = None
            if portfolio_id is not None:
                holdings_rows = repo.list_holdings(conn, portfolio_id)
                codes = [h["code"] for h in holdings_rows]
                if codes:
                    latest_closes = repo.get_latest_closes(conn, codes)
                    holdings_valued = value_holdings(holdings_rows, latest_closes)
                    for h in holdings_valued:
                        if h.get("market_value") is not None:
                            stock_value += float(h["market_value"])
                        if h.get("unrealized_pnl") is not None:
                            pnl += float(h["unrealized_pnl"])
                    as_of = repo.get_max_daily_date(conn)

            cash_row = repo.get_cash(conn)
            cash_value = float(cash_row["balance"]) if cash_row else 0.0
            ext_rows = repo.list_external_assets(conn)
            external_value = sum(float(r["value"]) for r in ext_rows if r.get("value") is not None)
            total_value = stock_value + cash_value + external_value

            def _weight(v: float) -> float:
                return v / total_value if total_value > 0 else 0.0

            allocation = [
                {"name": "株式", "value": stock_value, "weight": _weight(stock_value)},
                {"name": "現金", "value": cash_value, "weight": _weight(cash_value)},
                {"name": "投信", "value": external_value, "weight": _weight(external_value)},
            ]

            policy = get_policy(conn)
            deviations = (
                portfolio_deviations(conn, portfolio_id) if portfolio_id is not None else []
            )
            snapshots = repo.get_asset_snapshots(conn, limit=365)

        trend = [
            {"date": s["date"], "total_value": float(s["total_value"] or 0)} for s in snapshots
        ]
        policy_targets = {
            "target_cash_ratio": policy.get("target_cash_ratio"),
            "max_position_weight": policy.get("max_position_weight"),
        }
        return {
            "as_of": as_of,
            "is_delayed": _IS_DELAYED,
            "total_value": total_value,
            "stock_value": stock_value,
            "cash_value": cash_value,
            "external_value": external_value,
            "pnl": pnl,
            "allocation": allocation,
            "policy_targets": policy_targets,
            "deviations": deviations,
            "trend": trend,
        }
    except Exception as exc:
        logger.exception("handle_get_asset_overview 失敗")
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Phase 3 Tool
# ---------------------------------------------------------------------------


async def handle_submit_journal(args: dict[str, object]) -> dict[str, Any]:
    """submit_journal（spec §4.4・§5）。引数検証のみ（journal 書き込みは段2 nightly）。

    実際の advisor_journal / proposals 書き込みは nightly が tool_runs から引数を読んで行う。
    ここは検証して {"ok": True} を返すだけ（橋渡しの責務に閉じる）。

    頑健性（ADR-013/018）: 非力なモデルは任意項目 proposed_policy_change を単一 {field,to} で
    なく、非 dict（markdown 文字列）や多フィールド patch で渡すことがある。その 1 項目のために
    submission 全体を弾くと観測（observations）まで巻き添えで失い再試行でラウンドを浪費する。
    そこで coerce_policy_change で単一形に正規化し、適合しない変更案だけ落として受理する（nightly
    も同関数で正規化）。必須の observations が欠けるときだけ error を返す（ループは落とさない）。
    """
    # 変更案を単一 {field,to} に正規化。非 dict・多フィールド patch 等は None に倒して受理する。
    change = args.get("proposed_policy_change")
    coerced = coerce_policy_change(change)
    if change is not None and coerced is None:
        logger.warning(
            "submit_journal: proposed_policy_change が単一 {field,to} 形でない（%s）。変更案を破棄",
            type(change).__name__,
        )
    args = {**args, "proposed_policy_change": coerced}
    try:
        SubmitJournalArgs.model_validate(args)
        return {"ok": True}
    except Exception as exc:
        logger.exception("handle_submit_journal 失敗")
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Phase 4 Tool（Stock Dossier）
# ---------------------------------------------------------------------------


async def handle_investigate_stock(args: dict[str, object]) -> dict[str, Any]:
    """investigate_stock（spec §4・ADR-020/011）。調査パイプラインを起動する。

    引数は code のみ（spec §4）。取得手段は httpx 一本に統一したため `mode` は廃止した
    （ADR-020 改訂）。パイプライン本体は dossier.investigate_stock が持ち、本 handler は
    橋渡しのみ（ADR-014・レイヤ分離）。社名解決は dossier.investigate_stock が repo から行う。

    書き込みを伴う（dossier/sources を UPSERT）ので `with get_engine().begin() as conn:` で
    束ねる（dossier.investigate_stock は conn を受け自分では commit しない＝W2 規約）。
    戻り値 dict（spec §4 の investigate_stock スキーマ）をそのまま返す。
    """
    try:
        code = InvestigateStockArgs.model_validate(args).code
        with get_engine().begin() as conn:
            return await investigate_stock(conn, code)
    except Exception as exc:
        logger.exception("handle_investigate_stock 失敗")
        return {"error": str(exc)}


async def handle_get_dossier(args: dict[str, object]) -> dict[str, Any]:
    """get_dossier（spec §4・§5.2）。既存ドシエ本体とソース台帳を合成して返す。

    repo.get_dossier（1 行・key_facts は生 TEXT）と repo.list_dossier_sources（published_at 降順）
    を別々に引いて合成する（get_dossier は sources を JOIN しない＝repo 規約）。key_facts は
    json.loads でオブジェクト化して返す（spec §5.2 の `Record<string, unknown> | null`）。

    未調査（get_dossier が None）時は spec §5.2 の流儀に従い `summary_md: ""`・空 sources で返す
    （404 ではなく空ドシエ＝Tool はループを落とさず「まだ調査されていない」を LLM に伝えられる）。
    """
    try:
        code = GetDossierArgs.model_validate(args).code
        with get_engine().connect() as conn:
            row = repo.get_dossier(conn, code)
            source_rows = repo.list_dossier_sources(conn, code)

        sources = [
            {
                "url": s["url"],
                "title": s.get("title"),
                "summary": s.get("summary"),
                "published_at": s.get("published_at"),
                "source_type": s.get("source_type"),
            }
            for s in source_rows
        ]
        if row is None:
            # 未調査（spec §5.2: summary_md="" ＋空 sources で返す）。
            return {
                "code": code,
                "summary_md": "",
                "key_facts": None,
                "last_investigated_at": None,
                "updated_at": None,
                "sources": sources,
            }
        return {
            "code": code,
            "summary_md": row.get("summary_md") or "",
            "key_facts": _parse_payload(row.get("key_facts")) or None,
            "last_investigated_at": row.get("last_investigated_at"),
            "updated_at": row.get("updated_at"),
            "sources": sources,
        }
    except Exception as exc:
        logger.exception("handle_get_dossier 失敗")
        return {"error": str(exc)}


async def handle_fetch_news(args: dict[str, object]) -> dict[str, Any]:
    """fetch_news（spec §4）。ニュース取得を起動する（取得手段は httpx 一本＝ADR-020 改訂）。

    引数は code・任意 since（spec §4）。`mode` は廃止した（昼夜で取得を分けない＝ADR-020 改訂）。
    NewsAdapter は DB に触らない契約なので、社名解決は呼び出し側（本 handler）の責務として
    自前で読み取り接続を開き `repo.get_stock` から社名を引く（他 handler が自前 conn を開く流儀）。
    社名が取れなければ code を社名代わりに渡す（検索が空振りしても落とさない）。
    本 handler は橋渡しのみ（ADR-010/014）。
    """
    try:
        parsed = FetchNewsArgs.model_validate(args)
        with get_engine().connect() as conn:
            stock = repo.get_stock(conn, parsed.code)
        company_name = (stock or {}).get("company_name") or parsed.code
        articles = await fetch_news(parsed.code, company_name, since=parsed.since)
        return {"code": parsed.code, "articles": articles}
    except Exception as exc:
        logger.exception("handle_fetch_news 失敗")
        return {"error": str(exc)}


async def handle_get_general_news(args: dict[str, object]) -> dict[str, Any]:
    """get_general_news（ADR-034）。銘柄に紐づかない一般ニュースの直近台帳を返す。

    夜間ジョブ fetch_general_news が貯めた general_news を読むだけ（再取得はしない＝消費先は
    「貯めた台帳を読む」設計）。lookback はカテゴリ取得と同じ定数で揃え、直近分のみカテゴリ別に
    まとめて返す。本 handler は橋渡しのみ（ADR-010/014）。
    """
    from datetime import UTC, datetime, timedelta

    from app.adapters.general_news_config import GENERAL_NEWS_LOOKBACK_DAYS

    try:
        GetGeneralNewsArgs.model_validate(args)
        since = (datetime.now(UTC) - timedelta(days=GENERAL_NEWS_LOOKBACK_DAYS)).strftime(
            "%Y-%m-%d"
        )
        with get_engine().connect() as conn:
            rows = repo.list_general_news(conn, since=since)

        categories: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            categories.setdefault(r["category"], []).append(
                {
                    "url": r["url"],
                    "title": r.get("title"),
                    "summary": r.get("summary"),
                    "published_at": r.get("published_at"),
                }
            )
        return {
            "categories": [{"label": label, "items": items} for label, items in categories.items()]
        }
    except Exception as exc:
        logger.exception("handle_get_general_news 失敗")
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Phase 7 Tool（日米業種リードラグ）
# ---------------------------------------------------------------------------


async def handle_get_lead_lag(args: dict[str, object]) -> dict[str, Any]:
    """get_lead_lag（Phase 7・SIG-FIN-036-13）。日米業種リードラグの最新ランキング＋検証指標。

    GET /lead-lag と同じ事実（同じ signals を読む）を返す（計算経路を一致させる＝ADR-014）。
    夜間バッチ calc_lead_lag が焼いた signals（signal_type='lead_lag'）の最新算出日分を score
    降順で読み、ranking（JP 業種・5桁 code・和名・0..1 score・生 signal）＋meta を組む。
    台帳が空でも error にせず as_of=None / ranking=[] を返す（ループを落とさない）。
    """
    try:
        GetLeadLagArgs.model_validate(args)
        with get_engine().connect() as conn:
            resolved = repo.get_latest_signal_date(conn, "lead_lag")
            rows = repo.get_signals(conn, resolved, "lead_lag", limit=100) if resolved else []

        ranking: list[dict[str, Any]] = []
        head_payload: dict[str, Any] = {}
        for row in rows:
            payload = _parse_payload(row.get("payload"))
            if not head_payload:
                head_payload = payload
            ranking.append(
                {
                    "code": row["code"],
                    "label": payload.get("label") or row["code"],
                    "score": row["score"],
                    "signal": payload.get("signal"),
                }
            )
        return {
            "as_of": resolved,
            "ranking": ranking,
            "meta": {
                "is_delayed": _signals_is_delayed(resolved),
                "model_as_of": resolved,
                "ic": head_payload.get("ic"),
                "hit_rate": head_payload.get("hit_rate"),
                "window": head_payload.get("window"),
                "k": head_payload.get("k"),
                "lambda": head_payload.get("lambda"),
            },
        }
    except Exception as exc:
        logger.exception("handle_get_lead_lag 失敗")
        return {"error": str(exc)}
