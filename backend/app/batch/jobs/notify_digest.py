"""夜間バッチ: Signal Beacon 通知 digest ジョブ（phase6-spec.md §3・ADR-007/014/016/018）。

NIGHTLY_JOBS の**末尾**（取得 → signals → 夜の分析AI の後＝事実と提案が揃ってから）で呼ばれ、
当日の⑦⑧＋夜AI 当日提案を **1 通の Discord digest** に束ねて送る（spec §1）。

設計の芯:
- **AI に数値を計算させない**（ADR-014/016）。⑧アラートの抽出（score 閾値・出来高急増）は Python が
  signals の事実で判定する。出来高 3 倍判定は quant が payload.notable に焼くので通知層は再閾値化
  せず notable を読む。提案文は Phase 3 が生成済みの advisor_journal.proposal をそのまま引用する。
- **日付は UTC で統一**する。夜の分析AI（nightly.py）が journal を datetime.now(UTC) の日付で
  書くため、digest もその日付で journal を引く（cron は 02:00 JST＝前日 17:00 UTC でズレるため）。
- **冪等**（spec §3・ADR-002/018）。notify_key='digest:<UTC日付>' で 1 日 1 通。coalesce 漏れや
  POST /batch/run 手動再実行で同日 2 回走っても 2 通目は送らない。
- 例外は握って JobResult(ok=False)（runner が error 通知）。通知失敗で本処理は巻き込まない。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import Connection

from app.batch import notify
from app.batch.runner import JobResult
from app.config import settings
from app.db import repo
from app.db.engine import get_engine

logger = logging.getLogger(__name__)


def _parse_payload(raw: str | None) -> dict[str, Any]:
    """signals.payload（JSON 文字列）を dict に。壊れていれば空 dict（落とさない）。"""
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return obj if isinstance(obj, dict) else {}


def _is_alert(score: float, payload: dict[str, Any]) -> bool:
    """⑧アラート条件: 高スコア（score>=alert_score_min）または quant が notable と焼いた銘柄。

    出来高 3 倍（NOTABLE_RATIO）・ゴールデンクロス等の「目印」判定は quant が payload.notable に
    持つ（ADR-016）。通知層は閾値を再定義せず notable を読む。
    """
    return score >= settings.alert_score_min or bool(payload.get("notable"))


def _format_signal_line(row: dict[str, Any]) -> str:
    """1 アラートを 1 行に整形（社名(コード) ラベル [type] score）。"""
    name = row.get("company_name") or row["code"]
    payload = _parse_payload(row.get("payload"))
    label = payload.get("label") or row["signal_type"]
    return f"・{name} ({row['code']}) {label} [{row['signal_type']}] score {row['score']:.2f}"


def _rebalance_line(conn: Connection, today: str) -> str | None:
    """⑦リバランス: policy.updated_at から rebalance_alert_days 超なら見直しを促す 1 行。

    最終見直し日の正本は policy.updated_at（方針更新＝見直しの実体・裁定 OPEN-P）。updated_at が
    無い／policy 未設定なら判定不能で None（アラートしない）。
    """
    policy = repo.get_policy(conn)
    if not policy or not policy.get("updated_at"):
        return None
    try:
        updated = datetime.fromisoformat(policy["updated_at"]).date()
    except (ValueError, TypeError):
        return None
    days = (date.fromisoformat(today) - updated).days
    if days <= settings.rebalance_alert_days:
        return None
    return (
        f"⚠️ リバランス: 前回の方針見直しから {days} 日経過"
        f"（{settings.rebalance_alert_days} 日超）。方針を見直す時期です。"
    )


def _failed_index_line(conn: Connection) -> str | None:
    """直近の取得試行が失敗した指数を 1 行（非アラート情報）にまとめる。

    fetch_index の各シンボル（^SPX/^NKX/^TPX＋米国業種 ETF）について
    fetch_meta['index_quotes:<symbol>'] の last_attempt_ok を読み、0（直近試行が失敗）のものを
    抽出する。成否は fetch_index が試行ごとに記録する（成功=1／空取得＝休場も成功=1／失敗=0）。
    取得手段の無い指数（例: Free プランの ^TPX）を名指しハードコードせず汎用に拾う
    （将来 ^SPX 等が落ちても自動で出る）。試行成否そのものを見るため、市場休場やシンボル間の
    カレンダー差では誤検知しない。表示には最後に取得できた日（last_fetched_date）を添える。
    """
    # 同一 batch パッケージ内。対象集合とキー規約は fetch_index を正本に再利用する。
    from app.batch.jobs.fetch_index import _source_key, _target_symbols

    failed: list[str] = []
    for sym in _target_symbols():
        meta = repo.get_fetch_meta(conn, _source_key(sym))
        if not meta or meta.get("last_attempt_ok") != 0:
            continue
        last = meta.get("last_fetched_date")
        failed.append(f"{sym}（最終取得 {last}）" if last else f"{sym}（未取得）")

    if not failed:
        return None
    return f"📉 取得できなかった指数: {', '.join(failed)}"


# ②保有銘柄の悪材料アラートで digest に出す最大件数（残りは「ほか N 件」・ADR-051）。
_HOLDING_RISK_MAX = 5
# ①注目シグナルへ「なぜ動いたか」ニュースを添える際の発行日 lookback（直近 N 日・ADR-051）。
_SIGNAL_NEWS_LOOKBACK_DAYS = 3


def _news_attach_line(conn: Connection, code: str, today: str) -> str | None:
    """①注目シグナルの code に直近の stock 層ニュースを 1 件添える（急騰落の自動説明・ADR-051）。

    published_at が直近 _SIGNAL_NEWS_LOOKBACK_DAYS 日以内の stock 層ニュース最新 1 件の見出し
    （無ければ summary/url）を「なぜ動いたか」の手がかりとして 1 行返す。holdings フィルタはしない
    （保有外でも動いた理由を知りたい＝ADR-051 の①）。ニュースが無ければ None（何も添えない）。
    値動き=quant・説明=ニュース引用で AI に数値を作らせない（ADR-014）。
    """
    since = (date.fromisoformat(today) - timedelta(days=_SIGNAL_NEWS_LOOKBACK_DAYS)).isoformat()
    rows = repo.list_news(conn, level="stock", code=code, since=since, limit=1)
    if not rows:
        return None
    headline = rows[0].get("title") or rows[0].get("summary") or rows[0].get("url") or ""
    return f"　└ {headline}"


def _holding_risk_lines(conn: Connection) -> list[str]:
    """②保有銘柄の悪材料アラート行を組み立てる（ADR-051・能動配信）。

    既定ポートフォリオ（list_portfolios の先頭・裁定 L-9）の holdings に紐づく stock 層ニュースの
    うち、polarity='negative' かつ fetched_at が直近 24h の悪材料を最大 _HOLDING_RISK_MAX 件＋
    残件数で返す。fetched_at 24h 窓で「同じ悪材料を翌晩再掲しない」を自然に実現する（ADR-051）。
    悪材料が無ければ空リスト（呼び出し側がセクションごと省略）。社名は repo の LEFT JOIN で
    補完済み。
    """
    portfolios = repo.list_portfolios(conn)
    if not portfolios:
        return []
    codes = repo.list_holding_codes(conn, portfolios[0]["portfolio_id"])
    if not codes:
        return []
    since = (datetime.now(UTC) - timedelta(hours=24)).isoformat()
    rows = repo.list_negative_stock_news_for_codes(conn, codes, fetched_since=since)
    if not rows:
        return []

    shown = rows[:_HOLDING_RISK_MAX]
    remaining = len(rows) - len(shown)
    lines = [f"⚠️ 保有銘柄の悪材料（{len(rows)} 件）"]
    for r in shown:
        name = r.get("company_name") or r["code"]
        headline = r.get("title") or r.get("summary") or r.get("url") or ""
        lines.append(f"・{name} ({r['code']}) {headline}")
    if remaining > 0:
        lines.append(f"　…ほか {remaining} 件")
    return lines


def build_digest_content(conn: Connection, today: str) -> str | None:
    """当日の⑦⑧＋夜AI 提案を 1 通の digest 本文に組み立てる（spec §3）。

    today は UTC 日付 'YYYY-MM-DD'（journal と揃える）。⑧シグナルは最新算出日
    （get_latest_signal_date）の signals を閾値抽出 → score 降順 Top N。ALWAYS_DAILY_DIGEST=False
    かつ⑦⑧・提案すべて無しなら None（送信スキップ）。True（既定）なら検知ゼロでもサマリを返す。
    """
    # ⑧ シグナル抽出（最新算出日の signals を閾値で絞り score 降順 Top N）。
    signal_date = repo.get_latest_signal_date(conn)
    alerts: list[dict[str, Any]] = []
    total_signals = 0
    if signal_date:
        rows = repo.list_signals_for_alert(conn, signal_date)
        total_signals = len(rows)
        alerts = [r for r in rows if _is_alert(r["score"], _parse_payload(r.get("payload")))]

    top_n = max(1, settings.alert_top_n)
    shown = alerts[:top_n]
    remaining = len(alerts) - len(shown)

    # ⑦ リバランス判定。
    rebalance = _rebalance_line(conn, today)

    # 取得できなかった指数の情報行（非アラート）。has_content には含めない（取得失敗だけの日に
    # digest を新規発火させない＝静けさ維持。毎朝送信時はその本文に乗る）。
    failed_index = _failed_index_line(conn)

    # 夜AI 当日提案（Phase 3 生成済み文を引用・ADR-014）。
    journal = repo.get_journal_for_date(conn, today)
    proposal = (journal or {}).get("proposal")
    policy_change = None
    if journal and journal.get("proposed_policy_change"):
        try:
            pc = json.loads(journal["proposed_policy_change"])
            if isinstance(pc, dict) and pc.get("field"):
                policy_change = pc
        except (json.JSONDecodeError, TypeError):
            policy_change = None

    # ②保有銘柄の悪材料（能動配信の主目的・ADR-051）。has_content に含めて「悪材料がある夜は
    # always_daily_digest=False でも送る」を満たす。
    risk_lines = _holding_risk_lines(conn)

    has_content = bool(shown or rebalance or proposal or risk_lines)
    if not has_content and not settings.always_daily_digest:
        return None  # 好機がある日だけ送る設定で、何も無い日（[OPEN-N]）

    # --- 本文組み立て ---
    lines: list[str] = [f"**📊 AssetVane 朝のダイジェスト（{today}）**", ""]

    # ②は能動配信の主眼。Discord の 1900 字截断で末尾が切れても残るよう注目シグナルより前に置く。
    if risk_lines:
        lines.extend(risk_lines)
        lines.append("")

    if shown:
        lines.append(f"🔔 注目シグナル（{signal_date} 時点・{len(alerts)} 件）")
        for r in shown:
            lines.append(_format_signal_line(r))
            # ①「なぜ動いたか」を直近ニュースで補足する（あれば 1 行添える・ADR-051）。
            attach = _news_attach_line(conn, r["code"], today)
            if attach:
                lines.append(attach)
        if remaining > 0:
            lines.append(f"　…ほか {remaining} 件")
    else:
        lines.append("🔔 注目シグナル: なし")
    lines.append("")

    if rebalance:
        lines.append(rebalance)
        lines.append("")

    if proposal:
        lines.append(f"💡 夜の分析AI の提案: {proposal}")
        if policy_change:
            to = policy_change.get("to")
            lines.append(f"　方針変更案: {policy_change['field']} → {to}")
        lines.append("")

    if failed_index:
        lines.append(failed_index)
        lines.append("")

    # 当日サマリ（検知ゼロでも届く＝完了条件）。
    lines.append(
        f"— サマリ: signals {total_signals} 件 / 注目 {len(alerts)} 件 / "
        f"AI 提案 {'あり' if proposal else 'なし'}"
    )
    return "\n".join(lines)


def run() -> JobResult:
    """当日の事実と AI 提案を 1 通の Discord digest に束ねて冪等送信する（spec §3）。

    例外は握って JobResult(ok=False)（runner が error 通知）。送信失敗で本処理は巻き込まない。
    """
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    try:
        with get_engine().connect() as conn:
            content = build_digest_content(conn, today)

        if content is None:
            return JobResult(
                name="notify_digest", ok=True, rows=0, detail="送信対象なし（毎朝送信は無効）"
            )

        sent = notify.send_once(f"digest:{today}", content)
        detail = "digest 送信" if sent else "digest 送信せず（既送 or Webhook 未設定/失敗）"
        return JobResult(name="notify_digest", ok=True, rows=1 if sent else 0, detail=detail)
    except Exception as exc:  # noqa: BLE001 — ジョブ境界で握り runner に返す
        logger.exception("notify_digest が失敗")
        return JobResult(name="notify_digest", ok=False, rows=0, detail=f"失敗: {exc}")
