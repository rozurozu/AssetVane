"""AI Advisor の REST ルータ（軸2・相談チャット）。

設計の真実: docs/phase-specs/phase3-spec.md §6.3・ADR-014/015/024/025。

`POST /chat`（api.md §4）。CORE（不変・リポジトリ）＋ POLICY（DB）＋ 文脈（直近 journal）＋
画面コンテキスト（軽量ヒント）を build_messages で組み、Tool ループ（service.run_tool_loop）で
事実を Tool 経由で引きながら最終応答を返す（ADR-014）。サーバはステートレスで、会話履歴は
frontend が保持し毎ターン messages 配列で送る（ADR-024・§6.4）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from fastapi import APIRouter, HTTPException, Request
from openai import OpenAIError
from pydantic import BaseModel

from app.advisor.core_prompt import CORE
from app.advisor.engine import resolve_face, run_turn
from app.advisor.journaling import (
    build_watchlist_candidates_from_tool_runs,
    persist_card_ops_from_tool_runs,
    persist_journal_from_tool_runs,
    persist_trade_proposals_from_tool_runs,
)
from app.advisor.llm import CostGuardError
from app.advisor.prompt_builder import Message, ScreenContext, build_messages
from app.advisor.service import run_turn_cancellable
from app.advisor.tools.registry import CURRENT_PHASE
from app.db import repo
from app.db.engine import get_engine
from app.services import policy as policy_service
from app.services.knowledge_cards import load_card_texts_for_injection
from app.services.llm_config import FaceNotConfiguredError

router = APIRouter(tags=["advisor"])


class ChatRequest(BaseModel):
    """`POST /chat` のリクエスト（spec §6.3）。messages は user/assistant のみ（system 不可）。"""

    messages: list[Message]
    context: ScreenContext | None = None  # 画面コンテキスト（軽量ヒント・ADR-025）


class ToolRun(BaseModel):
    """チャットが呼んだ Tool の記録（UI 可視化用・spec §4.2）。結果の数値は載せない（ADR-025）。"""

    name: str
    args: dict[str, object] | None = None


class WatchlistCandidate(BaseModel):
    """propose_watchlist が提示したウォッチ候補 1 件（ADR-080・lib/api WatchlistCandidate と 1:1）。

    UI がチェックリストで見せ、ユーザーが選んで `POST /watchlist` する（AI は追加しない）。
    reason は追加時に watchlist の note に焼く元（空可）。company_name は backend が解決した社名。
    """

    code: str
    company_name: str
    reason: str


class ChatResponse(BaseModel):
    """`POST /chat` のレスポンス（spec §6.3）。{reply} 契約は維持し tool_runs を足すだけ。

    journal_id: チャットで submit_journal を呼んで投資日記に記録できたときの id（ADR-029）。
    呼ばれなかった・observations 空でスキップしたときは None。frontend の「日記に残した」表示用。
    card_ids: チャットで propose_card を呼んで起票した知識ノート draft の id（ADR-062 追補/065）。
    壁打ち→合意→起票のフィードバックを frontend がインライン表示する（journal_id と同型）。
    起票が無ければ空。active 化は人間が /cards で行う（承認制・ADR-009）。
    watchlist_candidates: チャットで propose_watchlist を呼んで提示したウォッチ候補（ADR-080）。
    frontend がチェックリストで見せ、ユーザーが選んで `POST /watchlist` する（追加は UI 側＝AI は
    watchlist を書かない）。呼ばれなければ空。surfacing は昼 router だけ＝夜 nightly は no-op。
    """

    reply: str
    tool_runs: list[ToolRun] = []
    journal_id: int | None = None
    card_ids: list[int] = []
    watchlist_candidates: list[WatchlistCandidate] = []


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request) -> ChatResponse:
    """相談チャット。POLICY/文脈/画面コンテキストを組み、Tool ループで応答を返す（spec §6.3）。

    送信中キャンセル（ADR-072）: run_turn（LLM ループ）を run_turn_cancellable でタスク化し、
    frontend の中止（fetch abort）→ クライアント切断を request.is_disconnected で検知したら
    LLM 呼び出しごと打ち切る。切断時は末尾の永続化（journal/proposals/cards）に到達しないので
    中途半端な起票は残らない（副作用は末尾集約＝下記）。
    """
    # POLICY・直近 journal・投資家プロファイルは読み取り接続で引く（ADR-005）。
    with get_engine().connect() as conn:
        policy = policy_service.get_policy(conn)
        recent = repo.get_recent_journal_summary(conn)
        profile = repo.get_investor_profile(conn)["body"]  # 記述の第3層（鏡・反追従・ADR-082）

    # 最新のユーザー発話を retrieval キーに知識カードを引く（ADR-062・ambient＋意味検索）。
    query = next((m.content for m in reversed(req.messages) if m.role == "user"), None)
    # 見ている銘柄（focus.code）の銘柄ノートは exact-match で無条件注入する（ADR-062 追補・③(1)）。
    # FocusRef は type=stock/signal のとき code を持つ（market は運ばない＝code 一致で衝突
    # しない・②）。
    focus = req.context.focus if req.context else None
    focus_code = focus.code if focus and focus.type in ("stock", "signal") and focus.code else None
    messages = build_messages(
        core_prompt=CORE,
        policy=policy,
        conversation=req.messages,
        screen_context=req.context,
        knowledge_cards=await load_card_texts_for_injection(query, focus_code=focus_code),
        recent_journal=recent,
        investor_profile=profile,
    )

    # 面（provider/model）は engine が source="chat" から解決する（ADR-058）。未設定なら明示エラー
    # （対話チャットなので Discord 通知はしない＝ADR-018・確定8）。journal の監査 model にも使う。
    try:
        face = resolve_face("chat")
    except FaceNotConfiguredError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"chat 面の LLM が未設定です（/settings で provider/model を割り当て）: {exc}",
        ) from exc

    try:
        # 送信中キャンセル（ADR-072）: 切断監視つきで run_turn を走らせる。切断検知なら None。
        result = await run_turn_cancellable(
            run_turn(messages, phase=CURRENT_PHASE, source="chat"),
            is_disconnected=request.is_disconnected,
        )
    except CostGuardError as exc:
        # 月額コスト上限超過（block）。frontend が detail を吹き出しに出す（spec §7.1・ADR-028）。
        raise HTTPException(
            status_code=429,
            detail=f"LLM 月額上限超過のため応答できません: {exc}",
        ) from exc
    except OpenAIError as exc:
        # 対話的なチャットなので Discord 通知はしない（あれは無人バッチ＝ADR-018）。
        # OpenAIError=API 経路の接続失敗。502 で返し frontend が再試行を促す。
        raise HTTPException(
            status_code=502,
            detail=f"LLM への接続に失敗しました（provider / base_url を確認）: {exc}",
        ) from exc

    if result is None:
        # クライアント切断（中止）。副作用（journal/proposals/cards）は下の末尾トランザクションに
        # 集約されており、ここに到達しないので未実行のまま＝中途半端な起票は残らない（ADR-072）。
        # クライアントは既に切断済みで body は破棄されるため、空応答を返して掃除だけする。
        return ChatResponse(reply="")
    reply, tool_runs = result

    response_tool_runs: list[ToolRun] = []
    for run in tool_runs:
        name = run.get("name")
        if not isinstance(name, str):
            continue
        args_raw = run.get("args")
        args = cast(dict[str, object], args_raw) if isinstance(args_raw, dict) else None
        response_tool_runs.append(ToolRun(name=name, args=args))

    # チャットが submit_journal を呼んだときだけ投資日記に記録する（明示要求時のみ＝ADR-029）。
    # propose_trade（ADR-052）は journal とは独立に buy/sell 提案を起票する＝submit が無くても
    # 起票するが、journal は has_submit のときだけ書く（「明示 submit がなければ日記は残さない」
    # 不変条件を保つ）。通常ターン（どちらも無し）では書き込み接続を開かない。橋渡しは nightly と
    # 共通の journaling サービスに一本化（W2＝begin() で journal＋proposal を atomic に束ねる）。
    journal_id: int | None = None
    card_ids: list[int] = []
    has_submit = any(r.get("name") == "submit_journal" for r in tool_runs)
    has_trade = any(r.get("name") == "propose_trade" for r in tool_runs)
    # ADR-062 追補: 知識カードの起票/weight 変更（承認制）。propose_card は draft 起票、
    # adjust_card_weight は proposals(kind=card_weight) を起票（人間が /cards・/proposals で承認）。
    has_card_op = any(r.get("name") in ("propose_card", "adjust_card_weight") for r in tool_runs)
    if has_submit or has_trade or has_card_op:
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        with get_engine().begin() as conn:
            if has_submit:
                journal_id = persist_journal_from_tool_runs(
                    conn,
                    tool_runs=tool_runs,
                    reply=reply,
                    source="chat",
                    date=today,
                    situation_briefing=None,  # 軸2 は画面コンテキストのみで監査 briefing は持たない
                    policy=policy,
                    llm_model=face.model,  # 面別に解決された実 model を監査に残す（ADR-058）
                )
            # buy/sell 提案を起票（journal_id があれば紐付け・無ければ独立＝journal_id=None）。
            persist_trade_proposals_from_tool_runs(
                conn, tool_runs=tool_runs, date=today, journal_id=journal_id
            )
            # 知識カードの起票/weight 変更を起票（ADR-062 追補・同一トランザクション）。
            # 起票した draft id を card_ids として frontend に返す（インライン表示・ADR-065）。
            card_ops = persist_card_ops_from_tool_runs(conn, tool_runs=tool_runs, date=today)
            card_ids = card_ops["cards"]

    # ウォッチ候補の surfacing（ADR-080）。propose_watchlist を呼んだときだけ、候補（code→社名解決・
    # 未知 drop）を組んで返す。**永続はしない**＝追加はユーザーが UI で選んで POST /watchlist する。
    # この surfacing は昼 router だけに配線するので、夜 nightly が呼んでも no-op（構造保証）。
    # 読み取りのみ（get_stock）なので begin() でなく connect() で、上の書き込み境界とは分ける。
    watchlist_candidates: list[WatchlistCandidate] = []
    if any(r.get("name") == "propose_watchlist" for r in tool_runs):
        with get_engine().connect() as conn:
            watchlist_candidates = [
                WatchlistCandidate(**c)
                for c in build_watchlist_candidates_from_tool_runs(conn, tool_runs=tool_runs)
            ]

    return ChatResponse(
        reply=reply,
        tool_runs=response_tool_runs,
        journal_id=journal_id,
        card_ids=card_ids,
        watchlist_candidates=watchlist_candidates,
    )
