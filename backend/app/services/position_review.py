"""保有ポジションの前提崩れ監視 — 決定論の保有レビュー候補ビルダー（ADR-088・#3）。

設計の真実: docs/decisions.md ADR-088。

CORE 要素⑤は買い時に invalidation（前提崩れ条件）・catalyst・確信度を述べさせ、ADR-084 で
proposals.body に構造化永続する。しかし「記録された前提が今も成立しているか」を後で誰も見ていない
（売り提案はニュース起点の AI 裁量のみ＝ADR-052）。本モジュールは services/notable.py（合流ゲート・
ADR-067）を手本に、既定ポートフォリオの各 JP 保有について「前提崩れの疑い材料」を既存の計算済み
事実から**決定論**で集約し、code 一致で最新の買い提案 thesis（conviction/invalidation/catalyst）を
添える。LLM は使わない（材料は事実の boolean 集約＝含み損率は value_holdings が既に算出）。

材料フラグ（点灯した次元だけ・notable の _materials_for 同型）:
  loss          = 含み損率が LOSS_FLAG_PCT 以下（entry 比・value_holdings 由来）
  news          = 直近 NEWS_LOOKBACK_HOURS の negative polarity stock ニュース（ADR-051 と同源）
  guidance_miss = 会社予想の達成率（実績/予想）が GUIDANCE_MISS_MAX 未満（ADR-063 #4）
  guidance_cut  = 会社予想の修正率（新/旧−1）が GUIDANCE_CUT_MAX 以下＝下方修正（ADR-063 #4）
  restatement   = 直近 RESTATEMENT_LOOKBACK_DAYS 以内の訂正報告提出（ADR-063 #7）

ゲート（ADR-051 の生ニュース②と差別化＝thesis-aware）:
  needs_review = (記録済み thesis あり and 材料 1 次元以上) or (材料 2 次元以上)
  → 生ニュース単独（thesis 無・材料 1）は #3 では鳴らさない（ADR-051 の②保有悪材料で既出）。

計算境界（ADR-014/016）: 事実（材料タグ・値）は Python がここで作り、#5 の夜AI は選別・解釈だけ。
手法閾値は本モジュールの定数（ADR-016/027）、運用つまみ（件数上限）は config.settings。
日本株のみ（市場分離＝ADR-031・US 保有は次段）。
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import Connection

from app.config import settings
from app.db import repo
from app.services.portfolio import value_holdings

logger = logging.getLogger(__name__)

# --- 手法パラメータ（ADR-016/027・再現性のためコード同居） ---
LOSS_FLAG_PCT = -0.15  # 材料 loss: entry 比の含み損率が -15% 以下
NEWS_LOOKBACK_HOURS = 24  # 材料 news: 直近 24h の negative stock ニュース（ADR-051 と同窓）
GUIDANCE_MISS_MAX = 0.9  # 材料 guidance_miss: 会社予想達成率（実績/予想）が 0.9 未満＝明確な未達
GUIDANCE_CUT_MAX = -0.1  # 材料 guidance_cut: 予想修正率（新/旧−1）が -0.1 以下＝下方修正
RESTATEMENT_LOOKBACK_DAYS = 90  # 材料 restatement: 直近 90 日以内の訂正報告提出


def build_position_reviews(conn: Connection, portfolio_id: int | None = None) -> dict[str, Any]:
    """既定 PF の各 JP 保有から前提崩れの疑いがある保有だけを決定論で組む（ADR-088）。

    戻り値（#5 プロンプト注入・Tool 返却・digest の共通の真実）:
      {
        "portfolio_id": int | None,
        "as_of": str | None,          # 保有評価に使った最新終値日（None＝保有/価格なし）
        "reviews": [ {code, company_name, weight, unrealized_pnl_pct, market_value,
                      thesis{proposed_date, conviction?, invalidation?, catalyst?}|None,
                      flags{...}, n_flags, needs_review}, ... ],
        "counts": {"holdings", "flagged", "dropped"},
      }
    reviews は needs_review=True の保有だけを severity 降順で並べ、cap（position_review_cap）で
    切る（超過は counts.dropped に件数だけ＝No silent caps）。portfolio_id 省略時は先頭 PF
    （list_portfolios の先頭・裁定 L-9）。
    """
    portfolios = repo.list_portfolios(conn)
    if not portfolios:
        return {"portfolio_id": None, "as_of": None, "reviews": [], "counts": _counts(0, 0, 0)}
    pid = portfolio_id if portfolio_id is not None else portfolios[0]["portfolio_id"]

    holdings_rows = repo.list_holdings(conn, pid)
    codes = [h["code"] for h in holdings_rows]
    if not codes:
        return {"portfolio_id": pid, "as_of": None, "reviews": [], "counts": _counts(0, 0, 0)}

    latest_closes = repo.get_latest_closes(conn, codes)
    valued = value_holdings(holdings_rows, latest_closes)
    as_of = max((c["date"] for c in latest_closes.values()), default=None)

    # 悪材料ニュース（材料 news）は 1 クエリで code ごと最新 1 件（ADR-051 と同源・同窓）。
    news_since = (datetime.now(UTC) - timedelta(hours=NEWS_LOOKBACK_HOURS)).isoformat()
    news_by_code: dict[str, dict[str, Any]] = {}
    for row in repo.list_negative_stock_news_for_codes(conn, codes, fetched_since=news_since):
        news_by_code.setdefault(str(row["code"]), row)  # fetched_at 降順なので最初が最新

    today = datetime.now(UTC).date()

    scored: list[tuple[float, dict[str, Any]]] = []
    flagged = 0
    for h in valued:
        code = str(h["code"])
        pnl_pct = _unrealized_pnl_pct(h)
        thesis = repo.get_latest_trade_thesis(conn, code)
        flags = _flags_for(
            conn, code=code, pnl_pct=pnl_pct, news=news_by_code.get(code), today=today
        )
        if not _qualifies(flags, has_thesis=thesis is not None):
            continue
        flagged += 1
        review = {
            "code": code,
            "company_name": h.get("company_name") or code,
            "weight": h.get("weight"),
            "unrealized_pnl_pct": round(pnl_pct, 4) if pnl_pct is not None else None,
            "market_value": h.get("market_value"),
            "thesis": thesis,
            "flags": flags,
            "n_flags": len(flags),
            "needs_review": True,
        }
        scored.append((_strength(flags, pnl_pct), review))

    scored.sort(key=lambda t: t[0], reverse=True)
    cap = max(1, settings.position_review_cap)
    kept = [r for _, r in scored[:cap]]
    dropped = max(0, flagged - len(kept))
    return {
        "portfolio_id": pid,
        "as_of": as_of,
        "reviews": kept,
        "counts": _counts(len(holdings_rows), flagged, dropped),
    }


def _unrealized_pnl_pct(valued_holding: dict[str, Any]) -> float | None:
    """含み損益率 = unrealized_pnl / (shares*avg_cost)。価格/簿価が無ければ None（捏造しない）。"""
    pnl = valued_holding.get("unrealized_pnl")
    shares = valued_holding.get("shares")
    avg_cost = valued_holding.get("avg_cost")
    if pnl is None or shares is None or avg_cost is None:
        return None
    cost_basis = float(shares) * float(avg_cost)
    if cost_basis <= 0.0:
        return None
    return float(pnl) / cost_basis


def _flags_for(
    conn: Connection,
    *,
    code: str,
    pnl_pct: float | None,
    news: dict[str, Any] | None,
    today: date,
) -> dict[str, dict[str, Any]]:
    """1 保有の点灯した前提崩れ材料 → 事実タグの dict を組む（点灯していない材料は入れない）。"""
    flags: dict[str, dict[str, Any]] = {}

    # loss: entry 比の含み損。
    if pnl_pct is not None and pnl_pct <= LOSS_FLAG_PCT:
        flags["loss"] = {"pnl_pct": round(pnl_pct, 4)}

    # news: 直近の negative polarity stock ニュース（見出し添付・ADR-051 と同源）。
    if news is not None:
        headline = news.get("title") or news.get("summary") or news.get("url") or ""
        flags["news"] = {"polarity": "negative", "headline": headline}

    # guidance_miss / guidance_cut: 会社予想の質（ADR-063 #4・valuation_snapshot 由来）。
    snap = repo.get_valuation_snapshot(conn, code)
    if snap is not None:
        miss = _guidance_miss(snap)
        if miss is not None:
            flags["guidance_miss"] = miss
        cut = _guidance_cut(snap)
        if cut is not None:
            flags["guidance_cut"] = cut

    # restatement: 直近の訂正報告提出（ADR-063 #7・edinet_restatements 由来）。
    restated = repo.get_latest_restatement_date(conn, code)
    if restated is not None and _within_days(restated, today, RESTATEMENT_LOOKBACK_DAYS):
        flags["restatement"] = {"last_restatement_at": restated}

    return flags


def _guidance_miss(snap: dict[str, Any]) -> dict[str, Any] | None:
    """会社予想の達成率（実績/予想）が GUIDANCE_MISS_MAX 未満なら miss タグ（ADR-063 #4）。"""
    facts: dict[str, Any] = {}
    for key in ("op_forecast_achievement", "profit_forecast_achievement"):
        val = snap.get(key)
        if val is not None and float(val) < GUIDANCE_MISS_MAX:
            facts[key] = round(float(val), 4)
    return facts or None


def _guidance_cut(snap: dict[str, Any]) -> dict[str, Any] | None:
    """会社予想の修正率（新/旧−1）が GUIDANCE_CUT_MAX 以下＝下方修正なら cut タグ（ADR-063 #4）。"""
    facts: dict[str, Any] = {}
    for key in ("op_forecast_revision", "profit_forecast_revision"):
        val = snap.get(key)
        if val is not None and float(val) <= GUIDANCE_CUT_MAX:
            facts[key] = round(float(val), 4)
    return facts or None


def _within_days(iso_date: str, ref: date, days: int) -> bool:
    """iso_date（'YYYY-MM-DD' 前提）が ref から days 日以内か。パース不能なら False（安全側）。"""
    try:
        d = date.fromisoformat(iso_date[:10])
    except (ValueError, TypeError):
        return False
    return 0 <= (ref - d).days <= days


def _qualifies(flags: dict[str, dict[str, Any]], *, has_thesis: bool) -> bool:
    """前提崩れゲート（ADR-088）。記録済み thesis があれば材料 1 次元・無ければ 2 次元で対象。

    生ニュース単独（thesis 無・材料 1）は #3 では鳴らさない（ADR-051 の②保有悪材料で既出＝二重
    掲載を避ける・thesis-aware に絞る）。
    """
    n = len(flags)
    if has_thesis:
        return n >= 1
    return n >= 2


def _strength(flags: dict[str, dict[str, Any]], pnl_pct: float | None) -> float:
    """並び替え用の強度（材料数を主・含み損の深さを従）。"""
    loss_mag = abs(pnl_pct) if pnl_pct is not None and pnl_pct < 0 else 0.0
    return 10.0 * len(flags) + loss_mag


def _counts(holdings: int, flagged: int, dropped: int) -> dict[str, int]:
    return {"holdings": holdings, "flagged": flagged, "dropped": dropped}


def summarize_flags(flags: dict[str, dict[str, Any]]) -> str:
    """材料タグを人が読める 1 行に（プロンプト注入・digest 共有＝ラベルの単一の真実）。"""
    parts: list[str] = []
    loss = flags.get("loss")
    if loss is not None:
        pct = loss.get("pnl_pct")
        parts.append(f"含み損({pct * 100:+.1f}%)" if pct is not None else "含み損")
    news = flags.get("news")
    if news is not None:
        parts.append(f"悪材料({news.get('headline')})")
    if "guidance_miss" in flags:
        parts.append("会社予想未達")
    if "guidance_cut" in flags:
        parts.append("下方修正")
    rest = flags.get("restatement")
    if rest is not None:
        parts.append(f"訂正報告({rest.get('last_restatement_at')})")
    return " / ".join(parts)


def format_position_reviews_for_prompt(result: dict[str, Any]) -> str:
    """保有レビューを夜AI プロンプトへ注入する文字列に整形する（ADR-088/089・入力は直注入）。

    各 review を「社名(コード) — 材料タグ｜前提崩れ条件: invalidation」の 1 行にする。前提崩れの
    疑いが無ければ「保有の前提崩れ: なし」。数値は事実（材料タグ）で、崩れ判断・売買は #5 の夜AI に
    委ねる（ADR-014）。
    """
    reviews = result.get("reviews") or []
    if not reviews:
        return "保有の前提崩れ: なし（記録済みの前提を崩す材料が閾値に達した保有なし）。"

    lines = [
        f"保有の前提崩れの疑い（{len(reviews)} 件・記録済み thesis と今日の事実の突き合わせ対象）:"
    ]
    for r in reviews:
        name = r.get("company_name") or r["code"]
        line = f"・{name} ({r['code']}) — {summarize_flags(r.get('flags') or {})}"
        thesis = r.get("thesis") or {}
        if thesis.get("invalidation"):
            line += f"｜前提崩れ条件: {thesis['invalidation']}"
        if thesis.get("catalyst"):
            line += f"｜catalyst: {thesis['catalyst']}"
        lines.append(line)
    counts = result.get("counts") or {}
    if counts.get("dropped"):
        lines.append(f"（ほか {counts['dropped']} 件は表示上限で省略）")
    return "\n".join(lines)
