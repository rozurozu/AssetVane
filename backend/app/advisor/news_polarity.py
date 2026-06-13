"""ニュースの定性 polarity 判定（ADR-049/051・能動配信の前提）。

設計の真実: docs/decisions.md ADR-049（ニュース RAG の線引き＝定性タグのみ・数値スコアは
作らない）・ADR-051（能動配信）。

stock 層ニュースの要約から「好材料/悪材料/中立」の定性センチメントを LLM 単発で判定する。
notify_digest の②保有銘柄悪材料アラートが polarity='negative' を拾うための前処理。

- **定性タグのみ**: 出力は 'positive'/'negative'/'neutral' の 3 値 enum。数値 sentiment_score は
  作らない（AI に数値を作らせない＝ADR-014/049）。
- **複数記事を 1 コールでバッチ判定**: id を同一性キーに渡し、id→polarity の対応で受け取る
  （tag_news_polarity が embed_news 同型でバッチを回す）。
- **壊れた応答で落とさない**: JSON パース失敗・形不一致・enum 外・幻 id は捨て、その記事は
  polarity 未付与（NULL のまま翌晩再試行）に倒す（ADR-018 の思想＝不確かなら書かない）。

接続規律: classify_polarities は DB に触れない純 LLM 関数（ジョブ側が読み書きを所有）。
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

# polarity の値域（3 値 enum・NULL=未判定）。これ以外は破棄して NULL のまま（ADR-049）。
_VALID_POLARITIES = frozenset({"positive", "negative", "neutral"})

# Markdown コードフェンス剥がし（```json ... ``` で包んで返すモデルへの防御・theme_tagger と同形）。
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)

# 判定の指示（ADR-049 の規律を明文化）。定性 3 値のみ・数値を作らない・id は同一性キー・JSON のみ。
_POLARITY_INSTRUCTION = (
    "あなたはニュース記事の投資センチメントを分類する担当である。"
    "渡される各記事（id・title・summary）について、その銘柄にとっての材料が"
    "好材料か悪材料か中立かを判定する。"
    "判定は次の 3 値のいずれか 1 つだけを使う: "
    "'positive'（好材料）・'negative'（悪材料）・'neutral'（中立・どちらとも言えない）。"
    "数値スコアやパーセントは一切付けない（定性 3 値のみ）。"
    "id は記事の同一性を示す識別子なので、出力でそのまま対応付けること。"
    "判断に迷う・情報が薄い場合は 'neutral' とする。"
    "出力は次の JSON オブジェクトのみとし、前後に地の文を付けない: "
    '{"results": [{"id": <記事の id 整数>, "polarity": "<positive|negative|neutral>"}]}'
)


async def classify_polarities(articles: list[dict[str, object]]) -> dict[int, str]:
    """複数のニュース記事を LLM 単発で定性 polarity 判定する（ADR-049/051・ADR-014）。

    LLM 単発 `engine.generate_once`（Tool ループ不要・provider は source="tagger" で解決＝
    theme_tagger と同レーン・未知 source は安全側に openai へ落ちる・ADR-012）。各記事は id を
    同一性キーに（title/summary を根拠に）3 値 enum で分類する。応答は正規化（3 値外・幻 id を
    破棄）を通過した id→polarity の dict を返す。

    Args:
        articles: `[{"id": int, "title": ..., "summary": ...}]`（list_news_needing_polarity の行）。

    Returns:
        `{news_id: 'positive'|'negative'|'neutral'}`（壊れた/欠落した記事は含めない＝NULL のまま）。
    """
    if not articles:
        return {}
    payload = [
        {"id": int(a["id"]), "title": a.get("title") or "", "summary": a.get("summary") or ""}
        for a in articles
    ]
    messages: list[dict[str, object]] = [
        {"role": "system", "content": _POLARITY_INSTRUCTION},
        {"role": "user", "content": json.dumps({"articles": payload}, ensure_ascii=False)},
    ]

    # engine は import 鎖の先にあるため関数内で遅延 import して循環を断つ（theme_tagger と同流儀）。
    from app.advisor.engine import generate_once

    content = await generate_once(messages, source="tagger")
    valid_ids = {int(a["id"]) for a in articles}
    return _parse_polarity_response(content, valid_ids)


def _parse_polarity_response(content: str | None, valid_ids: set[int]) -> dict[int, str]:
    """LLM 応答から id→polarity を取り出し正規化する（堅牢化・ADR-018/049）。

    壊れた応答で銘柄処理を落とさない: JSON パース失敗・形不一致は**空 dict**（NULL のまま）。
    polarity は前後空白を除き小文字化してから 3 値 enum と照合し、値域外、または id が valid_ids に
    無い（LLM の幻 id）要素は破棄して log.warning する。同一 id が重複したら先勝ち。
    """
    if not content:
        return {}

    # コードフェンス（```json ... ```）で包まれた応答は中身だけ取り出してからパースする。
    fence_match = _FENCE_RE.match(content)
    if fence_match:
        content = fence_match.group(1)

    try:
        parsed = json.loads(content)
    except (TypeError, ValueError):
        logger.warning("news_polarity: 応答が JSON でないため polarity を付けない（ADR-018）")
        return {}

    if not isinstance(parsed, dict):
        logger.warning("news_polarity: 応答が JSON オブジェクトでないため polarity を付けない")
        return {}
    items = parsed.get("results")
    if not isinstance(items, list):
        logger.warning("news_polarity: results が配列でないため polarity を付けない")
        return {}

    result: dict[int, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_id = item.get("id")
        # id は整数前提だが、LLM が文字列で返すこともあるため int 化を試みる（bool は弾く）。
        if isinstance(raw_id, bool):
            continue
        try:
            news_id = int(raw_id)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if news_id not in valid_ids or news_id in result:
            continue
        polarity = item.get("polarity")
        if not isinstance(polarity, str):
            continue
        polarity = polarity.strip().lower()
        if polarity not in _VALID_POLARITIES:
            logger.warning(
                "news_polarity: 値域外の polarity %r を破棄（id=%s・ADR-049）",
                item.get("polarity"),
                news_id,
            )
            continue
        result[news_id] = polarity

    return result
