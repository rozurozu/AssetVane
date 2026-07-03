"""投資家プロファイル蒸留の素材構築サービス（ADR-082・テーマ C・★4 自己改善ループ）。

設計の真実: docs/decisions.md ADR-082・tasks/hermes-transfer-2026-07-02.md テーマ C。

repo（台帳: transactions・価格・ベンチ・カーソル）と quant.behavior/quant.outcome（純関数）と
profiler 面（advisor/profiler.py）の間に立ち、①活動量ゲート（新規 SELL 数）②行動信号の素材
（手仕舞いの帰結・ディスポジション・関心集中）③プロンプト整形を組む（experience.py と同型）。

計算境界（ADR-014/016/025）: 数値は quant.behavior＋quant.outcome が計算する。ここは価格源の
振り分け・集計 dict の下ごしらえ・min_samples 足切り・散文整形だけ。数値は verbatim で渡す
（AI は再計算しない・数値を push しない）。生チャットは載せない（ADR-029・揮発）。

v1 は JP 台帳（transactions）に焦点を絞る（ADR-082 スコープ）。US（us_transactions）は
compute_horizon_outcome/match_round_trips がそのまま流用できる機械的拡張で、次段に回す。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Connection

from app.db import repo
from app.quant import behavior
from app.quant.outcome import compute_horizon_outcome
from app.reference.sector_codes import sector17_label

# profiler のカーソル（fetch_meta の source キー・reviewer:cursor 前例＝新表を作らない）。
# 値は「最後に蒸留した SELL の traded_at（YYYY-MM-DD）」。次回はこれ超の SELL を新着と数える。
PROFILER_CURSOR_KEY = "profiler:cursor"

# 手仕舞いの帰結を測る保有営業日数（track_record と同値の手法パラメータ・ADR-077 と対称）。
_HORIZONS: tuple[int, ...] = (20, 60)
_JP_BENCHMARK = "^TPX"


# ---- 台帳の取り出し（JP 限定・v1） -------------------------------------------------------


def _jp_transactions(conn: Connection) -> list[dict[str, Any]]:
    """全 portfolio の JP 取引を traded_at 昇順で 1 本に束ねる（往復突合・集計の素）。"""
    rows: list[dict[str, Any]] = []
    for pf in repo.list_portfolios(conn):
        rows.extend(repo.list_transactions(conn, int(pf["portfolio_id"])))
    return rows


def _sell_dates(txns: list[dict[str, Any]]) -> list[str]:
    return [str(t["traded_at"]) for t in txns if str(t.get("side")) == "sell"]


# ---- 活動量ゲート・カーソル（experience.py と同型・ADR-082） ------------------------------


def profiler_cursor(conn: Connection) -> str | None:
    """profiler の「最後に蒸留した SELL の traded_at」を返す（未蒸留は None・ADR-082）。"""
    row = repo.get_fetch_meta(conn, PROFILER_CURSOR_KEY)
    return row.get("last_fetched_date") if row else None


def count_new_sells(conn: Connection) -> int:
    """カーソル以降の新規 SELL 約定件数（活動量ゲートの母数・ADR-082）。

    比較は 'YYYY-MM-DD' 文字列の辞書順＝時系列順。素材（build_behavior_material）は毎回全取引を
    再集計するので、バックデートで記録された古い SELL がゲートを跨げなくても素材からは欠落しない
    （ゲートの発火が遅れるだけ・reviewer の scored_at ゲートと同じ健全な有界化）。
    """
    cursor = profiler_cursor(conn)
    return sum(1 for d in _sell_dates(_jp_transactions(conn)) if cursor is None or d > cursor)


def advance_cursor(conn: Connection) -> str | None:
    """カーソルを最新 SELL の traded_at まで前進させる（成功時のみ呼ぶ・ADR-082・W2）。

    SELL が 1 件も無ければ据え置き（None を返す）。conn 注入で commit しない（呼び出し側 job が
    begin を所有する）。同じ SELL 群を二度教材の「新着」に数えないための単調前進。
    """
    dates = _sell_dates(_jp_transactions(conn))
    latest = max(dates) if dates else None
    if latest is not None:
        repo.upsert_fetch_meta_tx(conn, PROFILER_CURSOR_KEY, latest)
    return latest


# ---- 行動信号の素材（quant.behavior に計算を委ねる） -------------------------------------


def _sell_outcomes(conn: Connection, txns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """各 SELL 約定の traded_at を起点に 20/60 営業日後の outcome を集める（信号①の素）。"""
    prices_cache: dict[str, list[dict[str, Any]]] = {}
    benchmark = repo.get_index_quotes(conn, _JP_BENCHMARK)
    outcomes: list[dict[str, Any]] = []
    for t in txns:
        if str(t.get("side")) != "sell":
            continue
        code = str(t["code"])
        entry_date = str(t["traded_at"])
        if code not in prices_cache:
            prices_cache[code] = repo.get_quotes(conn, code)
        prices = prices_cache[code]
        for horizon in _HORIZONS:
            outcomes.append(
                compute_horizon_outcome(prices, benchmark, entry_date=entry_date, horizon=horizon)
            )
    return outcomes


def _buys_by_sector(conn: Connection, txns: list[dict[str, Any]]) -> dict[str, int]:
    """buy 約定を sector17 ラベル別に数える（信号③の素・未分類は「（業種不明）」）。"""
    sector_of = {str(s["code"]): s.get("sector17_code") for s in repo.list_stocks(conn)}
    counts: dict[str, int] = {}
    for t in txns:
        if str(t.get("side")) != "buy":
            continue
        label = sector17_label(sector_of.get(str(t["code"]))) or "（業種不明）"
        counts[label] = counts.get(label, 0) + 1
    return counts


def build_behavior_material(conn: Connection, *, min_samples: int) -> dict[str, Any]:
    """蒸留の教材を組む（ADR-082・数値は quant が計算・ここは下ごしらえと足切り閾値の同梱）。

    返り dict:
    - sell_regret: 手仕舞いの帰結（summarize_sell_regret）＝売った後に上がった率。
    - disposition: ディスポジション効果（summarize_disposition）＝勝ち急ぎ・損塩漬けの保有日数差。
    - concentration: 繰り返す関心（summarize_concentration）＝buy の業種集中。
    - recent_journal: 直近 journal 要約（少量・文脈）。
    - min_samples: 過学習足切り閾値（format 側が「傾向」として見せるかの判定に使う）。
    """
    txns = _jp_transactions(conn)
    return {
        "sell_regret": behavior.summarize_sell_regret(_sell_outcomes(conn, txns)),
        "disposition": behavior.summarize_disposition(behavior.match_round_trips(txns)),
        "concentration": behavior.summarize_concentration(_buys_by_sector(conn, txns)),
        "recent_journal": repo.get_recent_journal_summary(conn),
        "min_samples": min_samples,
    }


# ---- プロンプト整形（数値は verbatim・experience.format_material_for_prompt と同型） --------


def _pct(value: float | None) -> str:
    """小数（0.023）を符号付きパーセント（+2.30%）にする。None は「—」。"""
    return "—" if value is None else f"{value * 100:+.2f}%"


def _rate(value: float | None) -> str:
    """割合（0.66）をパーセント（66%）にする。None は「—」。"""
    return "—" if value is None else f"{value * 100:.0f}%"


def format_behavior_material_for_prompt(material: dict[str, Any]) -> str:
    """教材 dict を profiler プロンプトに載せる散文へ整形する（ADR-082・数値は verbatim）。

    min_samples 未満の信号は「サンプル不足」と明示し、傾向として断定させない（過学習足切り）。
    直近 journal は build_messages の専用スロットに渡すのでここには載せない（重複回避）。
    """
    min_samples = int(material.get("min_samples") or 0)
    lines: list[str] = []

    sr = material.get("sell_regret") or {}
    n_final = int(sr.get("n_final") or 0)
    lines.append("## 手仕舞いの帰結（自分の売り × その後の市場結果）")
    if n_final >= min_samples and n_final > 0:
        lines.append(
            f"- 採点済みの売り {n_final} 件（未経過 {int(sr.get('n_pending') or 0)} 件）。"
            f"売った後に上がった率＝{_rate(sr.get('recover_rate'))}"
            f"（ベンチ超過で上がった率＝{_rate(sr.get('excess_recover_rate'))}）。"
        )
        lines.append(
            f"  平均実現={_pct(sr.get('avg_realized_return'))}"
            f" 平均超過={_pct(sr.get('avg_excess_return'))}。"
        )
    else:
        lines.append(
            f"- 採点済みの売りが {n_final} 件（閾値 {min_samples} 未満）。傾向は断定不可。"
        )

    dp = material.get("disposition") or {}
    n_trips = int(dp.get("n_win") or 0) + int(dp.get("n_loss") or 0)
    lines.append("")
    lines.append("## 勝ち急ぎ / 損塩漬け（ディスポジション効果）")
    if n_trips >= min_samples and n_trips > 0:
        win_d = _num(dp.get("avg_holding_days_win"))
        loss_d = _num(dp.get("avg_holding_days_loss"))
        gap_d = _num(dp.get("disposition_gap"))
        lines.append(
            f"- 実現した往復 {n_trips} 件"
            f"（勝ち {int(dp.get('n_win') or 0)}・負け {int(dp.get('n_loss') or 0)}）。"
        )
        lines.append(f"  勝ちの平均保有={win_d}日・負けの平均保有={loss_d}日")
        lines.append(f"  （差＝{gap_d}日、正が大きいほど負けを長く持つ癖）。")
    else:
        lines.append(f"- 実現した往復が {n_trips} 件（閾値 {min_samples} 未満）。傾向は断定不可。")

    conc = material.get("concentration") or []
    lines.append("")
    lines.append("## 繰り返す関心（買いの業種集中）")
    top = [c for c in conc if int(c.get("count") or 0) >= min_samples]
    if top:
        for c in top:
            lines.append(f"- {c['bucket']}: 買い {c['count']} 件（構成比 {_rate(c.get('share'))}）")
    else:
        lines.append(f"- 閾値 {min_samples} 件以上の業種集中はまだ無い。")

    return "\n".join(lines)


def _num(value: float | None) -> str:
    """日数などの実数を丸めた文字列にする。None は「—」。"""
    return "—" if value is None else f"{value:.0f}"
