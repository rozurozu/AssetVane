"""注目候補ビルダー — 合流(confluence)ゲートで候補集合を決定論的に組む（ADR-067）。

設計の真実: docs/decisions.md ADR-067・docs/phase-specs/phase6-spec.md。

夜 digest の「注目シグナル」を score 閾値 Top N 抽出から作り直す。Python が独立した材料次元を見て、
広い母集団は**材料 2 次元以上の重なり**だけを候補にする（GC 単独・トレンド継続単独は落ちる）。夜の
分析AI はこの候補集合から「注目すべき銘柄」だけを submit_notable_stocks で選ぶ（ADR-014＝Python は
事実、AI は選別・解釈）。

材料次元（独立 4 つ・相関は 1 つに数える＝GC と RSI 反転は両方点いても「値動き」1 個）:
  ①値動き price   = 当日大幅変動(|前日比|>=BIG_MOVE_PCT) or GC or RSI 反転（momentum＋quant）
  ②出来高 volume  = volume_spike payload.notable（quant が焼いた ratio>=3.0 の目印・ADR-016）
  ③ニュース news  = 直近 NEWS_LOOKBACK_HOURS の polarity(pos/neg) 付き stock 層ニュース（ADR-049）
  ④リードラグ leadlag = 当日 lead_lag でその銘柄の業種(S17)がリーダー（score>=LEADLAG_LEADER_MIN）
（ai_alpha は .pkl 配置後に⑤として自動参入＝現状は signals に出ないので材料にならない）

候補化ルール:
  - 広い母集団: 材料 BROAD_MIN_MATERIALS(=2) 次元以上
  - carve-out（広い母集団）: 出来高極増（ratio>=EXTREME_VOLUME_RATIO）は単独でも候補
  - レーダー枠（保有 ∪ ウォッチ）: 材料 RADAR_MIN_MATERIALS(=1) 次元で候補（材料ゼロは出さない）

計算境界（ADR-014/016）: 事実（材料タグ・値）は Python がここで作り、AI は選別・説明だけ。手法閾値は
本モジュールの定数（ADR-027）、運用つまみ（候補総数上限）は config.settings。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import Connection

from app.config import settings
from app.db import repo
from app.quant.notable import daily_move_pct
from app.reference.sector_codes import (
    S17_TO_TOPIX17_ETF,
    SECTOR17_LABELS_JA,
    normalize_sector17,
)

logger = logging.getLogger(__name__)

# --- 手法パラメータ（ADR-016/027・再現性のためコード同居） ---
BIG_MOVE_PCT = 0.07  # 材料①: 当日 |前日比| >= 7% を大幅変動とみなす
EXTREME_VOLUME_RATIO = 7.0  # carve-out: volume_spike ratio >= 7.0 は単独でも候補
LEADLAG_LEADER_MIN = 0.70  # 材料④: lead_lag score >= 0.70 の業種を「リーダー」とみなす
NEWS_LOOKBACK_HOURS = 24  # 材料③: 直近 24h の polarity 付き stock ニュース
BROAD_MIN_MATERIALS = 2  # 広い母集団: 材料 2 次元以上で候補
RADAR_MIN_MATERIALS = 1  # レーダー枠（保有∪ウォッチ）: 材料 1 次元で候補
QUOTE_LOOKBACK_DAYS = 8  # 大幅変動用の adj_close 取得窓（直近 2 営業日を含む余裕）


def _parse_payload(raw: object) -> dict[str, Any]:
    """signals.payload（JSON 文字列）を dict に。壊れていれば空 dict（落とさない）。"""
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return obj if isinstance(obj, dict) else {}


def _leader_sector17(signal_rows: list[dict[str, Any]]) -> set[str]:
    """当日 lead_lag のリーダー業種を S17 コード集合で返す（材料④）。

    lead_lag signals は code が業種 ETF の 5 桁 DB コード（例 "16170"＝ETF"1617"＋"0"）。
    score>=LEADLAG_LEADER_MIN のリーダー ETF を S17_TO_TOPIX17_ETF で S17（"1".."17"）へ逆写像する。
    """
    leader_etf_codes = {
        str(r["code"])
        for r in signal_rows
        if r["signal_type"] == "lead_lag" and float(r["score"]) >= LEADLAG_LEADER_MIN
    }
    return {s17 for s17, etf4 in S17_TO_TOPIX17_ETF.items() if f"{etf4}0" in leader_etf_codes}


def build_notable_candidates(conn: Connection) -> dict[str, Any]:
    """当日の signals/news/保有/ウォッチから合流ゲートで候補集合を組む（ADR-067）。

    戻り値（AI 注入・Tool 返却・digest サマリの共通の真実）:
      {
        "signal_date": str | None,   # 採用した signals の最新算出日（None＝signals 皆無）
        "candidates": [ {code, company_name, in_radar, n_materials, materials{...}}, ... ],
        "counts": {"signals", "events", "candidates", "dropped"},
      }
    candidates は cap（config.notable_candidate_cap）で切り、超過は counts.dropped に件数だけ残す
    （No silent caps）。並びは in_radar → 材料数 → 強度の降順（AI が上から読める）。
    """
    signal_date = repo.get_latest_signal_date(conn)
    if signal_date is None:
        return {"signal_date": None, "candidates": [], "counts": _counts(0, 0, 0, 0)}

    signal_rows = repo.list_signals_with_sector_for_date(conn, signal_date)
    momentum_by_code: dict[str, dict[str, Any]] = {}
    volume_by_code: dict[str, dict[str, Any]] = {}
    name_by_code: dict[str, str | None] = {}
    sector_by_code: dict[str, str | None] = {}
    for r in signal_rows:
        code = str(r["code"])
        stype = r["signal_type"]
        if stype == "momentum":
            momentum_by_code[code] = _parse_payload(r.get("payload"))
            name_by_code[code] = r.get("company_name")
            sector_by_code[code] = r.get("sector17_code")
        elif stype == "volume_spike":
            volume_by_code[code] = _parse_payload(r.get("payload"))
            name_by_code[code] = r.get("company_name")
            sector_by_code[code] = r.get("sector17_code")

    leader_sectors = _leader_sector17(signal_rows)

    # ニュース起点（材料③）: 直近 24h の polarity 付き stock ニュースを code ごと最新 1 件。
    news_since = (datetime.now(UTC) - timedelta(hours=NEWS_LOOKBACK_HOURS)).isoformat()
    news_by_code: dict[str, dict[str, Any]] = {}
    for row in repo.list_recent_polarity_stock_news(conn, fetched_since=news_since):
        code = str(row["code"])
        if code not in news_by_code:  # fetched_at 降順なので最初が最新
            news_by_code[code] = row

    # レーダー枠（保有 ∪ ウォッチ）。
    holding_codes = {str(c) for c in repo.list_all_holding_codes(conn)}
    watch_codes = {str(w["code"]) for w in repo.list_watchlist(conn)}
    radar_codes = holding_codes | watch_codes

    # 候補ユニバース＝signals（momentum/volume）∪ ニュース起点 ∪ レーダー。
    universe = set(momentum_by_code) | set(volume_by_code) | set(news_by_code) | radar_codes

    # signals 由来でない銘柄（ニュース起点・レーダー）の名前/業種を補完する。
    missing = [c for c in universe if c not in name_by_code]
    basic = repo.get_stocks_basic_map(conn, missing)
    for code, info in basic.items():
        name_by_code.setdefault(code, info.get("company_name"))
        sector_by_code.setdefault(code, info.get("sector17_code"))

    # 当日大幅変動（材料①の一部）用に adj_close を一括取得。
    quote_since = (
        date.fromisoformat(signal_date) - timedelta(days=QUOTE_LOOKBACK_DAYS)
    ).isoformat()
    closes_by_code = repo.get_recent_adj_closes_by_codes(conn, list(universe), since=quote_since)

    scored: list[tuple[float, dict[str, Any]]] = []
    event_count = 0
    qualified = 0
    for code in universe:
        materials = _materials_for(
            code,
            momentum=momentum_by_code.get(code),
            volume=volume_by_code.get(code),
            news=news_by_code.get(code),
            sector17=sector_by_code.get(code),
            leader_sectors=leader_sectors,
            move_pct=daily_move_pct(closes_by_code.get(code, [])),
        )
        if "price" in materials or "volume" in materials:
            event_count += 1
        in_radar = code in radar_codes
        if not _qualifies(materials, in_radar=in_radar):
            continue
        qualified += 1
        cand = {
            "code": code,
            "company_name": name_by_code.get(code),
            "in_radar": in_radar,
            "n_materials": len(materials),
            "materials": materials,
        }
        scored.append((_strength(materials, in_radar=in_radar), cand))

    scored.sort(key=lambda t: t[0], reverse=True)
    cap = max(1, settings.notable_candidate_cap)
    kept = [c for _, c in scored[:cap]]
    dropped = max(0, qualified - len(kept))

    return {
        "signal_date": signal_date,
        "candidates": kept,
        "counts": _counts(len(signal_rows), event_count, qualified, dropped),
    }


def _materials_for(
    code: str,
    *,
    momentum: dict[str, Any] | None,
    volume: dict[str, Any] | None,
    news: dict[str, Any] | None,
    sector17: str | None,
    leader_sectors: set[str],
    move_pct: float | None,
) -> dict[str, dict[str, Any]]:
    """1 銘柄の点灯した材料次元 → 事実タグの dict を組む（点灯していない次元は入れない）。"""
    materials: dict[str, dict[str, Any]] = {}

    # ①値動き: 当日大幅変動 or GC or RSI 反転。
    gc = bool(momentum and momentum.get("golden_cross"))
    rev = bool(momentum and momentum.get("rsi_reversal"))
    big_move = move_pct is not None and abs(move_pct) >= BIG_MOVE_PCT
    if gc or rev or big_move:
        price: dict[str, Any] = {}
        if move_pct is not None:
            price["move_pct"] = round(move_pct, 4)
        if gc:
            price["golden_cross"] = True
        if rev:
            price["rsi_reversal"] = True
        materials["price"] = price

    # ②出来高: volume_spike payload.notable（quant が焼いた ratio>=3.0・ADR-016）。
    if volume and volume.get("notable"):
        vol: dict[str, Any] = {"ratio": volume.get("ratio")}
        if volume.get("label"):
            vol["label"] = volume.get("label")
        materials["volume"] = vol

    # ③ニュース: 直近の polarity 付き stock ニュース。
    if news is not None:
        headline = news.get("title") or news.get("summary") or news.get("url") or ""
        materials["news"] = {"polarity": news.get("polarity"), "headline": headline}

    # ④リードラグ: その銘柄の業種(S17)がリーダー。
    s17 = normalize_sector17(sector17)
    if s17 is not None and s17 in leader_sectors:
        materials["leadlag"] = {"sector17": s17, "sector": SECTOR17_LABELS_JA.get(s17)}

    return materials


def _qualifies(materials: dict[str, dict[str, Any]], *, in_radar: bool) -> bool:
    """合流ゲート判定（ADR-067）。レーダーは 1 次元・広い母集団は 2 次元 or 出来高極増単独。"""
    n = len(materials)
    if in_radar:
        return n >= RADAR_MIN_MATERIALS
    if n >= BROAD_MIN_MATERIALS:
        return True
    # carve-out: 出来高極増（ratio>=EXTREME_VOLUME_RATIO）は単独でも候補。
    vol = materials.get("volume")
    ratio = vol.get("ratio") if vol else None
    return ratio is not None and float(ratio) >= EXTREME_VOLUME_RATIO


def _strength(materials: dict[str, dict[str, Any]], *, in_radar: bool) -> float:
    """並び替え用の強度（レーダー最優先→材料数→値動き/出来高の大きさ）。"""
    move = materials.get("price", {}).get("move_pct")
    ratio = materials.get("volume", {}).get("ratio")
    magnitude = max(
        abs(float(move)) if move is not None else 0.0, (float(ratio) / 10.0) if ratio else 0.0
    )
    return (100.0 if in_radar else 0.0) + 10.0 * len(materials) + magnitude


def _counts(signals: int, events: int, candidates: int, dropped: int) -> dict[str, int]:
    return {"signals": signals, "events": events, "candidates": candidates, "dropped": dropped}


def _fmt_materials(materials: dict[str, dict[str, Any]]) -> str:
    """材料タグを人が読める 1 行に（プロンプト注入・Tool 返却の説明用）。"""
    parts: list[str] = []
    price = materials.get("price")
    if price is not None:
        bits = []
        if "move_pct" in price:
            bits.append(f"前日比{price['move_pct'] * 100:+.1f}%")
        if price.get("golden_cross"):
            bits.append("GC")
        if price.get("rsi_reversal"):
            bits.append("RSI反転")
        parts.append("値動き(" + "・".join(bits) + ")")
    volume = materials.get("volume")
    if volume is not None:
        ratio = volume.get("ratio")
        parts.append(f"出来高(平常{float(ratio):.1f}倍)" if ratio is not None else "出来高急増")
    news = materials.get("news")
    if news is not None:
        parts.append(f"ニュース({news.get('polarity')}: {news.get('headline')})")
    leadlag = materials.get("leadlag")
    if leadlag is not None:
        parts.append(f"リードラグ({leadlag.get('sector')})")
    return " / ".join(parts)


def format_candidates_for_prompt(result: dict[str, Any]) -> str:
    """候補集合を夜AI プロンプトに注入する文字列へ整形する（ADR-067・入力はプロンプト直注入）。

    各候補を「社名(コード) [★=保有/ウォッチ] 材料タグ…」の 1 行にする。候補ゼロなら「候補なし」。
    数値は事実（材料タグ）で、選別・解釈は AI に委ねる（ADR-014）。
    """
    candidates = result.get("candidates") or []
    counts = result.get("counts") or {}
    if not candidates:
        return "今日の注目候補: なし（材料の重なりが閾値に達した銘柄なし）。"

    n_sig = counts.get("signals", 0)
    lines = [f"今日の注目候補（{len(candidates)} 件・signals {n_sig} 件から合流ゲート）:"]
    for c in candidates:
        name = c.get("company_name") or c["code"]
        star = " ★保有/ウォッチ" if c.get("in_radar") else ""
        lines.append(f"・{name} ({c['code']}){star} — {_fmt_materials(c['materials'])}")
    if counts.get("dropped"):
        lines.append(f"（ほか {counts['dropped']} 件は候補上限で省略）")
    return "\n".join(lines)
