"""知識カードの AI 審査トリアージ（ADR-062）。

設計の真実: docs/decisions.md ADR-062（知識カード基盤）・ADR-014/016（計算はコード・AI は解釈）。

UI で追加されたカード草案を LLM 単発で分類し、status の初期値を決める。これは「コード/カード/CORE/
LLM 一般知識」の振り分け規律（手法カード運用の弱点＝境界の曖昧さ）を自動で効かせる係でもある。

判定（status）:
- 'rejected'   … LLM が既に知っている一般教科書知識（例「PER 15 倍が目安」）。カード不要。
- 'to_core'    … 普遍的な規律・ペルソナ（例「単一指標で決めるな」「捏造するな」）。CORE 行きを示唆。
- 'needs_quant'… 解釈が未計算の指標値を要する。quant 実装待ち（quant_note に必要計算）。
- 'active'     … 既存データ/指標で成立する具体知識（市場文脈・外部メモ・既存 signal の読み方）。
                 （手法の解釈は knowledge_cards でなく method_cards が持つ＝ADR-075）。

接続規律: assist_card は DB に触れない純 LLM 関数（router/service が読み書きと status 反映を所有）。
壊れた応答・面未設定では None を返し、カードは draft のまま（人間が手動で判断できる・ADR-018）。
active 化（本番助言に効く）は AI ではなく人間が最終承認する（ADR-009）。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from app.services.llm_config import FaceNotConfiguredError

logger = logging.getLogger(__name__)

# 取りうる verdict（status の初期値）。これ以外は破棄して None（draft のまま）。
_VALID_VERDICTS = frozenset({"active", "needs_quant", "to_core", "rejected"})

# Markdown コードフェンス剥がし（```json ... ``` 防御・news_polarity / theme_tagger と同形）。
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


@dataclass(frozen=True)
class AssistResult:
    """AI ドラフト補助の結果（本文から生成したフィールド＋審査・ADR-062 追補）。"""

    title: str
    when_to_apply: str | None
    level: str | None  # market/sector/stock/general のいずれか or None
    verdict: str
    reason: str
    quant_note: str | None


# level の値域（AI 生成の正規化用）。値域外は None に倒す。
_VALID_LEVELS = frozenset({"market", "sector", "stock", "general"})

_ASSIST_INSTRUCTION = (
    "あなたは投資アドバイザー AI の知識ベースに追加される『知識カード』を整える担当である。"
    "ユーザーは本文（知識の中身）だけ、または不完全な情報を渡す。"
    "あなたの仕事は次を生成し審査する: \n"
    "- title: 本文を一言で表す簡潔な見出し（与えられていれば改善・なければ生成）。\n"
    "- when_to_apply: この知識が効く状況を一文で（意味検索のキーになる）。\n"
    "- level: 'market'（市況/マクロ全般）/'sector'（特定セクター）/'stock'（特定銘柄）/'general'"
    "（一般原則）から最も近いものを 1 つ。判断できなければ null。\n"
    "そのうえで、カードとして妥当かを次の 4 値で審査する（triage と同基準）: \n"
    "- 'rejected': 強力な LLM が既に知る一般教科書知識（例『PER は 15 倍が目安』）。\n"
    "- 'to_core': 普遍的な判断規律（例『単一指標で結論するな』）。CORE に置くべき。\n"
    "- 'needs_quant': 未計算の指標値を要する（quant 実装待ち）。quant_note に必要計算。\n"
    "- 'active': 既存データ/指標で成立する具体知識。\n"
    "数値スコアは付けない（定性のみ）。"
    "出力は次の JSON オブジェクトのみとし前後に地の文を付けない: "
    '{"title": "<見出し>", "when_to_apply": "<適用条件 or null>", '
    '"level": "<market|sector|stock|general|null>", '
    '"verdict": "<active|needs_quant|to_core|rejected>", "reason": "<短い理由>", '
    '"quant_note": "<needs_quant のとき・他は null>"}'
)


def _str_or_none(value: object) -> str | None:
    """文字列を strip して返す（非文字列・空文字は None）。LLM 応答の正規化共通ヘルパ。"""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


async def assist_card(
    *,
    body: str,
    title: str = "",
) -> AssistResult | None:
    """本文から title/when_to_apply/level を生成し審査する（ADR-062 追補・ADR-014）。

    「本文だけ入力 → AI が整える」フロー用（保存前の下書き補助）。LLM 単発 `generate_once`
    （source="triage"）。面未設定や壊れた応答では None（呼び出し側はユーザー入力のまま保存に倒す）。
    verdict が値域外でも None。生成 title が空なら本文先頭で代替するのは呼び出し側の責務。

    手法↔signal の索引は method_cards（advisor/method_cards/*.md）が持つため triage は生成しない
    （ADR-075・旧 linked_signal_type は 0035 で DROP）。
    """
    payload = {
        "title": title,
        "body": body,
    }
    messages: list[dict[str, object]] = [
        {"role": "system", "content": _ASSIST_INSTRUCTION},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]

    from app.advisor.engine import generate_once

    try:
        content = await generate_once(messages, source="triage")
    except FaceNotConfiguredError:
        logger.info("card_triage: triage 面が未設定のため assist を skip（ADR-058/062）")
        return None
    return _parse_assist_response(content)


def _parse_assist_response(content: str | None) -> AssistResult | None:
    """LLM 応答から AssistResult を取り出し正規化する（堅牢化・ADR-018）。

    JSON パース失敗・形不一致・verdict 値域外は None。level は値域外を None に倒す。
    title は空でも返す（呼び出し側が本文先頭で代替する）。
    """
    if not content:
        return None
    body_text = content.strip()
    fence_match = _FENCE_RE.match(body_text)
    if fence_match:
        body_text = fence_match.group(1)
    try:
        parsed = json.loads(body_text)
    except (TypeError, ValueError):
        logger.warning("card_triage: assist 応答が JSON でないため結果なし（ADR-018）")
        return None
    if not isinstance(parsed, dict):
        return None

    verdict = parsed.get("verdict")
    if not isinstance(verdict, str) or verdict.strip().lower() not in _VALID_VERDICTS:
        logger.warning("card_triage: assist の verdict が値域外（%r）", verdict)
        return None

    level = _str_or_none(parsed.get("level"))
    if level is not None and level.lower() not in _VALID_LEVELS:
        level = None

    return AssistResult(
        title=_str_or_none(parsed.get("title")) or "",
        when_to_apply=_str_or_none(parsed.get("when_to_apply")),
        level=level.lower() if level else None,
        verdict=verdict.strip().lower(),
        reason=_str_or_none(parsed.get("reason")) or "",
        quant_note=_str_or_none(parsed.get("quant_note")),
    )
