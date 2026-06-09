"""tool_runs → advisor_journal/proposals の橋渡し共通サービス（ADR-018/029/013）。

設計の真実: docs/phase-specs/phase3-spec.md §5・§6.5・ADR-029。

軸1（夜の分析AI＝nightly）と軸2（昼の相談チャット＝router の /chat）の両方が、Tool ループの
tool_runs から `submit_journal` の引数を拾い、advisor_journal を 1 件記録し、必要なら方針変更
proposal を起票する。この「橋渡し」を 1 か所に集約して真実の所在地を一本化する（同じ事実は
1 か所で計算して複数の出力先に配る＝backend-service-quant-pattern）。

接続規約（W2・backend-repo-pattern）: 本サービスは `conn` を受け取り insert を実行するだけで、
自分では commit しない。呼び出し側（nightly ジョブ／router）が `with get_engine().begin()` で
トランザクション境界を所有し、journal＋proposal を 1 トランザクションに atomic に束ねる。

依存方向: 本モジュールは router を import しない（循環回避・片方向依存）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import Connection

from app.advisor.tools.schemas import coerce_policy_change
from app.db import repo

logger = logging.getLogger(__name__)


def _extract_submit_journal(
    tool_runs: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """tool_runs から submit_journal の args を取り出す（最後の呼び出しを採用・spec §5）。

    複数回呼ばれた場合は最後の args を採用する（最終意思を尊重）。submit_journal が一度も
    呼ばれていなければ None を返す（呼び出し側は reply フォールバックに倒す＝ADR-018）。
    """
    submitted: dict[str, Any] | None = None
    for run in tool_runs:
        if run.get("name") == "submit_journal":
            args = run.get("args")
            if isinstance(args, dict):
                submitted = args
    return submitted


def persist_journal_from_tool_runs(
    conn: Connection,
    *,
    tool_runs: list[dict[str, Any]],
    reply: str | None,
    source: str,
    date: str,
    situation_briefing: str | None,
    policy: dict[str, Any] | None,
    llm_model: str | None,
) -> int | None:
    """tool_runs から submit_journal を拾い journal 1件記録・proposal 起票（ADR-018/029/013）。

    戻り値: journal_id（記録）/ None（observations 空でスキップ＝ADR-018）。

    手順（nightly の既存橋渡しロジックを移植・一本化）:
    1. tool_runs から submit_journal の最終 args を拾う。
    2. observations/proposal/proposed_policy_change を組む（submit が無くても reply 非空なら
       observations=reply にフォールバック＝既存挙動を保つ・ADR-018）。
    3. observations が空（縮退）なら journal を書かず None を返す（不変条件・ADR-018）。
    4. coerce_policy_change で単一 {field,to} に正規化（多列 patch・非 dict は None に倒し
       適用不能な提案を起票しない＝ADR-013/018）。
    5. insert_journal（date/source/situation_briefing/policy_snapshot を JSON 文字列で）。
    6. proposed_change があれば insert_proposal（kind=policy_change・pending・journal_id 紐付け）。

    接続規約（W2）: commit はしない。呼び出し側が `with get_engine().begin()` で境界を所有する。
    """
    submitted = _extract_submit_journal(tool_runs)
    if submitted is not None:
        observations = str(submitted.get("observations") or reply or "")
        proposal = submitted.get("proposal")
        raw_change = submitted.get("proposed_policy_change")
    else:
        # submit_journal 不呼び出しでも reply 非空なら正常（フォールバックを否定しない・ADR-018）。
        observations = reply or ""
        proposal = None
        raw_change = None

    # 縮退（例外なし・observations 空＝実質何も書くことがない）は journal を残さない（ADR-018）。
    if not observations.strip():
        return None

    # 変更案を単一 {field,to} に正規化（多列 patch 等は None＝適用不能な提案を起票しない）。
    # 正規化済み dict は apply_policy_change がそのまま食える形（ADR-013/018・U-10 裁定①）。
    proposed_change = coerce_policy_change(raw_change)
    if raw_change is not None and proposed_change is None:
        logger.warning(
            "journaling: proposed_policy_change が単一 {field,to} 形でない。"
            "提案は起票せず journal のみ記録する（ADR-013/018）。"
        )

    proposed_change_json = (
        json.dumps(proposed_change, ensure_ascii=False) if proposed_change else None
    )

    journal_id = repo.insert_journal(
        conn,
        date=date,
        source=source,
        situation_briefing=situation_briefing,
        observations=observations,
        proposal=proposal if isinstance(proposal, str) else None,
        proposed_policy_change=proposed_change_json,
        policy_snapshot=(json.dumps(policy, ensure_ascii=False) if policy is not None else None),
        llm_model=llm_model,
    )

    # 方針変更案があれば承認制の提案として起票する（kind=policy_change・pending・§6.5）。
    # proposed_change は正規化済み（None なら起票せず＝適用不能な提案を回避）。
    if proposed_change:
        reason = proposed_change.get("reason")
        repo.insert_proposal(
            conn,
            created_date=date,
            kind="policy_change",
            body=proposed_change_json,
            rationale=str(reason) if reason else None,
            status="pending",
            journal_id=journal_id,
        )

    return journal_id
