"""提案前 red-team 反証（skeptic 面）のオーケストレーション（ADR-086・提案の自己反証）。

設計の真実: docs/decisions.md ADR-086。

夜バッチの red_team_proposals ジョブが駆動する。当夜 pending の buy/sell 提案（run_advisor が生成／
昼 chat が起票）を、生成面（nightly/chat）とは**別の skeptic 面**で反証し、結果を提案の
`body.skeptic` に注記する（自動却下はしない＝承認判断は人間・ADR-009）。reviewer/profiler と同型:
gate → resolve_face → build_messages → run_turn（toolset 制限）→ persist（決定論で body に焼く）。
差分は 3 点:
1. **教材**＝当夜 pending 未反証の buy/sell 提案そのもの（採点済み outcome でなく）。
2. **ゲート**＝未反証の pending 提案が 0 件なら skip（カーソル不要＝body.skeptic の有無で有界化）。
3. **永続**＝新規 draft の insert でなく既存 proposal 行の UPDATE（attach_skeptic_review）。

障害設計（ADR-018）: skeptic 面未設定は resolve_face が FaceNotConfiguredError を投げ、呼び出し側
（red_team_proposals ジョブ）が沈黙 skip（ok=True・reviewer 同型）にする。LLM ハード失敗は run_turn
から伝播し、ジョブが ok=False で runner 集約通知に乗せる。conn は呼び出し側が begin() で渡す。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection

from app.advisor.core_prompt import CORE
from app.advisor.engine import resolve_face, run_turn
from app.advisor.journaling import persist_skeptic_reviews_from_tool_runs
from app.advisor.prompt_builder import Message, build_messages
from app.advisor.tools.registry import CURRENT_PHASE, SKEPTIC_TOOLSET
from app.db import repo
from app.services.knowledge_cards import load_card_texts_for_injection

logger = logging.getLogger(__name__)

# skeptic の定型指示文（ADR-086）。教材（当夜 pending の buy/sell 提案）は Python が verbatim で
# 末尾に注入する。核＝①各提案の最強の反対筋・弱点・前提崩れを挙げる ②事実で反証する（数値は Tool
# の戻り値だけ・ADR-014）③ai_alpha が提案の向きと食い違えばそのズレを論点化する（Part a と接続）
# ④却下はしない＝注記だけ・乱発せず効く指摘だけ（ADR-009）。
_SKEPTIC_INSTRUCTION = (
    "あなたは夜間の『独立した反証官（red-team）』だ。以下は今夜 AI（別の面）が起票した"
    "買い/売り提案（承認待ち）だ。各提案の論拠を鵜呑みにせず、最も強い反対筋・弱点・前提崩れを"
    "洗い出し、submit_refutation で 1 件ずつ反証を注記せよ。目的は承認者（人間）が承認/却下を"
    "判断する材料を増やすこと＝**却下はしない**（注記だけ・status は変えない）。\n\n"
    "作法:\n"
    "1. 各提案に最も効く反対材料を挙げよ＝この買い/売りが外れる最有力シナリオ・論拠の穴・"
    "見落とした逆風。verdict は holds（筋が通る）/weak（論拠が弱い）/fragile（前提が脆い）。\n"
    "2. 事実で反証せよ。get_valuation / get_signals / get_news_context / get_track_record 等で裏を"
    "取り、数値は Tool の戻り値だけを使う（自分で計算しない＝ADR-014）。\n"
    "3. AI決算スコア（get_signals の ai_alpha）が提案の向きと食い違うなら、そのズレを反証に書け"
    "（モデルは決算面を見る・提案は別の材料＝どちらが今効くか）。\n"
    "4. 反証材料が薄い提案は無理に fragile にせず holds でよい（乱発せず効く指摘だけ注記する）。\n"
)


def _format_proposals_for_prompt(proposals: list[dict[str, Any]]) -> str:
    """当夜 pending の buy/sell 提案を反証プロンプト用の verbatim テキストに整形する（ADR-086）。"""
    lines = ["## 反証対象の提案（当夜 pending の買い/売り）"]
    for p in proposals:
        head = f"- id={p['id']} / {p['action']} / {p.get('code') or '?'}"
        name = p.get("company_name")
        if name:
            head += f"（{name}）"
        lines.append(head)
        if p.get("reason"):
            lines.append(f"  根拠: {p['reason']}")
        extras: list[str] = []
        if p.get("conviction"):
            extras.append(f"確信度={p['conviction']}")
        if p.get("catalyst"):
            extras.append(f"catalyst={p['catalyst']}")
        if p.get("invalidation"):
            extras.append(f"invalidation={p['invalidation']}")
        if extras:
            lines.append("  " + " / ".join(extras))
    return "\n".join(lines)


async def run_skeptic_review(conn: Connection) -> dict[str, object]:
    """当夜 pending の buy/sell 提案を独立面で反証し body.skeptic に注記する（ADR-086）。

    1. ゲート＝未反証（body.skeptic 無し）の pending buy/sell が 0 件なら skip（カーソル不要）。
    2. skeptic 面を解決（未設定は FaceNotConfiguredError→上位ジョブが沈黙 skip・ADR-018）。
    3. 提案を verbatim 素材注入して run_turn（source='skeptic'・toolset=SKEPTIC_TOOLSET）。
    4. persist_skeptic_reviews（allowed_ids で束縛＝素材外/幻覚 id を drop）で body に反証を焼く。

    戻り値: {"ran", "reason", "reviewed", "n_pending"}。ran=False はゲート skip（ジョブ ok=True）。
    LLM 失敗は run_turn から伝播（ジョブが ok=False）。conn は呼び出し側が begin() で渡す。
    """
    today = datetime.now(UTC).strftime("%Y-%m-%d")

    pending = repo.list_pending_unreviewed_trade_proposals(conn)
    if not pending:
        reason = "反証対象の pending 提案が無い（skip）"
        logger.info("red_team_proposals: %s", reason)
        return {"ran": False, "reason": reason, "reviewed": [], "n_pending": 0}

    # ゲート通過後に面を解決（提案ゼロの晩に skeptic 面設定を要求しない・reviewer 同型）。
    resolve_face("skeptic")

    allowed_ids = {int(p["id"]) for p in pending}
    instruction = _SKEPTIC_INSTRUCTION + "\n" + _format_proposals_for_prompt(pending)

    messages = build_messages(
        core_prompt=CORE,
        policy=repo.get_policy(conn),
        conversation=[Message(role="user", content=instruction)],
        screen_context=None,  # 反証に画面は無い（ADR-025）
        knowledge_cards=await load_card_texts_for_injection(None),
    )

    # skeptic 面へ振り分け、最小 toolset だけ見せる（ADR-086）。LLM ハード失敗は握らず上位へ伝播。
    _reply, tool_runs = await run_turn(
        messages, phase=CURRENT_PHASE, source="skeptic", tool_names=SKEPTIC_TOOLSET
    )

    # submit_refutation だけ拾い、当夜対象（allowed_ids）に束縛して body に焼く（多重防御）。
    reviewed = persist_skeptic_reviews_from_tool_runs(
        conn, tool_runs=tool_runs, date=today, allowed_ids=allowed_ids
    )

    reason = f"pending {len(pending)} 件を反証 → 注記 {len(reviewed)} 件"
    logger.info("red_team_proposals: %s", reason)
    return {"ran": True, "reason": reason, "reviewed": reviewed, "n_pending": len(pending)}
