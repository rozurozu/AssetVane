"""Dossier・ニュースコーパス・embedding・polarity・watchlist（Phase 4・ADR-044/045/049/051）。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, and_, select, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.engine import get_engine
from app.db.schema import (
    news,
    stock_dossiers,
    stocks,
    watchlist,
)

# ===== Phase 4: Stock Dossier（phase4-spec.md §2/§3・ADR-020） =====

# 書き込み規約: upsert_dossier / upsert_dossier_source は investigate_stock パイプラインが
# 複数ソース＋ドシエ本体を 1 トランザクションに束ねて atomic に書くため、conn を受け取り
# commit はしない（W2・呼び出し側が `with get_engine().begin() as conn:` で境界を所有）。
# 一方 watchlist の add/remove は API からの単発書き込みなので repo が自前で begin する（W1）。


def upsert_dossier(
    conn: Connection,
    *,
    code: str,
    summary_md: str | None,
    key_facts: str | None,
    last_investigated_at: str | None,
    updated_at: str | None,
) -> None:
    """stock_dossiers を 1 銘柄 1 行で UPSERT する（code 衝突で更新・ADR-020/ADR-002・spec §2.2）。

    living document なので code conflict は do_update（summary_md 等を上書きしていく）。
    key_facts は呼び出し側で json.dumps 済みの文字列を渡す（JSON 化/パースは router/service）。
    commit はしない。呼び出し側が `with get_engine().begin() as conn:` で境界を所有する（W2）。
    """
    stmt = sqlite_insert(stock_dossiers).values(
        code=code,
        summary_md=summary_md,
        key_facts=key_facts,
        last_investigated_at=last_investigated_at,
        updated_at=updated_at,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["code"],
        set_={
            "summary_md": stmt.excluded.summary_md,
            "key_facts": stmt.excluded.key_facts,
            "last_investigated_at": stmt.excluded.last_investigated_at,
            "updated_at": stmt.excluded.updated_at,
        },
    )
    conn.execute(stmt)


# ===== ADR-044: ニュース統合コーパス（旧 general_news ＋ dossier_sources を統合した 1 本） =====

# 旧 5 関数（upsert_general_news/list_general_news/upsert_dossier_source/dossier_source_exists/
# list_dossier_sources）を以下 3 関数に統合した（ADR-044）。記事ごとに level（stock/sector/market/
# user）・code・sector17_code・category・source の階層タグを持たせ、3 層（銘柄/セクター/市況）を
# タグフィルタで取り出す（get_news_context）。本文は持たず summary と url のみ（ADR-020 堅持）。
# 書き込み規約: upsert_news は夜間ジョブ等が複数記事を 1 トランザクションに束ねて atomic に書く。
# conn を受け取り commit はしない（W2・呼び出し側が `with get_engine().begin() as conn:` で所有）。


def upsert_news(conn: Connection, rows: list[dict[str, Any]]) -> int:
    """ニュース記事を一括 UPSERT する（url 衝突なら skip・ADR-044/ADR-002）。

    各 row のキー = level/code/sector17_code/category/source/url/title/summary/published_at/
    fetched_at/extraction_status。本文は保存せず summary と url のみ（ADR-020 の流儀）。既定は
    「既存 url なら skip」＝on_conflict_do_nothing（再取得の二重取り込みを防ぐ冪等キー）。
    fetched_at 未指定行は UTC now を補う。返り値は受理を試みた行数（skip 含む・ログ用）。
    commit はしない。呼び出し側が `with get_engine().begin() as conn:` で境界を所有する（W2）。
    """
    if not rows:
        return 0
    now_iso = datetime.now(UTC).isoformat()
    for row in rows:
        stmt = sqlite_insert(news).values(
            level=row["level"],
            code=row.get("code"),
            sector17_code=row.get("sector17_code"),
            category=row.get("category"),
            source=row.get("source"),
            url=row["url"],
            title=row.get("title"),
            summary=row.get("summary"),
            published_at=row.get("published_at"),
            fetched_at=row.get("fetched_at") or now_iso,
            extraction_status=row.get("extraction_status"),
        )
        stmt = stmt.on_conflict_do_nothing(index_elements=["url"])  # 既存 url は無視（skip）
        conn.execute(stmt)
    return len(rows)


def news_exists(conn: Connection, url: str) -> bool:
    """url が news に既存か返す（要約前の dedup・URL 重複排除の存在確認・ADR-044）。"""
    stmt = select(news.c.id).where(news.c.url == url).limit(1)
    return conn.execute(stmt).first() is not None


def list_news(
    conn: Connection,
    *,
    level: str | None = None,
    code: str | None = None,
    sector17_code: str | None = None,
    since: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """ニュースを発行日降順で返す（統合コーパスの構造的取り出し・ADR-044）。

    与えられたフィルタ（level/code/sector17_code/since）で AND 絞り込みする。since 指定
    （'YYYY-MM-DD'）は `published_at >= since`。published_at 降順・同値は id 降順で並べ、
    published_at が NULL の行は末尾へ寄る。limit 指定時は件数を絞る。本文は持たない
    （summary と url のみ＝ADR-020）。
    """
    conds = []
    if level is not None:
        conds.append(news.c.level == level)
    if code is not None:
        conds.append(news.c.code == code)
    if sector17_code is not None:
        conds.append(news.c.sector17_code == sector17_code)
    if since is not None:
        conds.append(news.c.published_at >= since)
    stmt = select(news)
    if conds:
        stmt = stmt.where(and_(*conds))
    stmt = stmt.order_by(news.c.published_at.desc(), news.c.id.desc())
    if limit is not None:
        stmt = stmt.limit(limit)
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def get_news_by_url(conn: Connection, url: str) -> dict[str, Any] | None:
    """url 一致の news 1 行を素の dict で返す（無ければ None・ADR-046）。

    ユーザー投入（ingest_user_news）後に、UPSERT で確定した行（id・fetched_at 補完済み）を
    読み直して返すために使う。UNIQUE(url) なので 1 行に定まる。本文は持たない（ADR-020）。
    """
    row = conn.execute(select(news).where(news.c.url == url).limit(1)).mappings().first()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# ADR-045（ニュース意味検索 段階A）: embedding 生成・更新・意味検索
# ---------------------------------------------------------------------------

# news.embedding に貯めた要約＋検索クエリは、ともに float32 little-endian の BLOB として扱う。
# sqlite-vec の vec_distance_cosine は float32 BLOB をベクトルとして受ける（ADR-045）。

# search_news / list_news_needing_embedding が返す列（本文は持たない＝ADR-020）。
_NEWS_EMBED_COLS = (
    "id, level, code, sector17_code, category, source, url, title, summary, "
    "published_at, fetched_at, extraction_status, embed_model"
)


def list_news_needing_embedding(
    conn: Connection, *, current_model: str, limit: int
) -> list[dict[str, Any]]:
    """埋め込みが未生成 or モデル不一致の news 行を limit 件返す（ADR-045）。

    対象は「embedding IS NULL（未埋め込み）」または「embed_model がモデル不一致」の行。
    SQLite に IS DISTINCT FROM は無いため `embed_model IS NULL OR embed_model != :m` で表現する
    （未埋め込み行は embed_model も NULL なので OR の左で拾う）。summary が空の行は埋め込む
    テキストが無いので除外する（embed_news ジョブが summary を埋め込む前提）。id 昇順で安定。
    """
    stmt = text(
        f"SELECT {_NEWS_EMBED_COLS} FROM news "  # noqa: S608 — 列名は定数・ユーザー入力を含まない
        "WHERE summary IS NOT NULL AND summary != '' "
        "AND (embedding IS NULL OR embed_model IS NULL OR embed_model != :m) "
        "ORDER BY id ASC LIMIT :lim"
    )
    rows = conn.execute(stmt, {"m": current_model, "lim": limit}).mappings().all()
    return [dict(r) for r in rows]


def update_news_embedding(
    conn: Connection, news_id: int, embedding_blob: bytes, model: str
) -> None:
    """news 1 行の embedding/embed_model/embedded_at を更新する（ADR-045）。

    embedding_blob は pack_embedding 済みの float32 LE BLOB。embedded_at は UTC now の ISO8601。
    commit はしない＝呼び出し側（ジョブ/service）が `with get_engine().begin() as conn:` で
    境界を所有する（W2・複数行を 1 トランザクションに束ねられる）。
    """
    conn.execute(
        text(
            "UPDATE news SET embedding = :emb, embed_model = :model, embedded_at = :at "
            "WHERE id = :id"
        ),
        {
            "emb": embedding_blob,
            "model": model,
            "at": datetime.now(UTC).isoformat(),
            "id": news_id,
        },
    )


def search_news(
    conn: Connection,
    query_blob: bytes,
    *,
    level: str | None = None,
    code: str | None = None,
    sector17_code: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """意味（embedding 余弦距離）でニュースを近い順に返す（ADR-045）。

    vec_distance_cosine(embedding, :qvec) を距離**昇順**（近い順）に並べる。query_blob は
    pack_embedding 済みの float32 LE BLOB。embedding が NULL の行は除外する。任意フィルタ
    （level/code/sector17_code）と発行日範囲（since=published_at>=・until=published_at<=）を
    AND で絞る。返す行は list_news と同じ列構成＋距離（distance）。本文は持たない（ADR-020）。

    sqlite-vec 未ロード等で vec_distance_cosine が無いと SQL が失敗するが、ここでは握らず投げる
    （呼び出し側 service が握って空＋理由に翻訳する＝ADR-018）。
    """
    conds = ["embedding IS NOT NULL"]
    params: dict[str, Any] = {"qvec": query_blob, "lim": limit}
    if level is not None:
        conds.append("level = :level")
        params["level"] = level
    if code is not None:
        conds.append("code = :code")
        params["code"] = code
    if sector17_code is not None:
        conds.append("sector17_code = :sector17_code")
        params["sector17_code"] = sector17_code
    if since is not None:
        conds.append("published_at >= :since")
        params["since"] = since
    if until is not None:
        conds.append("published_at <= :until")
        params["until"] = until
    where = " AND ".join(conds)
    stmt = text(
        f"SELECT {_NEWS_EMBED_COLS}, "  # noqa: S608 — 列名・WHERE 句は定数組み立て（値は bind）
        "vec_distance_cosine(embedding, :qvec) AS distance "
        f"FROM news WHERE {where} "
        "ORDER BY distance ASC LIMIT :lim"
    )
    rows = conn.execute(stmt, params).mappings().all()
    return [dict(r) for r in rows]


def delete_user_news(news_id: int) -> int:
    """source='user' の news 1 行を id で削除し、削除件数を返す（ADR-046）。

    ユーザー投入分（source='user'）のみ削除し、自動取得分（source='news' 等）は id が一致しても
    消さない（WHERE で source='user' を AND する＝誤って自動取得記事を消さない安全弁）。単発の
    単純な書き込み（1 文で閉じる）なので repo が自前で begin する（W1・remove_watchlist と同流儀）。
    返り値は影響行数（0=対象なし・router が 404 に翻訳）。
    """
    with get_engine().begin() as conn:
        result = conn.execute(
            news.delete().where(and_(news.c.id == news_id, news.c.source == "user"))
        )
    return result.rowcount or 0


# ---------------------------------------------------------------------------
# ADR-049/051（定性 polarity・能動配信）: stock 層ニュースの好/悪/中立タグ付けと悪材料抽出
# ---------------------------------------------------------------------------

# polarity は 'positive'/'negative'/'neutral' の定性タグ（NULL=未判定）。数値スコアは持たない
# （AI に数値を作らせない＝ADR-014/049）。tag_news_polarity が stock 層のみ判定し、notify_digest
# の②保有銘柄悪材料アラートが polarity='negative' を拾う。


def list_news_needing_polarity(conn: Connection, *, limit: int) -> list[dict[str, Any]]:
    """polarity 未判定（NULL）の stock 層ニュースを id 昇順で limit 件返す（ADR-049/051）。

    対象は level='stock' かつ polarity IS NULL の行。判定材料（title/summary）が無い行は除外する
    （summary 空はタグの根拠が無い＝theme_tagger と同じ「テキスト無しに付けない」規律）。
    tag_news_polarity ジョブが embed_news 同型でバッチ判定するための母集団。id 昇順で安定。
    """
    stmt = (
        select(news.c.id, news.c.title, news.c.summary)
        .where(
            and_(
                news.c.level == "stock",
                news.c.polarity.is_(None),
                news.c.summary.isnot(None),
                news.c.summary != "",
            )
        )
        .order_by(news.c.id.asc())
        .limit(limit)
    )
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def update_news_polarity(conn: Connection, news_id: int, polarity: str) -> None:
    """news 1 行の polarity を更新する（ADR-049/051）。

    polarity は 'positive'/'negative'/'neutral' のいずれか（3値外や壊れた応答は呼び出し側が弾き、
    書かず NULL のまま翌晩再試行する＝tag_news_polarity の規律）。commit はしない＝呼び出し側
    （ジョブ）が `with get_engine().begin() as conn:` で境界を所有する（W2・複数行を 1
    トランザクションに束ねられる＝update_news_embedding と同流儀）。
    """
    conn.execute(news.update().where(news.c.id == news_id).values(polarity=polarity))


def list_negative_stock_news_for_codes(
    conn: Connection, codes: list[str], *, fetched_since: str
) -> list[dict[str, Any]]:
    """保有銘柄の悪材料ニュース（polarity='negative'・直近取り込み）を返す（ADR-051・能動配信②）。

    notify_digest の②保有銘柄悪材料アラート用。level='stock' かつ code が codes に含まれ、
    polarity='negative' かつ fetched_at >= fetched_since の行を fetched_at 降順（同値は id 降順）で
    返す。fetched_at 窓（呼び出し側が「今−24h」を渡す）で「同じ悪材料を翌晩再掲しない」を自然に
    実現する（ADR-051）。社名は stocks を LEFT JOIN して company_name を同梱する（名前補完規約）。
    codes 空なら [] を返す（upsert_news の空ガードと同流儀）。
    """
    if not codes:
        return []
    stmt = (
        select(
            news.c.id,
            news.c.code,
            news.c.title,
            news.c.summary,
            news.c.url,
            news.c.published_at,
            news.c.fetched_at,
            stocks.c.company_name,
        )
        .select_from(news.outerjoin(stocks, news.c.code == stocks.c.code))
        .where(
            and_(
                news.c.level == "stock",
                news.c.code.in_(codes),
                news.c.polarity == "negative",
                news.c.fetched_at >= fetched_since,
            )
        )
        .order_by(news.c.fetched_at.desc(), news.c.id.desc())
    )
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def get_dossier(conn: Connection, code: str) -> dict[str, Any] | None:
    """stock_dossiers の 1 行を素の dict で返す（無ければ None・spec §2.2/§5.2）。

    key_facts（JSON TEXT）はパースせず生のまま返す（json.loads は router の責務）。
    sources の JOIN はしない（一覧は list_dossier_sources で別途取得）。
    """
    row = (
        conn.execute(select(stock_dossiers).where(stock_dossiers.c.code == code)).mappings().first()
    )
    return dict(row) if row else None


def list_watchlist(conn: Connection) -> list[dict[str, Any]]:
    """watchlist を company_name・last_investigated_at 付きで返す（spec §2.1/§5.1）。

    company_name は stocks JOIN、last_investigated_at は stock_dossiers LEFT JOIN で補う
    （行レベルに焼かず読むときに結合＝repo 規約）。dossier 未作成の銘柄は
    last_investigated_at が None で返る。stale 判定は per-row の interval_days 基準（ADR-033）で
    上位（router/service）が行う。
    """
    stmt = (
        select(
            watchlist.c.id,
            watchlist.c.code,
            stocks.c.company_name,
            watchlist.c.note,
            watchlist.c.added_at,
            watchlist.c.interval_days,
            stock_dossiers.c.last_investigated_at,
        )
        .select_from(
            watchlist.outerjoin(stocks, watchlist.c.code == stocks.c.code).outerjoin(
                stock_dossiers, watchlist.c.code == stock_dossiers.c.code
            )
        )
        .order_by(watchlist.c.id)
    )
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def add_watchlist(code: str, note: str | None = None, interval_days: int = 21) -> dict[str, Any]:
    """watchlist に 1 銘柄を追加し、その行（dict）を返す（spec §5.1・ADR-002/ADR-033）。

    UNIQUE(code) 衝突時は do_nothing（既存を重複として扱う＝spec §5.1）。挿入でも既存でも
    最終的に code に対応する行を読み直して返す（重複時も既存行を返す）。重複の扱い
    （メッセージ等）は router の責務。added_at 未指定なら UTC now を入れる。
    interval_days は銘柄ごとの調査間隔（既定 21・stale 起点＝ADR-033）。
    """
    added_at = datetime.now(UTC).isoformat()
    stmt = sqlite_insert(watchlist).values(
        code=code, note=note, added_at=added_at, interval_days=interval_days
    )
    stmt = stmt.on_conflict_do_nothing(index_elements=["code"])
    with get_engine().begin() as conn:
        conn.execute(stmt)
    with get_engine().connect() as conn:
        row = conn.execute(select(watchlist).where(watchlist.c.code == code)).mappings().first()
    return dict(row) if row else {}


def set_watchlist_interval(code: str, interval_days: int) -> None:
    """watchlist の銘柄の調査間隔（interval_days）を更新する（ADR-033・間隔設定 UI/PATCH）。

    code で UPDATE する単発の単純な書き込み（1 文で閉じる）なので repo が自前で begin する
    （W1・add/remove_watchlist と同じ流儀）。存在しない code は影響行 0 で静かに終わる
    （存在確認・エラー化は router の責務）。
    """
    with get_engine().begin() as conn:
        conn.execute(
            watchlist.update().where(watchlist.c.code == code).values(interval_days=interval_days)
        )


def remove_watchlist(watchlist_id: int) -> None:
    """watchlist の id 行を削除する（spec §5.1・DELETE /watchlist/{id}）。"""
    with get_engine().begin() as conn:
        conn.execute(watchlist.delete().where(watchlist.c.id == watchlist_id))
