"""個別銘柄の定性調査パイプライン（Stock Dossier）。

設計の真実: docs/phase-specs/phase4-spec.md §3（パイプライン正本）・§4（返却スキーマ）／
ai-advisor.md §11／ADR-020・ADR-014・ADR-011・ADR-005。

ADR-020: 「1 つの調査パイプライン・2 つの起動口」。`investigate_stock(conn, code)` を
        夜間 watchlist 巡回とチャット Tool の 2 経路から共用する。取得手段は httpx 一本に
        統一したため（ADR-020 改訂）、昼夜で取得を分ける `mode` は廃止した（段取りは同一）。
ADR-014: AI に数値を計算させない。財務の数値は data レーンの事実（repo.get_financials）から取り、
        LLM は定性要約（物語）だけを担う。生データは丸投げせず、記事は「短い要約」を渡す。
ADR-020: ソースは「取得 → 要約 → 本文を捨てる」。統合コーパス news（ADR-044）には summary と
        url のみ残す（本文は保存しない）。
ADR-044: 旧 dossier_sources は統合コーパス news に集約済み。銘柄ニュースは level="stock"＋code で
        記録する（source は旧 source_type を移し替えた値・fetched_at は取り込み時刻を補填）。
ADR-050: テーマタグ段階B。ドシエ要約（summary_md）を company_descriptions(JP, source='dossier') に
        同一 conn で焼き、夜間 tag_jp_themes の grounded タグ付け信号源にする（段取り 7.5）。
ADR-076: 調査の開始/終了を `dossier_progress` レジストリ（プロセスメモリ）に mark/unmark し、
        `GET /dossiers/{code}` の investigating に露出する。リロードしても「調査中」を保つ。

トランザクション境界（ADR-005・W2）: `investigate_stock` は spec §3 の正本シグネチャどおり
`conn` を受け取り、自分では commit しない。複数ソース＋ドシエ本体を 1 トランザクションに
atomic に束ねる境界は **呼び出し側**（REST ルータ／夜間巡回ジョブ）が
`with get_engine().begin() as conn:` で所有する（repo の W2 規約と同じ流儀＝書き手は FastAPI
1 プロセス）。

書きロック保持の最小化（tasks/review-2026-06-12.md C-6）: pysqlite は**最初の DML まで
BEGIN（SQLite の書きロック取得）を遅延**するため、ニュース取得・LLM 要約（数十秒かかる
await）を**全部終えてから**書き込みだけを末尾に束ねる段取りに再構成した。これで呼び出し側
begin() 配下でも書きロックの保持は upsert 群の一瞬だけになり、昼チャット経由の調査中に他の
書き込み（取引登録等）が busy_timeout=5000ms を超えて `database is locked` になる事態を防ぐ。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Connection

from app.adapters.news import fetch_news
from app.advisor import dossier_progress
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
) -> dict[str, Any]:
    """個別銘柄を調査しドシエを生成・更新する（spec §3 が正本・ADR-020/014/011）。

    段取り（取得 → 要約 → 保存）は夜間巡回・チャットで共用。取得手段は httpx 一本に統一した
    ため `mode` は廃止した（ADR-020 改訂）。`conn` は呼び出し側が begin() で束ねる
    （W2・本モジュール docstring）。

    社名解決の責務（W-pipeline / adapter 契約）: NewsAdapter は DB に触らないため、
    `fetch_news` に渡す社名はこの呼び出し側で `repo.get_stock` から解決する（解決できない
    場合は code をそのまま社名として渡す＝検索が空振りしても落とさない）。

    トランザクション内の段取り（tasks/review-2026-06-12.md C-6）: ニュース取得・LLM 要約
    （await）を**全部終えてから**書き込み（最初の DML）を末尾に束ねる。pysqlite は最初の DML
    まで BEGIN（SQLite の書きロック取得）を遅延するため、これで書きロックの保持が upsert 群の
    一瞬だけになり、LLM の数十秒間に他の書き込みを `database is locked` で阻害しない。
    意味の変化（意図したもの）: 従来は LLM 要約**前**に news を upsert していたが、再構成後は
    LLM 失敗（例外・タイムアウト）時に DML 自体が発行されず、news もドシエも DB には何も
    書かれない（原子的）。

    Args:
        conn: 書き込み接続（呼び出し側が `with get_engine().begin()` で所有・commit しない）。
        code: 調査対象の銘柄コード。

    Returns:
        `{code, summary_md, key_facts, last_investigated_at, n_sources_added}`（spec §4 正本）。
        key_facts は JSON 文字列（dossier_sources/stock_dossiers に保存した生の形）。
    """
    # 進行状態レジストリに「調査中」を刻む（ADR-076）。手動 POST・夜間巡回・チャット Tool の
    # 共通パイプラインなので、ここ 1 箇所の mark/unmark でどの起動口の調査も
    # GET /dossiers/{code} の investigating に映る。in-memory なので `conn` の
    # トランザクション設計（末尾に書き込みを束ねる）には干渉しない。finally で必ず落とす
    # ＝クライアント切断で CancelledError が飛んでも整合する。
    dossier_progress.mark(code)
    try:
        # ── 前段: 読み取り＋await のみ（DML を発行しない＝書きロックを取らない・C-6） ──

        # 1. 財務の事実（data レーン・ADR-014）。AI には計算させず、この事実を要約に渡す。
        financials = repo.get_financials(conn, code)

        # 2. ニュース取得（発行 1 週間以内・httpx 一本＝ADR-020 改訂）。社名は呼び出し側で解決する
        #    （adapter は DB に触らない契約）。社名が取れなければ code を社名代わりに使う。
        stock = repo.get_stock(conn, code)
        company_name = (stock or {}).get("company_name") or code
        articles = await fetch_news(code, company_name, since=_since_today_minus(_LOOKBACK_DAYS))

        # 3. URL 重複排除（既存 url の記事は二重に取り込まない・spec §3）。
        #    統合コーパス news の存在確認（ADR-044・旧 dossier_source_exists を置換）。
        new_articles = [a for a in articles if not repo.news_exists(conn, a["url"])]

        # 4. 既存ドシエ（living document）を読む。
        existing = repo.get_dossier(conn, code)

        # 5. LLM 単発で要約更新（Tool ループ不要・記事は「短い要約」のみ渡す・ADR-014/020）。
        #    ここまで DML を 1 つも発行していないので、LLM が失敗してもまだ DB には何も書かれて
        #    いない（原子的・tasks/review-2026-06-12.md C-6）。
        summary_md, key_facts = await summarize_dossier(existing, financials, new_articles)

        # ── 後段: 書き込みだけを束ねる（最初の DML はここ＝書きロック保持は upsert 群の間のみ） ──

        # 6. 新着のみ統合コーパス news に記録（本文は保存せず summary/url のみ・ADR-020/044）。
        #    level="stock"＋code でタグ付けし、旧 source_type は news.source へ移す
        #    （fetched_at は upsert_news が UTC now を補填する）。
        news_rows = [
            {
                "level": "stock",
                "code": code,
                "sector17_code": None,
                "category": None,
                "source": a.get("source_type"),
                "url": a["url"],
                "title": a.get("title"),
                "summary": a.get("summary"),
                "published_at": a.get("published_at"),
                "extraction_status": a.get("extraction_status"),
            }
            for a in new_articles
        ]
        repo.upsert_news(conn, news_rows)

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

        # 7.5 ドシエ要約を company_descriptions(JP, source='dossier') に焼く
        # （テーマタグ段階B の信号源・ADR-050 改訂）。同一 conn（W2）でドシエ本体と atomic に書く。
        # description_text が変化したときだけ fetched_at が動き、夜間 tag_jp_themes が
        # 「説明変化した銘柄」として grounded タグ付け。JP の同一性は code。基準日/書類番号は
        # EDINET 専用なのでドシエ由来は None。
        repo.upsert_company_description_tx(
            conn,
            market="JP",
            code=code,
            source="dossier",
            description_text=summary_md,
            disclosed_date=None,
            doc_id=None,
            fetched_at=now,
        )

        # 8. 返却（spec §4 の investigate_stock スキーマ正本）。
        return {
            "code": code,
            "summary_md": summary_md,
            "key_facts": key_facts,
            "last_investigated_at": now,
            "n_sources_added": len(new_articles),
        }
    finally:
        # 調査中フラグを必ず落とす（成功・例外・切断キャンセルのいずれでも・ADR-076）。
        dossier_progress.unmark(code)


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

    # provider（OpenAI 互換）は engine が source="dossier" から解決する（ADR-058）。
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
