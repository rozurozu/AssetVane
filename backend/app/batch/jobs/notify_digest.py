"""夜間バッチ: Signal Beacon 通知 digest ジョブ（phase6-spec.md §3・ADR-007/014/016/018/051/067）。

NIGHTLY_JOBS の**末尾**（取得 → signals → 夜の分析AI の後＝事実と提案が揃ってから）で呼ばれ、
当日の注目・提案を **1 通の Discord digest** に束ねて送る（spec §1）。

設計の芯:
- **注目シグナルは AI 選別（ADR-067）**。旧・score 閾値 Top N 抽出（`_is_alert` 系）は
  廃した。Python が合流ゲートで候補集合を組み（services/notable.py）、夜の分析AI が
  submit_notable_stocks で厳選した銘柄を notable_picks に永続する。digest はそれを読んで載せるだけ
  （AI に数値を計算させない＝ADR-014／手法閾値は quant・service＝ADR-016）。
- **保有銘柄の悪材料は決定論で必ず出す（ADR-051 維持）**。AI 選別の拾い忘れに依らない安全装置として
  polarity='negative'・24h 窓の悪材料を独立セクションで先頭付近に置く（1900 字截断から守る）。
- **日付は UTC で統一**する（夜の分析AI が journal/notable_picks を UTC 日付で書くため）。
- **冪等**（spec §3・ADR-002/018）。notify_key='digest:<UTC日付>' で 1 日 1 通。
- 例外は握って JobResult(ok=False)（runner が error 通知）。通知失敗で本処理は巻き込まない。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Connection

from app.batch import notify
from app.batch.runner import JobResult
from app.config import settings
from app.db import repo
from app.db.engine import get_engine
from app.services.notable import build_notable_candidates

logger = logging.getLogger(__name__)

# ②保有銘柄の悪材料アラートで digest に出す最大件数（残りは「ほか N 件」・ADR-051）。
_HOLDING_RISK_MAX = 5


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


def _holding_risk_lines(conn: Connection) -> list[str]:
    """②保有銘柄の悪材料アラート行を組み立てる（ADR-051・能動配信・決定論で維持＝ADR-067）。

    既定ポートフォリオ（list_portfolios の先頭・裁定 L-9）の holdings に紐づく stock 層ニュースの
    うち、polarity='negative' かつ fetched_at が直近 24h の悪材料を最大 _HOLDING_RISK_MAX 件＋
    残件数で返す。fetched_at 24h 窓で「同じ悪材料を翌晩再掲しない」を自然に実現する（ADR-051）。
    悪材料が無ければ空リスト（呼び出し側がセクションごと省略）。社名は repo の LEFT JOIN で
    補完済み。AI 選別（notable_picks）の拾い忘れに依らない安全装置（ADR-067）。
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


def _notable_lines(conn: Connection, today: str) -> tuple[list[str], int]:
    """注目シグナル（夜AI 選別の notable_picks）を digest 行に整形する（ADR-067）。

    source='nightly' の当日 notable_picks を起票順で読み、社名(コード) — 理由 の 1 行に整形する。
    notable_digest_max 件まで表示し、残りは「…ほか N 件」。戻り値は (行リスト, 選別件数)。空なら
    「注目シグナル: なし」1 行（件数 0）。生のイベント一覧は出さない（安全網は DB＋/signals）。
    """
    picks = repo.list_notable_picks_for_date(conn, today, source="nightly")
    if not picks:
        return (["🔔 注目シグナル: なし"], 0)

    top_n = max(1, settings.notable_digest_max)
    shown = picks[:top_n]
    remaining = len(picks) - len(shown)
    lines = [f"🔔 注目シグナル（AI 選別・{len(picks)} 件）"]
    for p in shown:
        name = p.get("company_name") or p["code"]
        reason = (p.get("reason") or "").strip()
        lines.append(f"・{name} ({p['code']})" + (f" — {reason}" if reason else ""))
    if remaining > 0:
        lines.append(f"　…ほか {remaining} 件")
    return (lines, len(picks))


def _reviewer_drafts_line(conn: Connection, today: str) -> str | None:
    """当夜 reviewer が起票した知識ノート下書きの件数を 1 行にする（ADR-081・Q9）。

    情報行（failed_index 同型）＝has_content には含めない（下書きだけの夜に digest を新規発火
    させない）。0 件なら None（行を出さない）。当夜作った draft は /cards で承認できる。
    """
    n = repo.count_reviewer_drafts_on(conn, today)
    if n <= 0:
        return None
    return f"🗂 経験レビューの知識ノート下書き {n} 件（/cards で確認）"


def _profile_notes_line(conn: Connection, today: str) -> str | None:
    """当夜 profiler が起票した投資家プロファイルの傾向メモ件数を 1 行にする（ADR-082）。

    情報行（reviewer 下書きと同型）＝has_content には含めない（下書きだけの夜に digest を新規発火
    させない）。0 件なら None（行を出さない）。当夜作った傾向メモは /profile で承認できる。
    """
    n = repo.count_profile_notes_on(conn, today)
    if n <= 0:
        return None
    return f"🪞 投資家プロファイルの傾向メモ下書き {n} 件（/profile で確認）"


def build_digest_content(conn: Connection, today: str) -> str | None:
    """当日の注目（AI 選別）＋⑦＋夜AI 提案を 1 通の digest 本文に組み立てる（spec §3・ADR-067）。

    today は UTC 日付 'YYYY-MM-DD'（journal/notable_picks と揃える）。本文は【決定論: 保有の悪材料
    （ADR-051）】＋【AI 選別の注目】＋【⑦リバランス】＋【夜AI 提案】＋【極薄サマリ】。
    ALWAYS_DAILY_DIGEST=False かつ保有悪材料・注目・⑦・提案すべて無しなら None（送信スキップ）。
    True（既定）なら検知ゼロでもサマリを返す。
    """
    # 合流ゲートの counts（極薄サマリの素・AI 選別と同じ DB 状態を決定論的に読む＝ADR-067）。
    # best-effort: ここは末尾サマリの表示件数のためだけの再計算なので、失敗しても ②保有悪材料
    # アラート（ADR-051・能動配信の安全網）や本文送信を巻き添えにしない（#18）。失敗時は counts を
    # 空にしてサマリの件数を 0/省略にとどめる。
    try:
        counts = build_notable_candidates(conn).get("counts") or {}
    except Exception:  # noqa: BLE001 — サマリ件数の best-effort。失敗しても digest 本体は送る
        logger.warning("notify_digest: 候補 counts の再計算に失敗（サマリ件数を省略・#18）")
        counts = {}

    # 注目（AI 選別・notable_picks）。
    notable_lines, n_picks = _notable_lines(conn, today)

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

    # ②保有銘柄の悪材料（能動配信の主目的・ADR-051 決定論で維持）。has_content に含めて「悪材料が
    # ある夜は always_daily_digest=False でも送る」を満たす。
    risk_lines = _holding_risk_lines(conn)

    # 経験レビューの下書き件数（ADR-081・Q9）。情報行なので has_content には含めない。
    reviewer_drafts = _reviewer_drafts_line(conn, today)
    # 投資家プロファイルの傾向メモ件数（ADR-082）。同じく情報行（has_content に含めない）。
    profile_notes = _profile_notes_line(conn, today)

    has_content = bool(n_picks or rebalance or proposal or risk_lines)
    if not has_content and not settings.always_daily_digest:
        return None  # 好機がある日だけ送る設定で、何も無い日（[OPEN-N]）

    # --- 本文組み立て ---
    lines: list[str] = [f"**📊 AssetVane 朝のダイジェスト（{today}）**", ""]

    # ②は能動配信の主眼。Discord の 1900 字截断で末尾が切れても残るよう注目シグナルより前に置く。
    if risk_lines:
        lines.extend(risk_lines)
        lines.append("")

    # 注目シグナル（AI 選別）。
    lines.extend(notable_lines)
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

    if reviewer_drafts:
        lines.append(reviewer_drafts)
        lines.append("")

    if profile_notes:
        lines.append(profile_notes)
        lines.append("")

    if failed_index:
        lines.append(failed_index)
        lines.append("")

    # 当日サマリ（検知ゼロでも届く＝完了条件・ADR-067 の極薄サマリ）。
    summary = (
        f"— サマリ: signals {counts.get('signals', 0)} 件 / "
        f"候補 {counts.get('candidates', 0)} 件 / AI 選別 {n_picks} 件"
    )
    if counts.get("dropped"):
        summary += f" / 候補上限で省略 {counts['dropped']} 件"
    lines.append(summary)
    return "\n".join(lines)


def run() -> JobResult:
    """当日の注目と AI 提案を 1 通の Discord digest に束ねて冪等送信する（spec §3・ADR-067）。

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
