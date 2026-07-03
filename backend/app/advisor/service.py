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

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal

from sqlalchemy import Connection

from app.advisor.llm import complete
from app.advisor.tools.registry import CURRENT_PHASE, REGISTRY, openai_tools
from app.db import repo
from app.services.llm_config import FaceNotConfiguredError, ResolvedFace, resolve_face
from app.services.policy import DEFAULT_POLICY, encode_policy_field, normalize_policy_row

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# (a) Tool dispatch ループ（spec §4.2）
# ---------------------------------------------------------------------------


def _tool_result_default(o: object) -> object:
    """Tool 結果を LLM へ渡す JSON 化の最終防波堤（ADR-014・保険）。

    handler の返り値は JSON-safe な素の型に限るのが規約（[[advisor-tool-pattern]]）で、Decimal を
    生む出所（repo の window 関数 percent_rank 等）は [[backend-repo-pattern]] で Float 化して断つ。
    本関数はその規約が破れても run_tool_loop の json.dumps が 500 で落ちないための保険（＝出所を
    断つのが本命）。既知型は素直に正規化し、未知型は str に倒しつつ warning で顕在化する
    （握りつぶさない）。
    """
    if isinstance(o, Decimal):
        return float(o)
    if isinstance(o, (date, datetime)):  # datetime は date のサブクラス
        return o.isoformat()
    item = getattr(o, "item", None)  # numpy scalar 等（0 次元 → Python ネイティブ）
    if callable(item):
        return item()
    logger.warning("Tool 結果に JSON 非対応の型: %s（str に倒す）", type(o).__name__)
    return str(o)


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
    face: ResolvedFace,
    phase: int = CURRENT_PHASE,
    source: str = "chat",
    max_rounds: int = 6,
    tool_names: set[str] | frozenset[str] | None = None,
) -> tuple[str, list[dict[str, object]]]:
    """build 済み messages を面別 provider/model で LLM に投げ、最終テキストと tool_runs を返す。

    （spec §4.2・ADR-014/025/058）。face は engine が resolve_face で解決して渡す。

    1. resp = complete(messages, face, tools=openai_tools(phase, allow=tool_names), source=source)
    2. resp.tool_calls がある限り（max_rounds まで）:
       - 各 tool_call の handler を呼ぶ（未知 name は {"error": ...}・落とさない）
       - assistant の tool_calls 記録 → 各結果を {"role":"tool", ...} で messages に append
       - tool_runs に {"name", "args"} を蓄積（**結果値は載せない**＝ADR-025）
       - 再度 complete
    3. max_rounds 超過時は打ち切り、実 content を返す（無ければ nightly は ""・chat は定型文）。

    tool_names を渡すと LLM に見せる Tool をその集合に絞る（reviewer の toolset 制限＝ADR-081）。
    既定 None は phase の全 Tool（chat/nightly は従来どおり）。

    戻り値: (最終テキスト, tool_runs)。tool_runs は [{name, args}]。
    """
    tools = openai_tools(phase, allow=tool_names)
    tool_runs: list[dict[str, object]] = []

    resp = await complete(messages, face=face, tools=tools, source=source)
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
                    "content": json.dumps(result, ensure_ascii=False, default=_tool_result_default),
                }
            )
            # tool_runs には名前と引数だけ（結果の数値は載せない＝ADR-025）。
            tool_runs.append({"name": tc.name, "args": tc.arguments})

        rounds += 1
        resp = await complete(messages, face=face, tools=tools, source=source)

    if resp.tool_calls and rounds >= max_rounds:
        # 上限到達で Tool 要求が続く → 打ち切る。実 content があればそれを返す。content が空のときの
        # 合成プレースホルダは reply→observations に化けて縮退検知（ADR-018）を素通りし、nightly で
        # 偽成功＋ゴミ journal を残す（#13）。よって nightly は "" を返し縮退（journal スキップ）に
        # 倒し、chat は利用者向けの定型文を出す（空応答の UX を避ける）。
        if resp.content:
            return resp.content, tool_runs
        fallback = "" if source == "nightly" else "（応答が長すぎるため打ち切りました）"
        return fallback, tool_runs

    return resp.content or "", tool_runs


async def run_turn_cancellable(
    coro: Awaitable[tuple[str, list[dict[str, object]]]],
    *,
    is_disconnected: Callable[[], Awaitable[bool]],
    poll_seconds: float = 0.25,
) -> tuple[str, list[dict[str, object]]] | None:
    """coro（run_turn）を実行しつつクライアント切断を監視する（送信中キャンセル・ADR-072）。

    チャットの LLM ループ（run_turn）はタスク化して走らせ、poll_seconds ごとに is_disconnected を
    見る。切断を検知したらタスクを cancel し（CancelledError が httpx の in-flight リクエストへ伝播
    して LLM 呼び出し自体を止める）、None を返す。呼び出し側（router）は None のとき末尾の永続化
    （journal/proposals/cards）をスキップする＝副作用は末尾集約なので中途半端な起票は残らない。

    - coro が正常終了/例外送出したら、監視より先にそれを拾って返す/再送出する（既存の
      CostGuardError/OpenAIError 分岐が router 側で効くように、例外は握らない）。
    - is_disconnected は callable で受け、service 層に web フレームワーク依存を持ち込まない
      （router が Starlette の request.is_disconnected を渡す）。
    """
    task = asyncio.ensure_future(coro)
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=poll_seconds)
            if task in done:
                return task.result()  # 例外はここで再送出（握らない）
            if await is_disconnected():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                return None
    except BaseException:
        # 監視側が cancel される等の予期せぬ伝播でも LLM タスクを取り残さない。
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        raise


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
      DB 形への変換（sector_caps/exclusions の json.dumps・no_leverage の 0/1）は
      PUT /policy と共通の encode_policy_field に委ねる（ADR-013・入口間ドリフト防止）。
      LLM 由来の型不一致 `to` は ValueError → router 境界で 409 に翻訳される。
    - policy 更新後の行をまるごと JSON 化し、当日の advisor_journal に snapshot を残す
      （journal_id 指定があればその行を上書き、無ければ新規 1 件を起票する）。
      snapshot は dumps 前に normalize_policy_row で JSON 列を型へ直す（単エンコード）。

    write を含むため、呼び出し側が `with get_engine().begin() as conn:` で渡すこと。
    """
    field = change.get("field")
    if not isinstance(field, str) or not field:
        raise ValueError("policy 変更には field が必要です。")
    # 未知 field は upsert_policy が SQL レベルで不可解に落ちるため境界で弾く（防御・ADR-013）。
    if field not in DEFAULT_POLICY:
        raise ValueError(f"未知の policy フィールド: {field}")
    if "to" not in change:
        raise ValueError("policy 変更には to（変更後の値）が必要です。")

    # 1 列だけ upsert（部分更新・id 固定）。dict/list のまま TEXT 列にバインドすると
    # sqlite3 エラーで落ちるため、必ず DB 形に変換してから書く。
    repo.upsert_policy(conn, {field: encode_policy_field(field, change["to"])})

    # 更新後 policy をまるごと snapshot 用 JSON に（JSON 列は型へ直してから dumps＝単エンコード）。
    updated = repo.get_policy(conn)
    snapshot = (
        json.dumps(normalize_policy_row(updated), ensure_ascii=False)
        if updated is not None
        else None
    )
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    reason = change.get("reason")
    observations = f"方針を更新（{field}）" + (f": {reason}" if reason else "")

    if journal_id is not None:
        # 既存 journal（夜の生成元）に snapshot を焼く（Table 直参照は repo に閉じる・ADR-002）。
        repo.set_journal_policy_snapshot(conn, journal_id, snapshot)
    else:
        # チャット承認など journal が無い入口は当日 journal を 1 件起票して snapshot を残す。
        # 監査 model は source 面の解決値を best-effort で（未設定なら None＝この snapshot は LLM
        # 生成物ではなく方針適用の記録なので model は付帯情報・ADR-058）。
        try:
            journal_model: str | None = resolve_face(conn, source).model
        except FaceNotConfiguredError:
            journal_model = None
        repo.insert_journal(
            conn,
            date=today,
            source=source,
            observations=observations,
            policy_snapshot=snapshot,
            llm_model=journal_model,
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

    # approved かつ card_weight なら知識カードの weight を適用（ADR-062 追補・承認制の変更）。
    if decision == "approved" and proposal.get("kind") == "card_weight":
        _apply_card_weight(conn, proposal.get("body"))


def _apply_card_weight(conn: Connection, body_raw: object) -> None:
    """proposals.body（{card_id, weight}）から知識カードの weight を適用する（ADR-062 追補）。

    壊れた body・不正な値・weight<=0 は適用せず skip（落とさない・ADR-018）。
    """
    try:
        data = json.loads(body_raw) if isinstance(body_raw, str) else body_raw
    except (TypeError, ValueError):
        logger.warning("card_weight: body が JSON でない。適用せず skip")
        return
    if not isinstance(data, dict):
        return
    try:
        card_id = int(data["card_id"])
        weight = float(data["weight"])
    except (KeyError, TypeError, ValueError):
        logger.warning("card_weight: card_id/weight が不正。適用せず skip")
        return
    if weight <= 0:
        return
    repo.update_card_weight(conn, card_id, weight)


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
