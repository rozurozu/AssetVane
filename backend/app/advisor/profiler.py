"""投資家プロファイル蒸留（profiler 面）のオーケストレーション（ADR-082・テーマ C・ループ ④）。

設計の真実: docs/decisions.md ADR-082・tasks/hermes-transfer-2026-07-02.md テーマ C。

夜バッチの distill_investor_profile ジョブが駆動する。取引台帳（transactions）から Python が計算した
行動の癖（手仕舞いの帰結・ディスポジション・関心集中）を教材に、profiler 面の Tool ループで
「癖 → 傾向メモ draft」を蒸留する（承認は人間＝ADR-009）。reviewer.py（ADR-081）と同型で、差分:
1. 教材が採点済み outcome でなく **投資家の行動信号**（services/investor_behavior）。
2. toolset は PROFILER_TOOLSET（propose_profile_note を allowlist_only で profiler にだけ露出）。
3. 使い方は **鏡・反追従**＝記述であって規範でない（迎合しない）。CORE の反追従節が助言側を縛る。
4. 既存プロファイルは **重複回避の参照のみ**（自己強化の材料にしない・根拠は台帳事実だけ）。

障害設計（ADR-018）: profiler 面未設定は resolve_face が FaceNotConfiguredError を投げ、ジョブが
沈黙 skip（ok=True・reviewer/triage/tagger 同型）。LLM ハード失敗は run_turn からそのまま伝播し
ジョブが ok=False で集約通知に乗せる。skip/失敗時はカーソルを前進させない（材料を失わない）。
conn は呼び出し側が begin() で渡す（W2）。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import Connection

from app.advisor.core_prompt import CORE
from app.advisor.engine import resolve_face, run_turn
from app.advisor.journaling import persist_profile_notes_from_tool_runs
from app.advisor.prompt_builder import Message, build_messages
from app.advisor.tools.registry import CURRENT_PHASE, PROFILER_TOOLSET
from app.config import settings
from app.db import repo
from app.services import investor_behavior
from app.services.knowledge_cards import load_card_texts_for_injection

logger = logging.getLogger(__name__)

# profiler の定型指示文（ADR-082）。教材（行動信号）は Python が verbatim で末尾に注入する。核＝
# ①記述であって規範でない（policy を書かない・嗜好に迎合しない）②十分なサンプルの癖だけ durable
# 化③数値は教材の値を verbatim（ADR-014）④draft 起票のみで採用は人間（ADR-009）⑤既存プロファイル
# は重複回避の参照のみで自己強化しない（根拠は台帳事実）。
_PROFILER_INSTRUCTION = (
    "あなたは夜間の『投資家プロファイラ』だ。この投資家の取引台帳から Python が計算した行動の癖"
    "（下の教材）を読み、次の助言に効く**記述的な癖**だけを投資家プロファイルの傾向メモ下書きに"
    "蒸留せよ。目的は鏡・反追従＝本人のバイアスを打ち消す助言の土台を作ること。\n\n"
    "規律:\n"
    "1. これは『規範（どうすべきか）』でなく『記述（どういう人か）』だ。方針（policy）を書くな。"
    "嗜好に迎合するメモ（『◯◯が好きだから増やそう』）でなく、癖の記述（『◯◯に偏りがち』）を書け。\n"
    "2. 十分なサンプル（教材が『傾向』として提示したもの）に裏打ちされた癖だけをメモ化せよ。"
    "単発の売買から durable なメモを作るな（過学習を避ける）。どの信号（件数・率）を根拠にしたか"
    "を evidence に書け。\n"
    "3. 数値は教材（Python が計算済み）の値をそのまま引用し、自分で計算し直すな（ADR-014）。\n"
    "4. メモは下書き（draft）で起票され、採用は人間が /profile で最終承認する。確信が持てる癖だけ"
    "厳選し、乱発するな（本当に無ければ何も起票しなくてよい）。\n"
    "5. 下に『既存プロファイル』があれば、それと重複するメモは起票するな（重複回避の参照のみ・"
    "既存の記述を自己強化の材料にはせず、根拠は台帳事実だけにせよ）。\n"
    "より深い文脈が要れば get_track_record・search_judgments で掘ってよい。\n"
)


async def run_profile_distillation(conn: Connection) -> dict[str, object]:
    """台帳の行動信号を教材に投資家プロファイルの傾向メモ draft を蒸留する（ADR-082・ループ ④）。

    1. 活動量ゲート＝新規 SELL（カーソル超）が settings.profiler_min_new_sells 未満なら skip。
    2. profiler 面を解決（未設定は FaceNotConfiguredError→上位ジョブが沈黙 skip・ADR-018）。
    3. 教材構築（行動信号・min_samples 足切り）＋既存プロファイルを重複回避の参照として同梱。
    4. run_turn（source='profiler'・toolset=PROFILER_TOOLSET）で蒸留させる。
    5. persist_profile_notes_from_tool_runs で pending 起票（W2・承認は人間が /profile で）。
    6. 成功時のみカーソルを最新 SELL の traded_at まで前進（skip/失敗では据え置き）。

    戻り値: {"ran", "reason", "notes", "new_sells"}。ran=False はゲート skip（ジョブは ok=True）。
    LLM 失敗は run_turn からそのまま伝播（ジョブが ok=False）。conn は呼び出し側が begin() で渡す。
    """
    today = datetime.now(UTC).strftime("%Y-%m-%d")

    new_sells = investor_behavior.count_new_sells(conn)
    threshold = settings.profiler_min_new_sells
    if new_sells < threshold:
        reason = f"新規売り {new_sells} 件 < 閾値 {threshold}（蒸留 skip・カーソル据え置き）"
        logger.info("distill_investor_profile: %s", reason)
        return {"ran": False, "reason": reason, "notes": [], "new_sells": new_sells}

    # ゲート通過後に面を解決（新着ゼロの晩に面設定を要求しない＝reviewer 同型・ADR-018/058）。
    resolve_face("profiler")

    material = investor_behavior.build_behavior_material(
        conn, min_samples=settings.profiler_min_samples
    )
    material_text = investor_behavior.format_behavior_material_for_prompt(material)
    current = str(repo.get_investor_profile(conn).get("body") or "").strip()
    profile_ref = f"\n\n## 既存プロファイル（重複回避の参照のみ）\n{current}" if current else ""
    instruction = _PROFILER_INSTRUCTION + "\n" + material_text + profile_ref

    messages = build_messages(
        core_prompt=CORE,
        policy=repo.get_policy(conn),
        conversation=[Message(role="user", content=instruction)],
        screen_context=None,  # 蒸留に画面は無い（ADR-025）
        # ambient（市況/一般）カードのみ。プロファイルは build_messages の第 3 層でなく instruction
        # 内に重複回避の参照として載せる（profiler 固有の使い方＝自己強化しない）。
        knowledge_cards=await load_card_texts_for_injection(None),
        recent_journal=material.get("recent_journal"),
    )

    # profiler 面へ振り分け、最小 toolset だけ見せる（ADR-082）。LLM ハード失敗は握らず上位へ伝播。
    _reply, tool_runs = await run_turn(
        messages, phase=CURRENT_PHASE, source="profiler", tool_names=PROFILER_TOOLSET
    )

    # profile_note だけ永続する（多重防御＝allowlist_only で他 Tool は見えず、混ざっても拾わない）。
    notes = persist_profile_notes_from_tool_runs(conn, tool_runs=tool_runs, date=today)

    # 成功時のみカーソルを前進（同一 begin 内で起票と atomic・ADR-082）。
    investor_behavior.advance_cursor(conn)

    reason = f"新規売り {new_sells} 件をレビュー → 傾向メモ下書き {len(notes)} 件"
    logger.info("distill_investor_profile: %s", reason)
    return {"ran": True, "reason": reason, "notes": notes, "new_sells": new_sells}
