"""AI Advisor の接着層 — Tool dispatch ループと状態遷移（spec §4.2・§8.1・§6.5）。

設計の真実: docs/phase-specs/phase3-spec.md §4.2・§5・§6.5・§8.1・ADR-013/014/018/025。

- `run_tool_loop`: build 済みの messages を LLM に投げ、tool_calls を registry の handler で
  解決して最終テキストと tool_runs を返す（軸1 夜AI・軸2 チャット共通）。tool_runs には
  呼んだ Tool 名と引数だけを載せ、**結果の数値は載せない**（ADR-025）。
- `apply_policy_change` / `resolve_proposal`: proposals の状態遷移と policy 更新を担う
  共通ロジック（承認順制御 depends_on・policy_change なら当日 journal に snapshot）。
  write を含むため、呼び出し側が `with get_engine().begin() as conn:` で conn を渡す
  （repo の書き込み規律と同じ）。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import Connection

from app.advisor.llm import complete
from app.advisor.tools.registry import CURRENT_PHASE, REGISTRY, openai_tools
from app.config import settings
from app.db import repo

# ---------------------------------------------------------------------------
# (a) Tool dispatch ループ（spec §4.2）
# ---------------------------------------------------------------------------


def _assistant_tool_call_record(content: str | None, tool_calls: list) -> dict[str, object]:
    """LLM の tool_calls 要求を OpenAI 形式の assistant メッセージに直す（spec §4.2）。

    次ターンの complete に「どの Tool を要求したか」を伝えるための記録。
    function.arguments は JSON 文字列で詰める（OpenAI 互換）。
    """
    return {
        "role": "assistant",
        "content": content or "",
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                },
            }
            for tc in tool_calls
        ],
    }


async def run_tool_loop(
    messages: list[dict[str, object]],
    *,
    phase: int = CURRENT_PHASE,
    source: str = "chat",
    max_rounds: int = 6,
) -> tuple[str, list[dict[str, object]]]:
    """build 済み messages を LLM に投げ、tool_calls を解決して最終テキストと tool_runs を返す。

    （spec §4.2・ADR-014/025）

    1. resp = complete(messages, tools=openai_tools(phase), source=source)
    2. resp.tool_calls がある限り（max_rounds まで）:
       - 各 tool_call の handler を呼ぶ（未知 name は {"error": ...}・落とさない）
       - assistant の tool_calls 記録 → 各結果を {"role":"tool", ...} で messages に append
       - tool_runs に {"name", "args"} を蓄積（**結果値は載せない**＝ADR-025）
       - 再度 complete
    3. max_rounds 超過時は打ち切り、最後の content（無ければ定型文）を返す。

    戻り値: (最終テキスト, tool_runs)。tool_runs は [{name, args}]。
    """
    tools = openai_tools(phase)
    tool_runs: list[dict[str, object]] = []

    resp = await complete(messages, tools=tools, source=source)
    rounds = 0
    while resp.tool_calls and rounds < max_rounds:
        # 1) この往復で要求された tool_calls を assistant 記録として積む（OpenAI 形式）。
        messages.append(_assistant_tool_call_record(resp.content, resp.tool_calls))

        # 2) 各 tool_call を解決し、結果を tool ロールで返す。
        for tc in resp.tool_calls:
            tool_def = REGISTRY.get(tc.name)
            if tool_def is None:
                result: dict[str, object] = {"error": "unknown tool"}
            else:
                result = await tool_def.handler(tc.arguments)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )
            # tool_runs には名前と引数だけ（結果の数値は載せない＝ADR-025）。
            tool_runs.append({"name": tc.name, "args": tc.arguments})

        rounds += 1
        resp = await complete(messages, tools=tools, source=source)

    if resp.tool_calls and rounds >= max_rounds:
        # 上限到達で Tool 要求が続いている → 打ち切る。最後の content があれば返す。
        return (
            resp.content or "（応答が長すぎるため打ち切りました）",
            tool_runs,
        )

    return resp.content or "", tool_runs


# ---------------------------------------------------------------------------
# (b) 状態遷移（spec §8.1・§6.5・ADR-013/018・決定4/B-8）
# ---------------------------------------------------------------------------


def apply_policy_change(
    conn: Connection,
    *,
    change: dict[str, object],
    source: Literal["chat", "nightly"],
    journal_id: int | None,
) -> None:
    """change={field, to, ...} を policy に適用し、当日の journal に policy_snapshot を残す。

    （spec §8.1・§6.5・ADR-013）

    - change は `{field, from?, to, reason?}` 形（submit_journal / proposals.body 由来）。
      `field`/`to` から `{field: to}` 1 列だけを upsert する（最適化に効く構造化コア）。
    - policy 更新後の行をまるごと JSON 化し、当日の advisor_journal に snapshot を残す
      （journal_id 指定があればその行を上書き、無ければ新規 1 件を起票する）。

    write を含むため、呼び出し側が `with get_engine().begin() as conn:` で渡すこと。
    """
    field = change.get("field")
    if not isinstance(field, str) or not field:
        raise ValueError("policy 変更には field が必要です。")
    if "to" not in change:
        raise ValueError("policy 変更には to（変更後の値）が必要です。")

    # 1 列だけ upsert（部分更新・id 固定）。
    repo.upsert_policy(conn, {field: change["to"]})

    # 更新後 policy をまるごと snapshot 用 JSON に。
    updated = repo.get_policy(conn)
    snapshot = json.dumps(updated, ensure_ascii=False) if updated is not None else None
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    reason = change.get("reason")
    observations = f"方針を更新（{field}）" + (f": {reason}" if reason else "")

    if journal_id is not None:
        # 既存 journal（夜の生成元）に snapshot を焼く。
        conn.execute(
            repo.advisor_journal.update()
            .where(repo.advisor_journal.c.id == journal_id)
            .values(policy_snapshot=snapshot)
        )
    else:
        # チャット承認など journal が無い入口は当日 journal を 1 件起票して snapshot を残す。
        repo.insert_journal(
            conn,
            date=today,
            source=source,
            observations=observations,
            policy_snapshot=snapshot,
            llm_model=settings.llm_model,
        )


def resolve_proposal(
    conn: Connection,
    proposal_id: int,
    *,
    decision: Literal["approved", "rejected"],
    outcome: str | None = None,
) -> None:
    """proposals.status を遷移する。approved かつ kind=policy_change なら policy を適用する。

    （spec §8.1・決定4/B-8・ADR-001/019）

    - proposal が存在しなければ KeyError（ルータが 404 に翻訳）。
    - approve で `depends_on` が指す提案が未 approved の間は ValueError（ルータが 409 に翻訳）。
    - buy/sell/rebalance の承認は約定を起こさない（status 遷移のみ・ADR-001/019）。

    write を含むため、呼び出し側が `with get_engine().begin() as conn:` で渡すこと。
    """
    proposal = repo.get_proposal(conn, proposal_id)
    if proposal is None:
        raise KeyError(f"提案 {proposal_id} は存在しません。")

    if decision == "approved":
        depends_on = proposal.get("depends_on")
        if depends_on is not None:
            parent = repo.get_proposal(conn, int(depends_on))
            if parent is None or parent.get("status") != "approved":
                raise ValueError(
                    f"先行する提案 {depends_on} が未承認のため、この提案は承認できません。"
                )

    # status を遷移（resolved_at は repo が UTC now を入れる）。
    repo.update_proposal_status(conn, proposal_id, decision, outcome=outcome)

    # approved かつ policy_change なら policy を適用し journal に snapshot（ADR-013）。
    if decision == "approved" and proposal.get("kind") == "policy_change":
        body_raw = proposal.get("body")
        change = _parse_change(body_raw)
        if change is not None:
            apply_policy_change(
                conn,
                change=change,
                source="chat",
                journal_id=proposal.get("journal_id"),
            )


def _parse_change(body_raw: object) -> dict[str, object] | None:
    """proposals.body（JSON 文字列 or dict）を policy 変更 dict に直す。

    パースできない・field/to を欠く場合は None（policy 適用をスキップ・落とさない）。
    """
    if body_raw is None:
        return None
    if isinstance(body_raw, dict):
        body = body_raw
    elif isinstance(body_raw, str):
        try:
            parsed = json.loads(body_raw)
        except (TypeError, ValueError):
            return None
        if not isinstance(parsed, dict):
            return None
        body = parsed
    else:
        return None
    if "field" not in body or "to" not in body:
        return None
    return body
