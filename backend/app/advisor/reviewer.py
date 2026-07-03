"""経験蒸留（reviewer 面）のオーケストレーション（ADR-081・テーマ B・自己改善ループ ④）。

設計の真実: docs/decisions.md ADR-081・tasks/hermes-transfer-2026-07-02.md §8。

夜バッチの distill_experience ジョブが駆動する。採点済み outcome（ADR-077）を教材に、reviewer 面の
Tool ループで「傾向 → 知識カード draft」を蒸留する（活性化は人間＝ADR-009）。nightly.py と同型:
build_messages → run_turn → persist_card_ops。差分は 3 点だけ:
1. **活動量ゲート**（ADR-081）＝新規 final（カーソル超）が閾値未満の晩は LLM を呼ばず skip。
2. **toolset 制限**（ADR-081）＝reviewer には最小 Tool 集合だけ見せ、末尾で card_ops だけ永続する
   （propose_trade/submit_journal 等は見せない・多重防御）。
3. **source 強制**＝propose_card の source を決定論で 'reviewer' に上書きする（/cards で由来識別）。

障害設計（ADR-018）: reviewer 面未設定は resolve_face が FaceNotConfiguredError を投げ、呼び出し側
（distill_experience ジョブ）が沈黙 skip（ok=True・triage/tagger 同型）にする。LLM ハード失敗は
run_turn からそのまま伝播し、ジョブが ok=False で runner 集約通知に乗せる。skip/失敗時はカーソルを
前進させない（材料を失わない）。conn は呼び出し側が begin() で渡す（W2）。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import Connection

from app.advisor.core_prompt import CORE
from app.advisor.engine import resolve_face, run_turn
from app.advisor.journaling import persist_card_ops_from_tool_runs
from app.advisor.prompt_builder import Message, build_messages
from app.advisor.tools.registry import CURRENT_PHASE, REVIEWER_TOOLSET
from app.config import settings
from app.db import repo
from app.services import experience
from app.services.knowledge_cards import load_card_texts_for_injection

logger = logging.getLogger(__name__)

# reviewer の定型指示文（ADR-081）。教材は Python が verbatim で末尾に注入する。核＝①十分な
# サンプルのある傾向だけを durable card 化し単発トレードから一般化しない（過学習足切りは整形段で
# Python が担保済み・ここは規律の言語化）②起票前に search_cards で近傍カードを検索し重複 draft を
# 作らない（近く有効なら adjust_card_weight で重み↑・近いが誤りなら↓・新規なら propose_card）
# ③数値は必ず教材の値を使い自分で計算しない（ADR-014）。カードは draft 起票のみで活性化は人間
# （ADR-009）＝乱発しない。
_REVIEWER_INSTRUCTION = (
    "あなたは夜間の『経験レビュアー』だ。AI が過去に出した提案（buy/sell）と注目選別を市場結果で"
    "採点した成績（下の教材）を読み、次の判断に効く知識だけを知識ノート（知識カード）の下書きに"
    "蒸留せよ。目的は自己改善＝『何が当たり何が外れたか』から再現性のある教訓を残すこと。\n\n"
    "規律:\n"
    "1. 十分なサンプル（教材の『傾向』バケット）に裏打ちされた一般化だけをノート化せよ。"
    "単発の当たり/外れから durable なノートを作るな（過学習を避ける）。一般化するときは"
    "どの傾向（source/kind/horizon と n・的中率）を根拠にしたか本文に書け。\n"
    "2. 起票の前に必ず search_cards で近い既存ノートを探せ。"
    "近くて今も有効なノートがあれば propose_card せず adjust_card_weight で重みを上げよ。"
    "近いが今回の結果で否定されたノートは重みを下げよ。新しい教訓のときだけ propose_card せよ。\n"
    "3. 数値は教材（Python が計算済み）の値をそのまま引用し、自分で計算し直すな。\n"
    "4. ノートは下書き（draft）で起票され、採用は人間が /cards で最終承認する。"
    "確信が持てる教訓だけを厳選し、乱発するな（本当に無ければ何も起票しなくてよい）。\n"
    "より深い文脈が要れば get_track_record（成績の集計）・search_judgments（過去の判断ログ）で"
    "掘ってよい。\n"
)


async def run_experience_distillation(conn: Connection) -> dict[str, object]:
    """採点済み outcome を教材に知識カード draft を蒸留する（ADR-081・自己改善ループ ④）。

    1. 活動量ゲート＝新規 final（カーソル超）が settings.reviewer_min_new_finals 未満なら skip。
    2. reviewer 面を解決（未設定は FaceNotConfiguredError→上位ジョブが沈黙 skip・ADR-018）。
    3. 教材構築（count≥min_samples の傾向＋新規 final の bookend＋直近 journal）。
    4. run_turn（source='reviewer'・toolset=REVIEWER_TOOLSET）で蒸留させる。
    5. persist_card_ops（source_override='reviewer'）で draft/重み提案を永続（W2）。
    6. 成功時のみカーソルを最新 final scored_at まで前進（skip/失敗では据え置き）。

    戻り値: {"ran", "reason", "drafts", "weight_proposals", "new_finals"}。ran=False は
    ゲート skip（ジョブは ok=True）。LLM 失敗は run_turn からそのまま伝播（ジョブが ok=False）。
    conn は呼び出し側（distill_experience ジョブ）が begin() で渡す。
    """
    today = datetime.now(UTC).strftime("%Y-%m-%d")

    new_finals = experience.count_new_finals(conn)
    threshold = settings.reviewer_min_new_finals
    if new_finals < threshold:
        reason = f"新規 final {new_finals} 件 < 閾値 {threshold}（蒸留 skip・カーソル据え置き）"
        logger.info("distill_experience: %s", reason)
        return {
            "ran": False,
            "reason": reason,
            "drafts": [],
            "weight_proposals": [],
            "new_finals": new_finals,
        }

    # 面を先に解決（未設定/宙づりは FaceNotConfiguredError→上位ジョブが沈黙 skip・ADR-018/058）。
    # ゲート通過後に解決するのは、新着ゼロの晩に面設定を要求しない（健全 no-op）ため。
    resolve_face("reviewer")

    material = experience.build_distillation_material(
        conn, min_samples=settings.reviewer_min_samples
    )
    instruction = _REVIEWER_INSTRUCTION + "\n" + experience.format_material_for_prompt(material)

    messages = build_messages(
        core_prompt=CORE,
        policy=repo.get_policy(conn),
        conversation=[Message(role="user", content=instruction)],
        screen_context=None,  # レビューに画面は無い（ADR-025）
        # ambient（市況/一般）カードのみ注入する。具体の近傍検索は reviewer 自身が
        # search_cards Tool で引く（ADR-062）。
        knowledge_cards=await load_card_texts_for_injection(None),
        recent_journal=material.get("recent_journal"),
    )

    # reviewer 面へ振り分け、最小 toolset だけ見せる（ADR-081）。LLM ハード失敗は握らず上位へ伝播。
    _reply, tool_runs = await run_turn(
        messages, phase=CURRENT_PHASE, source="reviewer", tool_names=REVIEWER_TOOLSET
    )

    # card_ops だけ永続する（多重防御＝propose_trade/submit_journal は toolset に無く、混ざっても
    # 拾わない）。source は決定論で 'reviewer' に強制（LLM の source 引数を信用しない・ADR-081）。
    ops = persist_card_ops_from_tool_runs(
        conn, tool_runs=tool_runs, date=today, source_override="reviewer"
    )

    # 成功時のみカーソルを前進（同一 begin 内で card 起票と atomic・ADR-081）。
    experience.advance_cursor(conn)

    drafts = ops["cards"]
    weight_proposals = ops["weight_proposals"]
    reason = (
        f"新規 final {new_finals} 件をレビュー → 下書き {len(drafts)} 件・"
        f"重み提案 {len(weight_proposals)} 件"
    )
    logger.info("distill_experience: %s", reason)
    return {
        "ran": True,
        "reason": reason,
        "drafts": drafts,
        "weight_proposals": weight_proposals,
        "new_finals": new_finals,
    }
