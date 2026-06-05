"""個別銘柄の定性調査パイプライン（Stock Dossier）。

設計の真実: docs/phase-specs/phase4-spec.md §3（パイプライン正本）・§4（返却スキーマ）／
ai-advisor.md §11／ADR-020・ADR-014・ADR-011・ADR-005。

ADR-020: 「1 つの調査パイプライン・2 つの起動口」。`investigate_stock(conn, code, mode)` を
        夜間 watchlist 巡回（mode="nightly"・軽め）とチャット Tool（mode="chat"・リッチ）の
        2 経路から共用する。違いは fetch_news の取得手段と要約の濃さだけ（段取りは同一）。
ADR-014: AI に数値を計算させない。財務の数値は data レーンの事実（repo.get_financials）から取り、
        LLM は定性要約（物語）だけを担う。生データは丸投げせず、記事は「短い要約」を渡す。
ADR-020: ソースは「取得 → 要約 → 本文を捨てる」。dossier_sources には summary と url のみ残す。

トランザクション境界（ADR-005・W2）: `investigate_stock` は spec §3 の正本シグネチャどおり
`conn` を受け取り、自分では commit しない。複数ソース＋ドシエ本体を 1 トランザクションに
atomic に束ねる境界は **呼び出し側**（REST ルータ／夜間巡回ジョブ）が
`with get_engine().begin() as conn:` で所有する（repo の W2 規約と同じ流儀＝書き手は FastAPI
1 プロセス）。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import Connection

from app.adapters.news import fetch_news
from app.db import repo

logger = logging.getLogger(__name__)

# 発行 1 週間以内の新着のみ取り込む（spec §3・完了条件）。
_LOOKBACK_DAYS = 7

# 定性要約の指示（CORE の規律を継ぐ・ADR-014/020）。記事全文ではなく要約と財務「事実」のみを渡す。
_SUMMARIZE_INSTRUCTION = (
    "あなたは個別銘柄の定性的な調査レポート（ドシエ）を書く担当である。"
    "渡された『既存ドシエ』『財務の事実（data レーンが計算した数値）』『新着ニュースの要約』だけを"
    "根拠に、既存ドシエを incremental に更新した最新版を作れ（living document として積み上げる）。"
    "数値は必ず渡された財務の事実のみを使い、自分で計算・推測しない（ADR-014）。"
    "新しい事実が無ければ既存ドシエを維持してよい。"
    "出力は必ず次の JSON オブジェクトのみ（前後に地の文を付けない）: "
    '{"summary_md": "<markdown のレポート本文>", '
    '"key_facts": {"<キー>": <値>, ...}}。'
    "key_facts には PER・成長率・直近トピック等の構造化された要点を入れる（出所は財務の事実）。"
)


def _now_iso() -> str:
    """現在時刻を ISO8601（UTC）で返す（既存コードの作法に合わせる・nightly.py 踏襲）。"""
    return datetime.now(UTC).isoformat()


def _since_today_minus(days: int) -> str:
    """今日から days 日前の 'YYYY-MM-DD' を返す（fetch_news の取得下限・spec §3）。"""
    return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")


async def investigate_stock(
    conn: Connection,
    code: str,
    *,
    mode: Literal["nightly", "chat"],
) -> dict[str, Any]:
    """個別銘柄を調査しドシエを生成・更新する（spec §3 が正本・ADR-020/014/011）。

    段取り（取得 → 要約 → 保存）は夜間巡回・チャットで共用。`mode` が fetch_news の取得手段と
    要約の濃さだけを分ける。`conn` は呼び出し側が begin() で束ねる（W2・本モジュール docstring）。

    Args:
        conn: 書き込み接続（呼び出し側が `with get_engine().begin()` で所有・commit しない）。
        code: 調査対象の銘柄コード。
        mode: "nightly"=夜 MCP 非依存・軽め／"chat"=昼 MCP リッチ（spec §3）。

    Returns:
        `{code, summary_md, key_facts, last_investigated_at, n_sources_added}`（spec §4 正本）。
        key_facts は JSON 文字列（dossier_sources/stock_dossiers に保存した生の形）。
    """
    # 1. 財務の事実（data レーン・ADR-014）。AI には計算させず、この事実を要約に渡す。
    financials = repo.get_financials(conn, code)

    # 2. ニュース取得（発行 1 週間以内・mode で取得手段を切替・spec §3）。
    articles = await fetch_news(code, since=_since_today_minus(_LOOKBACK_DAYS), mode=mode)

    # 3. URL 重複排除（既存 url の記事は二重に取り込まない・spec §3）。
    new_articles = [a for a in articles if not repo.dossier_source_exists(conn, a["url"])]

    # 4. 新着のみ台帳に記録（本文は保存しない＝summary と url のみ・ADR-020）。
    processed_at = _now_iso()
    for a in new_articles:
        repo.upsert_dossier_source(
            conn,
            code=code,
            url=a["url"],
            title=a.get("title"),
            summary=a.get("summary"),
            published_at=a.get("published_at"),
            source_type=a.get("source_type"),
            processed_at=processed_at,
        )

    # 5. 既存ドシエ（living document）を読む。
    existing = repo.get_dossier(conn, code)

    # 6. LLM 単発で要約更新（Tool ループ不要・記事は「短い要約」のみ渡す・ADR-014/020）。
    summary_md, key_facts = await summarize_dossier(existing, financials, new_articles)

    # 7. ドシエ本体を UPSERT（last_investigated_at を前進・stale 起点）。
    now = _now_iso()
    repo.upsert_dossier(
        conn,
        code=code,
        summary_md=summary_md,
        key_facts=key_facts,
        last_investigated_at=now,
        updated_at=now,
    )

    # 8. 返却（spec §4 の investigate_stock スキーマ正本）。
    return {
        "code": code,
        "summary_md": summary_md,
        "key_facts": key_facts,
        "last_investigated_at": now,
        "n_sources_added": len(new_articles),
    }


async def summarize_dossier(
    existing: dict[str, Any] | None,
    financials: list[dict[str, Any]],
    new_articles: list[dict[str, Any]],
) -> tuple[str, str]:
    """既存ドシエを記事要約と財務事実で incremental に更新する（spec §3・ADR-014/020）。

    LLM 単発 `engine.generate_once`（Tool ループ不要・provider は source="dossier" で解決）。
    **記事全文は渡さず**、ソースの短い要約
    （title/summary/published_at/source_type）と data レーンの財務事実だけを渡す（生データ
    丸投げ禁止＝ADR-014）。数値は財務の事実に紐づける。

    Args:
        existing: 既存ドシエ行（`get_dossier` 戻り・無ければ None）。summary_md を living
            document の土台に使う。
        financials: data レーンの財務事実（`repo.get_financials` の dict 列）。
        new_articles: 新着ソースの**要約**（url/title/summary/published_at/source_type）。

    Returns:
        `(summary_md, key_facts)`。summary_md は markdown 本文、key_facts は JSON 文字列。
        LLM 応答が JSON として壊れている場合は既存値（無ければ空）を維持して落ちない。
    """
    existing_summary = (existing or {}).get("summary_md") or ""
    existing_key_facts = (existing or {}).get("key_facts") or "{}"

    # LLM へ渡す事実（記事は要約のみ・全文は載せない＝ADR-014/020）。
    article_digests = [
        {
            "title": a.get("title"),
            "summary": a.get("summary"),
            "published_at": a.get("published_at"),
            "source_type": a.get("source_type"),
        }
        for a in new_articles
    ]
    payload = {
        "existing_summary_md": existing_summary,
        "financials": financials,  # data レーンの事実（数値・ADR-014）
        "new_articles": article_digests,  # 短い要約のみ
    }

    messages: list[dict[str, object]] = [
        {"role": "system", "content": _SUMMARIZE_INSTRUCTION},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]

    # provider（openai/codex）は engine が source="dossier" から解決する（plans・ADR-012）。
    # engine は service→registry→handlers→dossier の import 鎖の先にあるため、ここは関数内で
    # 遅延 import して循環 import を断つ（dossier は Tool registry に取り込まれる低レイヤ）。
    from app.advisor.engine import generate_once

    content = await generate_once(messages, source="dossier")
    return _parse_summary_response(content, existing_summary, existing_key_facts)


def _parse_summary_response(
    content: str | None,
    existing_summary: str,
    existing_key_facts: str,
) -> tuple[str, str]:
    """LLM 応答（JSON 文字列想定）から (summary_md, key_facts) を取り出す（堅牢化・ADR-018）。

    壊れた応答で調査を落とさない: パース不能なら既存値を維持する。key_facts は JSON 文字列に
    正規化して返す（repo は文字列を期待・spec §2.2）。
    """
    if not content:
        return existing_summary, existing_key_facts

    try:
        parsed = json.loads(content)
    except (TypeError, ValueError):
        # JSON でなければ本文をそのまま summary とみなし、key_facts は既存維持。
        logger.warning("summarize_dossier: 応答が JSON でない。本文を summary として採用する。")
        return content, existing_key_facts

    if not isinstance(parsed, dict):
        return content, existing_key_facts

    summary_md = parsed.get("summary_md")
    summary_md = summary_md if isinstance(summary_md, str) and summary_md else existing_summary

    key_facts = parsed.get("key_facts")
    if isinstance(key_facts, (dict, list)):
        key_facts_str = json.dumps(key_facts, ensure_ascii=False)
    elif isinstance(key_facts, str) and key_facts:
        key_facts_str = key_facts  # 既に文字列ならそのまま
    else:
        key_facts_str = existing_key_facts

    return summary_md, key_facts_str
