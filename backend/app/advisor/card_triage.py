"""知識カードの AI 審査トリアージ（ADR-062）。

設計の真実: docs/decisions.md ADR-062（知識カード基盤）・ADR-014/016（計算はコード・AI は解釈）。

UI で追加されたカード草案を LLM 単発で分類し、status の初期値を決める。これは「コード/カード/CORE/
LLM 一般知識」の振り分け規律（手法カード運用の弱点＝境界の曖昧さ）を自動で効かせる係でもある。

判定（status）:
- 'rejected'   … LLM が既に知っている一般教科書知識（例「PER 15 倍が目安」）。カード不要。
- 'to_core'    … 普遍的な規律・ペルソナ（例「単一指標で決めるな」「捏造するな」）。CORE 行きを示唆。
- 'needs_quant'… 解釈が未計算の指標値を要する。quant 実装待ち（quant_note に必要計算）。
- 'active'     … 既存データ/指標で成立する具体知識（市場文脈・外部メモ・既存 signal の読み方）。
                 既存 signal に紐づくなら linked_signal_type をその値にする。

接続規律: triage_card は DB に触れない純 LLM 関数（router/service が読み書きと status 反映を所有）。
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

_TRIAGE_INSTRUCTION = (
    "あなたは投資アドバイザー AI の知識ベースに追加される『知識カード』草案を審査する担当である。"
    "カードは『数値の読み方・解釈の作法・市場文脈』を持つ参照知識で、計算そのものは持たない"
    "（計算は必ずテスト済みコードにある）。渡された草案を次の 4 つのいずれか 1 つに分類する: \n"
    "- 'rejected': 強力な LLM が既に一般常識として知っている教科書的知識（例『PER は 15 倍が目安・"
    "低いほど割安』）。カードにする価値が薄い。\n"
    "- 'to_core': 特定の知識ではなく、普遍的な判断規律・姿勢（例『単一指標で結論するな』『数値を"
    "捏造するな』『断定せず根拠を添えろ』）。これは不変のシステム規律(CORE)に置くべきでカードにしない。\n"
    "- 'needs_quant': 解釈が、現状システムでまだ計算されていない指標値を前提にしている"
    "（その値が無いとカードを適用できない）。新しい計算コードの実装が要る。\n"
    "- 'active': 既存のデータ・指標で今すぐ成立する具体的な知識・解釈（市場固有の文脈、外部情報の"
    "要約、既に計算済みの指標やシグナルの読み方など）。\n"
    "判断材料として『既に計算済みのシグナル種別』のリストを渡す。カードがそのいずれかの読み方なら"
    "verdict を 'active' とし linked_signal_type にその種別名を入れる。未計算の新指標が要るなら"
    "'needs_quant' とし quant_note に『どんな計算が必要か』を一文で書く。\n"
    "数値スコアは付けない（定性判定のみ）。"
    "出力は次の JSON オブジェクトのみとし前後に地の文を付けない: "
    '{"verdict": "<active|needs_quant|to_core|rejected>", "reason": "<日本語の短い理由>", '
    '"quant_note": "<needs_quant のとき必要な計算・他は null>", '
    '"linked_signal_type": "<active で既存シグナルに紐づくとき種別名・他は null>"}'
)


@dataclass(frozen=True)
class TriageResult:
    """AI 審査の結果（status の初期値＋付随情報）。"""

    verdict: str  # _VALID_VERDICTS のいずれか
    reason: str
    quant_note: str | None
    linked_signal_type: str | None


@dataclass(frozen=True)
class AssistResult:
    """AI ドラフト補助の結果（本文から生成したフィールド＋審査・ADR-062 追補）。"""

    title: str
    when_to_apply: str | None
    level: str | None  # market/sector/stock/general のいずれか or None
    verdict: str
    reason: str
    quant_note: str | None
    linked_signal_type: str | None


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
    "- 'active': 既存データ/指標で成立する具体知識。"
    "既存シグナルの読み方なら linked_signal_type に種別名。\n"
    "判断材料として『既に計算済みのシグナル種別』のリストを渡す。数値スコアは付けない（定性のみ）。"
    "出力は次の JSON オブジェクトのみとし前後に地の文を付けない: "
    '{"title": "<見出し>", "when_to_apply": "<適用条件 or null>", '
    '"level": "<market|sector|stock|general|null>", '
    '"verdict": "<active|needs_quant|to_core|rejected>", "reason": "<短い理由>", '
    '"quant_note": "<needs_quant のとき・他は null>", '
    '"linked_signal_type": "<active で紐づくとき・他は null>"}'
)


def _str_or_none(value: object) -> str | None:
    """文字列を strip して返す（非文字列・空文字は None）。LLM 応答の正規化共通ヘルパ。"""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


async def triage_card(
    *,
    title: str,
    body: str,
    when_to_apply: str | None,
    existing_signal_types: list[str],
) -> TriageResult | None:
    """カード草案を LLM 単発で審査し TriageResult を返す（ADR-062・ADR-014）。

    LLM 単発 `engine.generate_once`（Tool 不要・source="triage" で独立面を解決・ADR-062）。
    面未設定や壊れた応答では None を返す（カードは draft のまま・人間判断に委ねる・ADR-018）。
    verdict が値域外でも None。
    """
    draft = {
        "title": title,
        "body": body,
        "when_to_apply": when_to_apply or "",
        "existing_signal_types": existing_signal_types,
    }
    messages: list[dict[str, object]] = [
        {"role": "system", "content": _TRIAGE_INSTRUCTION},
        {"role": "user", "content": json.dumps(draft, ensure_ascii=False)},
    ]

    # engine は import 鎖の先なので関数内で遅延 import（news_polarity / theme_tagger と同流儀）。
    from app.advisor.engine import generate_once

    try:
        content = await generate_once(messages, source="triage")
    except FaceNotConfiguredError:
        logger.info(
            "card_triage: triage 面が未設定のため審査を skip（カードは draft のまま・ADR-058/062）"
        )
        return None
    return _parse_triage_response(content)


def _parse_triage_response(content: str | None) -> TriageResult | None:
    """LLM 応答から TriageResult を取り出し正規化する（堅牢化・ADR-018）。

    JSON パース失敗・形不一致・verdict 値域外は None（draft のまま）。reason は文字列化、
    quant_note/linked_signal_type は空文字を None に畳む。
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
        logger.warning("card_triage: 応答が JSON でないため審査結果なし（draft のまま・ADR-018）")
        return None
    if not isinstance(parsed, dict):
        logger.warning("card_triage: 応答が JSON オブジェクトでないため審査結果なし")
        return None

    verdict = parsed.get("verdict")
    if not isinstance(verdict, str) or verdict.strip().lower() not in _VALID_VERDICTS:
        logger.warning("card_triage: verdict が値域外（%r）のため審査結果なし", verdict)
        return None
    verdict = verdict.strip().lower()
    reason = _str_or_none(parsed.get("reason")) or ""
    return TriageResult(
        verdict=verdict,
        reason=reason,
        quant_note=_str_or_none(parsed.get("quant_note")),
        linked_signal_type=_str_or_none(parsed.get("linked_signal_type")),
    )


async def assist_card(
    *,
    body: str,
    title: str = "",
    existing_signal_types: list[str],
) -> AssistResult | None:
    """本文から title/when_to_apply/level を生成し審査する（ADR-062 追補・ADR-014）。

    「本文だけ入力 → AI が整える」フロー用（保存前の下書き補助）。LLM 単発 `generate_once`
    （source="triage"）。面未設定や壊れた応答では None（呼び出し側はユーザー入力のまま保存に倒す）。
    verdict が値域外でも None。生成 title が空なら本文先頭で代替するのは呼び出し側の責務。
    """
    payload = {
        "title": title,
        "body": body,
        "existing_signal_types": existing_signal_types,
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
        linked_signal_type=_str_or_none(parsed.get("linked_signal_type")),
    )
