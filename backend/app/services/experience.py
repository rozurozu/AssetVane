"""経験蒸留（reviewer 面）の素材構築サービス（ADR-081・テーマ B）。

設計の真実: docs/decisions.md ADR-081・tasks/hermes-transfer-2026-07-02.md §8。

自己改善ループ ④「知識へ蒸留」の下ごしらえ。repo（採点済み outcome・カーソル）と reviewer 面の
Tool ループ（advisor/reviewer.py）の間に立ち、①活動量ゲートの新規 final 件数 ②蒸留の教材
（傾向バケット＋新規 final の bookend＋直近 journal）③プロンプト整形を組む。

計算境界（ADR-014/016/025）: 数値そのものは quant/outcome＋repo 集計が計算済み。ここは「どの
バケットを傾向として見せるか（count≥floor の頻度カウント＝過学習足切り）」「どの outcome を
起点根拠で bookend するか」の整形だけ。LLM には Python 計算の値を verbatim で渡す（AI は再計算
しない・数値を push しない）。生チャットは載せない（ADR-029・揮発）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Connection

from app.db import repo

# reviewer のレビュー済みカーソル（fetch_meta の source キー・edinet:crawl 前例＝新表を作らない）。
# 値は「最後にレビューした final の scored_at（ISO8601）」。次回はこれ超の final を新着と数える。
REVIEWER_CURSOR_KEY = "reviewer:cursor"

# 素材に載せる新規 final の上限（bookend の肥大を防ぐ安全弁・No silent caps＝format で残件明示）。
_NEW_FINAL_LIMIT = 50


def reviewer_cursor(conn: Connection) -> str | None:
    """reviewer の「最後にレビューした final の scored_at」を返す（未レビューは None・ADR-081）。"""
    row = repo.get_fetch_meta(conn, REVIEWER_CURSOR_KEY)
    return row.get("last_fetched_date") if row else None


def count_new_finals(conn: Connection) -> int:
    """カーソル以降に新しく final 化した outcome 件数（活動量ゲートの母数・ADR-081）。"""
    return repo.count_final_outcomes_since(conn, since=reviewer_cursor(conn))


def advance_cursor(conn: Connection) -> str | None:
    """カーソルを現時点の最新 final scored_at まで前進させる（成功時のみ呼ぶ・ADR-081・W2）。

    レビュー成功後、その時点で final 済みの最大 scored_at にカーソルを進める。次回のゲートは
    これ超の final だけを新着として数える（同じ outcome を二度教材にしない）。final が 1 件も
    無ければ据え置き（None を返す）。conn 注入で commit しない（呼び出し側 job が begin を所有）。
    """
    latest = repo.latest_final_scored_at(conn)
    if latest is not None:
        repo.upsert_fetch_meta_tx(conn, REVIEWER_CURSOR_KEY, latest)
    return latest


def build_distillation_material(conn: Connection, *, min_samples: int) -> dict[str, Any]:
    """蒸留の教材を組む（ADR-081・過学習足切りは count≥min_samples の Python 頻度カウント）。

    返り dict:
    - patterns: final 全体の source×kind×horizon 集計のうち **count ≥ min_samples** のバケットだけ
      （＝durable card にしてよい「傾向」。少サンプルは除外＝単発トレードから一般化させない）。
    - new_finals: カーソル以降に新しく final 化した outcome（起点 rationale/reason で bookend）。
    - recent_journal: 直近 journal の要約（少量・文脈）。
    - new_final_count / min_samples: サマリ用。
    """
    since = reviewer_cursor(conn)
    all_buckets = repo.aggregate_track_record(conn)
    patterns = [b for b in all_buckets if int(b.get("count") or 0) >= min_samples]
    new_finals = repo.list_new_final_outcomes(conn, since=since, limit=_NEW_FINAL_LIMIT)
    recent_journal = repo.get_recent_journal_summary(conn)
    return {
        "patterns": patterns,
        "new_finals": new_finals,
        "recent_journal": recent_journal,
        "new_final_count": len(new_finals),
        "min_samples": min_samples,
    }


def _pct(value: float | None) -> str:
    """小数リターン（0.023）を符号付きパーセント表記（+2.30%）にする。None は「—」。"""
    if value is None:
        return "—"
    return f"{value * 100:+.2f}%"


def _hit_mark(hit: Any) -> str:
    """hit（1/0/None）を ○/×/（非方向）に直す（notable は None＝非方向）。"""
    if hit is None:
        return "（非方向）"
    return "○" if int(hit) else "×"


def format_material_for_prompt(material: dict[str, Any]) -> str:
    """教材 dict を reviewer プロンプトに載せる散文へ整形する（ADR-081・数値は verbatim）。

    ①傾向（count≥floor バケット）②新規 final の bookend（起点根拠→採点数値）を並べる。どちらも
    Python が計算した値をそのまま並べる（AI は再計算しない・ADR-014/025）。直近 journal は
    build_messages の専用スロットに渡すのでここには載せない（重複回避）。
    """
    lines: list[str] = []

    patterns = material.get("patterns") or []
    lines.append("## 傾向（採点済み・十分なサンプルがあるバケットのみ）")
    if patterns:
        for b in patterns:
            hr = b.get("hit_rate")
            hr_txt = "—" if hr is None else f"{hr * 100:.0f}%"
            lines.append(
                f"- {b['source']}/{b['kind']} {b['horizon']}営業日: n={b['count']}"
                f" 的中率={hr_txt} 平均実現={_pct(b.get('avg_realized_return'))}"
                f" 平均超過={_pct(b.get('avg_excess_return'))}"
            )
    else:
        lines.append("- （十分なサンプルの傾向バケットはまだ無い）")

    new_finals = material.get("new_finals") or []
    lines.append("")
    lines.append(f"## 新しく確定した個別結果（前回レビュー以降・{len(new_finals)} 件）")
    if new_finals:
        for o in new_finals:
            name = o.get("company_name") or o.get("code")
            rationale = (o.get("rationale") or "").strip().replace("\n", " ")
            if len(rationale) > 120:
                rationale = rationale[:120] + "…"
            head = f"根拠『{rationale}』" if rationale else "根拠なし"
            lines.append(
                f"- [{o['source']}/{o['kind']} {o['horizon']}営業日] {name}({o['code']})"
                f" 起点{o['entry_date']} {head}"
                f" → 実現{_pct(o.get('realized_return'))} 超過{_pct(o.get('excess_return'))}"
                f" 的中={_hit_mark(o.get('hit'))}"
            )
    else:
        lines.append("- （新しく確定した個別結果は無い）")

    return "\n".join(lines)
