"""テーマタグ company_descriptions/themes/stock_themes（ADR-050 改訂・ADR-056）。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, Table, and_, case, func, or_, select, text, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.engine import get_engine
from app.db.schema import (
    company_descriptions,
    fetch_meta,
    stock_themes,
    stocks,
    themes,
    us_stocks,
)

# ===== テーマタグ（ADR-050 改訂・ADR-056・段階A・data-model.md「テーマタグ」節） =====
# 全ユニバース（JP＋US）を実在テキストに grounded で事前タグ付けする。書き込みは
# UPSERT＋last_seen_at bump（削除しない）＋時間窓 prune で 2 書き手（ユニバースタガー／
# investigate オーバーレイ）が共存する。テーマは定性タグで数値を持たない（ADR-014）。


def _company_description_upsert_stmt(values: dict[str, Any], *, protect_dossier: bool = False):
    """company_descriptions の冪等 UPSERT 文を組む（W1/W2 共有・ADR-050/056）。

    **description_text が既存と同一なら何も更新しない**（DO UPDATE の WHERE 条件で弾く）。
    これにより `fetched_at` は「テキスト最終変化時刻」の意味になり、差分タガーが fetch_meta の
    last_fetched_date と比較して「説明が変化した銘柄」だけを再タグできる（設計の肝・
    list_us/jp_codes_for_theme_tagging の優先順②の判定材料）。同一判定は NULL 安全
    （is_distinct_from＝SQLite では `IS NOT`。既存が NULL でも新テキストで更新できる）。
    fetched_at 未指定なら UTC now を補完する。

    protect_dossier=True（段階C の EDINET 書き込み）: 既存が source='dossier'（JP 調査済み）の
    ときは上書きしない。dossier ⊇ EDINET（ドシエは EDINET の事業の内容＋ニュース＋財務から組まれ
    包含する）ので、ユニバースベースラインの edinet で調査済みオーバーレイを潰さない（ADR-050 実装
    メモ「dossier 行があれば edinet で上書きしない」）。edinet→edinet 更新は通る（NULL 安全＝
    既存 source が NULL でも更新できる）。dossier 書き込み（W2・段階B）は無条件のまま勝つ。
    """
    values = dict(values)
    values.setdefault("fetched_at", datetime.now(UTC).isoformat())
    stmt = sqlite_insert(company_descriptions).values(**values)
    update_cols = ("source", "description_text", "disclosed_date", "doc_id", "fetched_at")
    where = company_descriptions.c.description_text.is_distinct_from(stmt.excluded.description_text)
    if protect_dossier:
        where = and_(where, company_descriptions.c.source.is_distinct_from("dossier"))
    return stmt.on_conflict_do_update(
        index_elements=["market", "code"],
        set_={c: stmt.excluded[c] for c in update_cols if c in values},
        where=where,
    )


def upsert_company_description(row: dict[str, Any], *, protect_dossier: bool = False) -> int:
    """company_descriptions を (market, code) で冪等 UPSERT する（ADR-050/056・W1）。

    バッチ（fetch_us_fundamentals 等）からの単発 UPSERT 用。冪等性（同一テキストは fetched_at
    据え置き）は _company_description_upsert_stmt に集約し W2 版と共有する。
    protect_dossier=True は EDINET 書き込み専用ガード（既存 dossier を上書きしない・段階C）。
    返り値は影響行数（1=新規 or テキスト変化で更新／0=同一テキスト or dossier 保護で据え置き）。
    """
    stmt = _company_description_upsert_stmt(row, protect_dossier=protect_dossier)
    with get_engine().begin() as conn:
        result = conn.execute(stmt)
    return result.rowcount or 0


def upsert_company_description_edinet(row: dict[str, Any]) -> int:
    """EDINET 由来の事業説明を (market, code) で冪等 UPSERT する（段階C・ADR-056・dossier 保護）。

    upsert_company_description の薄いラッパで protect_dossier=True を固定する。クロールジョブは
    要約前に dossier 持ちを事前 skip するが（コスト節約）、ここでも保険として既存 dossier を
    上書きしない二重防御（書き込み順の race・将来の経路変更に対する安全弁・ADR-050 実装メモ）。
    """
    return upsert_company_description(row, protect_dossier=True)


def upsert_company_description_tx(
    conn: Connection,
    *,
    market: str,
    code: str,
    source: str,
    description_text: str | None,
    disclosed_date: str | None = None,
    doc_id: str | None = None,
    fetched_at: str | None = None,
) -> int:
    """company_descriptions を (market, code) で冪等 UPSERT する conn 受け取り版（W2・ADR-050）。

    investigate_stock のように 1 リクエストで複数表（stock_dossiers ＋ company_descriptions）を
    atomic に書く経路用（段階B＝JP 調査済みドシエ要約を信号源に焼く）。**commit はしない。
    呼び出し側が `with get_engine().begin()` で境界を所有する**。冪等性は W1 版
    upsert_company_description と同一ヘルパ _company_description_upsert_stmt で共有する。
    fetched_at=None のときはヘルパが UTC now を補完する。返り値は影響行数。
    """
    values: dict[str, Any] = {
        "market": market,
        "code": code,
        "source": source,
        "description_text": description_text,
        "disclosed_date": disclosed_date,
        "doc_id": doc_id,
    }
    if fetched_at is not None:
        values["fetched_at"] = fetched_at
    stmt = _company_description_upsert_stmt(values)
    result = conn.execute(stmt)
    return result.rowcount or 0


def get_company_description(conn: Connection, market: str, code: str) -> dict[str, Any] | None:
    """company_descriptions の 1 行を (market, code) で引く（無ければ None・ADR-050/056）。"""
    stmt = select(company_descriptions).where(
        and_(company_descriptions.c.market == market, company_descriptions.c.code == code)
    )
    row = conn.execute(stmt).mappings().first()
    return dict(row) if row else None


def insert_themes_if_absent(names: list[str], first_seen_at: str) -> int:
    """themes 目録へ新出テーマだけを追加する（on_conflict_do_nothing・冪等・W1・ADR-050）。

    語彙は単調増加で消さない（reconcile の資産）。既存名は素通しし first_seen_at を潰さない。
    embedding/near_duplicate_of は夜間 embed_themes（後続ウェーブ）が付けるためここでは NULL。
    返り値は実際に挿入された件数（再実行で 0＝冪等）。
    """
    if not names:
        return 0
    # 入力内の重複は 1 行に畳む（順序維持・同一文内の二重 VALUES で衝突させない）。
    unique_names = list(dict.fromkeys(names))
    rows = [{"name": n, "first_seen_at": first_seen_at} for n in unique_names]
    stmt = sqlite_insert(themes).values(rows).on_conflict_do_nothing(index_elements=["name"])
    with get_engine().begin() as conn:
        result = conn.execute(stmt)
    return result.rowcount or 0


def list_theme_names(conn: Connection) -> list[str]:
    """themes の全テーマ名を name 昇順で返す（タガーのプロンプト注入用語彙・ADR-050）。"""
    return list(conn.execute(select(themes.c.name).order_by(themes.c.name)).scalars().all())


def list_themes_with_counts(conn: Connection) -> list[dict[str, Any]]:
    """テーマ目録＋所属銘柄数を返す（Tool `list_themes` の素・ADR-050）。

    themes LEFT JOIN stock_themes ＋ GROUP BY で n_stocks（所属銘柄数・未付与は 0）を都度数える。
    near_duplicate_of は重複候補フラグ（自動マージせず候補提示のみ）。name 昇順で安定。
    """
    stmt = (
        select(
            themes.c.name,
            themes.c.near_duplicate_of,
            themes.c.first_seen_at,
            func.count(stock_themes.c.id).label("n_stocks"),
        )
        .select_from(themes.outerjoin(stock_themes, stock_themes.c.theme_name == themes.c.name))
        .group_by(themes.c.name)
        .order_by(themes.c.name)
    )
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def list_themes_needing_embedding(
    conn: Connection, *, current_model: str, limit: int
) -> list[dict[str, Any]]:
    """埋め込みが未生成 or モデル不一致の themes 行を limit 件返す（ADR-045/050）。

    list_news_needing_embedding と同型。SQLite に IS DISTINCT FROM は無いため
    `embed_model IS NULL OR embed_model != :m` で表現する（未埋め込み行は embed_model も
    NULL なので OR の左で拾う）。name 昇順で安定。
    """
    stmt = (
        select(themes.c.name, themes.c.embed_model)
        .where(
            or_(
                themes.c.embedding.is_(None),
                themes.c.embed_model.is_(None),
                themes.c.embed_model != current_model,
            )
        )
        .order_by(themes.c.name)
        .limit(limit)
    )
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def update_theme_embedding(conn: Connection, name: str, embedding_blob: bytes, model: str) -> None:
    """themes 1 行の embedding/embed_model を更新する（ADR-045/050・W2）。

    embedding_blob は pack_embedding 済みの float32 LE BLOB（news と同じ格納形式＝
    vec_distance_cosine が次元非依存に読む）。commit はしない＝呼び出し側（embed_themes）が
    `with get_engine().begin() as conn:` で境界を所有する（W2・1 バッチの複数行を 1 トランザク
    ションに束ねる＝update_news_embedding と同型・tasks/review-2026-06-12.md §3）。
    """
    stmt = (
        themes.update()
        .where(themes.c.name == name)
        .values(embedding=embedding_blob, embed_model=model)
    )
    conn.execute(stmt)


def find_nearest_theme(conn: Connection, name: str, query_blob: bytes) -> dict[str, Any] | None:
    """`name` 以外で query_blob に最も近いテーマを 1 件返す（語彙 reconcile・ADR-045/050）。

    vec_distance_cosine(embedding, :qvec) の直接スキャンを距離昇順 LIMIT 1（search_news と
    同流儀・vec0 仮想表は使わず次元非依存）。自分自身（name 一致）と embedding NULL の行は
    除外する。戻りは {"name", "distance"} か None（候補なし）。近接判定の閾値比較と
    near_duplicate_of への記録は呼び出し側（embed_themes ジョブ）の責務。
    sqlite-vec 未ロードだと SQL が失敗するが、ここでは握らず投げる（呼び出し側が握って
    degrade する＝ADR-018）。
    """
    stmt = text(
        "SELECT name, vec_distance_cosine(embedding, :qvec) AS distance "
        "FROM themes WHERE embedding IS NOT NULL AND name != :name "
        "ORDER BY distance ASC LIMIT 1"
    )
    row = conn.execute(stmt, {"qvec": query_blob, "name": name}).mappings().first()
    return dict(row) if row else None


def set_theme_near_duplicate(name: str, near_duplicate_of: str | None) -> None:
    """themes 1 行の near_duplicate_of を設定/解除する（重複候補フラグ・ADR-050・W1）。

    自動マージはせず候補提示のみ（None で解除）。単発・1 文で閉じる書き込みなので W1。
    """
    stmt = themes.update().where(themes.c.name == name).values(near_duplicate_of=near_duplicate_of)
    with get_engine().begin() as conn:
        conn.execute(stmt)


def upsert_stock_themes(rows: list[dict[str, Any]]) -> int:
    """stock_themes を (market, code, theme_name) で冪等 UPSERT する（ADR-050・W1）。

    衝突時は **last_seen_at のみ bump** し first_assigned_at は既存値を保持する（EXCLUDED の
    set_ に含めない）。削除はしない＝古いタグは prune_stale_stock_themes の時間窓 prune が
    枯らす。この「UPSERT＋bump・削除しない」が 2 書き手（ユニバースタガー／investigate
    オーバーレイ）のクロバー回避の要（ADR-050 の三択トレードオフ解）。
    rows の各行は {market, code, theme_name, first_assigned_at, last_seen_at} を持つこと。
    """
    if not rows:
        return 0
    stmt = sqlite_insert(stock_themes)
    stmt = stmt.on_conflict_do_update(
        index_elements=["market", "code", "theme_name"],
        set_={"last_seen_at": stmt.excluded["last_seen_at"]},
    )
    with get_engine().begin() as conn:
        conn.execute(stmt, rows)
    return len(rows)


def bump_stock_themes_last_seen(*, market: str, code: str, last_seen_at: str) -> int:
    """指定銘柄の既存テーマタグの last_seen_at を一括 bump する（ADR-050 段階B・W1）。

    説明テキストが前回タグ以降に未変化のとき、tag_jp_themes が LLM を呼ばずに prune を回避する
    ための安価パス（毎晩 LLM 再タグのコスト削減）。re-tag と違い新テーマの発見はしない＝既存の
    タグ集合をそのまま「再確認した」とみなして時間窓を延ばすだけ。返り値は更新行数（タグ 0 件の
    銘柄は 0）。単発・1 文で閉じる書き込みなので W1（repo が自前 begin）。
    """
    stmt = (
        update(stock_themes)
        .where(and_(stock_themes.c.market == market, stock_themes.c.code == code))
        .values(last_seen_at=last_seen_at)
    )
    with get_engine().begin() as conn:
        result = conn.execute(stmt)
    return result.rowcount or 0


def get_stock_themes(conn: Connection, market: str, code: str) -> list[dict[str, Any]]:
    """1 銘柄のテーマ一覧を theme_name 昇順で返す（Tool `get_stock_themes` の素・ADR-050）。"""
    stmt = (
        select(stock_themes)
        .where(and_(stock_themes.c.market == market, stock_themes.c.code == code))
        .order_by(stock_themes.c.theme_name)
    )
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def screen_stocks_by_theme(
    conn: Connection,
    theme: str,
    *,
    market: str | None = None,
    sector17_code: str | None = None,
    gics_sector: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """テーマ所属銘柄を返す（Tool `screen_by_theme` の素・ADR-050）。

    market='US' 行は us_stocks（company_name/gics_sector）、market='JP' 行は stocks
    （company_name/sector17_code）へ LEFT JOIN し、名称とセクターラベルを読み取り時に補完する
    （行レベルに焼かない＝repo 規約）。sector ラベルは US が gics_sector（英語ラベル）、JP が
    sector17_code（J-Quants S17 コード・ADR-053）。sector17_code 絞りは stocks.sector17_code、
    gics_sector 絞りは us_stocks.gics_sector に効く（他市場の行は条件不一致で落ちる）。
    戻り行は market/code/company_name/sector/last_seen_at の事実のみ＝バリュエーション数値は
    含めない（ADR-014: テーマ所属の事実に数値を混ぜない）。
    """
    st = stock_themes
    joined = st.outerjoin(
        us_stocks, and_(st.c.market == "US", st.c.code == us_stocks.c.symbol)
    ).outerjoin(stocks, and_(st.c.market == "JP", st.c.code == stocks.c.code))
    company_name = func.coalesce(us_stocks.c.company_name, stocks.c.company_name).label(
        "company_name"
    )
    sector = func.coalesce(us_stocks.c.gics_sector, stocks.c.sector17_code).label("sector")

    stmt = (
        select(st.c.market, st.c.code, company_name, sector, st.c.last_seen_at)
        .select_from(joined)
        .where(st.c.theme_name == theme)
    )
    if market is not None:
        stmt = stmt.where(st.c.market == market)
    if sector17_code is not None:
        stmt = stmt.where(stocks.c.sector17_code == sector17_code)
    if gics_sector is not None:
        stmt = stmt.where(us_stocks.c.gics_sector == gics_sector)
    stmt = stmt.order_by(st.c.market, st.c.code).limit(limit)
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def prune_stale_stock_themes(*, market: str, cutoff_iso: str) -> int:
    """`market` の stale なテーマタグを時間窓 prune する（ADR-050・W1）。

    DELETE WHERE market=:market AND last_seen_at < :cutoff（境界一致は残す・NULL は消さない）。
    「一定期間どの再タグにも再確認されなかった行だけ枯らす」＝特定書き手基準でないので
    クロバーにならない（upsert_stock_themes の bump と対の設計）。
    **market は必須**＝段階 A のタガーは US のみ稼働のため、JP 行（段階 B/C で付く）を誤って
    枯らさない安全弁。全市場一括 prune の口は意図的に作らない。返り値は削除行数。
    """
    stmt = stock_themes.delete().where(
        and_(stock_themes.c.market == market, stock_themes.c.last_seen_at < cutoff_iso)
    )
    with get_engine().begin() as conn:
        result = conn.execute(stmt)
    return result.rowcount or 0


def _list_codes_for_theme_tagging(
    conn: Connection, *, market: str, join_table: Table, join_key_col: Any, limit: int
) -> list[str]:
    """テーマタガーの巡回対象を市場共通ロジックで優先順に limit 件返す（US/JP の実体）。

    list_us_codes_for_theme_tagging / list_jp_codes_for_theme_tagging の唯一の差は「銘柄マスタ
    （us_stocks/stocks）と JOIN 列（symbol/code）・market・fetch_meta の source 接頭辞」だけなので
    ここに 1 本化する（tasks/review-2026-06-12.md §3・c2fad92 と同じ重複返済）。優先順（①未タグ →
    ②説明変化 → ③古い順ローテ）・ETF 除外・NULL 畳みは両市場で完全同一。
    """
    if limit <= 0:
        return []
    cd = company_descriptions
    src = f"{market.lower()}_themes:" + cd.c.code
    priority = case(
        (fetch_meta.c.source.is_(None), 0),  # ① 未タグ
        (cd.c.fetched_at > fetch_meta.c.last_fetched_date, 1),  # ② 説明変化
        else_=2,  # ③ ローテ
    )
    # NULL（未タグ）を先頭に保つため last_fetched_date の NULL は空文字に畳んで昇順。
    order_age = func.coalesce(fetch_meta.c.last_fetched_date, "")
    stmt = (
        select(cd.c.code)
        .select_from(
            cd.join(join_table, join_key_col == cd.c.code).join(
                fetch_meta, fetch_meta.c.source == src, isouter=True
            )
        )
        .where(cd.c.market == market)
        .where(func.coalesce(join_table.c.is_etf, 0) == 0)  # ETF 除外（NULL は普通株扱い）
        .order_by(priority.asc(), order_age.asc(), cd.c.code.asc())
        .limit(limit)
    )
    return list(conn.execute(stmt).scalars().all())


def list_us_codes_for_theme_tagging(conn: Connection, limit: int) -> list[str]:
    """テーマタガーの巡回対象 US 銘柄を優先順に limit 件返す（ADR-050・ADR-033 同型）。

    company_descriptions（market='US'）を起点に us_stocks へ JOIN し **is_etf=1 を除外**する
    （ETF の longBusinessSummary はファンド運用方針の説明で、事業テーマの信号にならないノイズ）。
    fetch_meta は source キー `'us_themes:' || code` で LEFT JOIN する
    （list_us_symbols_for_fundamentals の `us_fundamentals:<symbol>` と同じキー慣行）。

    優先順（CASE でバケツ分け→バケツ内は last_fetched_date 古い順→code 昇順で安定）:
      ① メタ無し（未タグ）を最優先
      ② company_descriptions.fetched_at > メタの last_fetched_date（説明テキストが前回タグ
         以降に変化した銘柄。upsert_company_description の「同一テキストは fetched_at 据え置き」
         契約が判定を成立させる）
      ③ 残りは last_fetched_date が古い順のローテ（語彙ドリフトの eventual 追従・ADR-033 流用）

    **タガーが 'us_themes:<code>' に書く last_fetched_date は ISO datetime 文字列（時刻まで）**
    であること。②は fetched_at（ISO datetime）との文字列比較で成立する前提
    （'YYYY-MM-DD' 日付のみだと同日内のテキスト変化を取りこぼす）。
    """
    return _list_codes_for_theme_tagging(
        conn, market="US", join_table=us_stocks, join_key_col=us_stocks.c.symbol, limit=limit
    )


def list_jp_codes_for_theme_tagging(conn: Connection, limit: int) -> list[str]:
    """テーマタガーの巡回対象 JP 銘柄を優先順に limit 件返す（ADR-050 段階B・ADR-033 同型）。

    company_descriptions（market='JP'＝investigate_stock が焼いたドシエ要約・source='dossier'）を
    起点に stocks へ JOIN し **is_etf=1 を除外**する（watchlist に ETF を入れる運用は薄いが US 版
    と対称の安全弁）。fetch_meta は source キー `'jp_themes:' || code` で LEFT JOIN する
    （list_us_codes_for_theme_tagging の `us_themes:<code>` と同じキー慣行）。

    優先順は US 版と同一（①未タグ ②fetched_at>last_fetched_date=説明変化 ③古い順ローテ）。
    段階Bでは JP 行は source='dossier' のみ（段階C で EDINET が `source='edinet'` を加える）。
    タガーが 'jp_themes:<code>' に書く last_fetched_date は ISO datetime 文字列（時刻まで）である
    こと（②の文字列比較が同日内のテキスト変化を取りこぼさない前提）。
    """
    return _list_codes_for_theme_tagging(
        conn, market="JP", join_table=stocks, join_key_col=stocks.c.code, limit=limit
    )
