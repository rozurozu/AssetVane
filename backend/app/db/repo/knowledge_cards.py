"""知識カード（knowledge_cards）のクエリ（ADR-062・backend-repo-pattern）。

AI アドバイザーの第 3 の知識源（CORE/POLICY に続く・ADR-015 拡張）。UI で追加・編集し、AI 審査が
status を付け、人間が active 化する（ADR-009）。注入対象は status='active' の行。embedding 3 列は
when_to_apply の意味検索キー（ADR-045 同型・フェーズ2 の retrieval で使う）。

戻り値は素の dict（Pydantic 変換は router の責務）。embedding BLOB は UI に返さないので読み取りは
明示列で返す（embedding は除外）。書き込みは単発が多いので W1（自前 begin）、埋め込み更新だけは
ジョブが複数行を 1 tx に束ねられる W2（conn 受け取り・update_news_embedding 同型）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, delete, insert, select, text, update

from app.db.engine import get_engine
from app.db.schema import knowledge_cards

# UI/注入に返す列（embedding BLOB は除外＝バイト列を router に渡さない）。
_CARD_COLS = (
    "id",
    "title",
    "body",
    "when_to_apply",
    "status",
    "level",
    "sector17_code",
    "theme",
    "linked_signal_type",
    "quant_note",
    "always_inject",
    "source",
    "embed_model",
    "embedded_at",
    "created_at",
    "updated_at",
)

# 編集で書き換えてよい列（status はトリアージ/承認の専用関数で変える＝ここには含めない）。
_EDITABLE_COLS = frozenset(
    {
        "title",
        "body",
        "when_to_apply",
        "level",
        "sector17_code",
        "theme",
        "linked_signal_type",
        "quant_note",
        "always_inject",
        "source",
    }
)


def _select_cols() -> Any:
    return select(*[knowledge_cards.c[name] for name in _CARD_COLS])


# --- 読み取り（conn 注入・commit しない） -----------------------------------


def list_knowledge_cards(conn: Connection, *, status: str | None = None) -> list[dict[str, Any]]:
    """知識カードを一覧する（status 指定で絞り込み・新しい順）。UI の一覧用。"""
    stmt = _select_cols()
    if status is not None:
        stmt = stmt.where(knowledge_cards.c.status == status)
    stmt = stmt.order_by(knowledge_cards.c.updated_at.desc(), knowledge_cards.c.id.desc())
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def get_knowledge_card(conn: Connection, card_id: int) -> dict[str, Any] | None:
    """1 件取得（無ければ None）。"""
    stmt = _select_cols().where(knowledge_cards.c.id == card_id)
    row = conn.execute(stmt).mappings().first()
    return dict(row) if row else None


def list_active_knowledge_cards(conn: Connection) -> list[dict[str, Any]]:
    """注入対象（status='active'）のカードを返す（フェーズ1 の常時注入用）。

    フェーズ2 ではここを when_to_apply の意味検索に置き換える（ADR-062・ADR-045 同型）。
    """
    stmt = (
        _select_cols()
        .where(knowledge_cards.c.status == "active")
        .order_by(knowledge_cards.c.id.asc())
    )
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def list_cards_needing_embedding(
    conn: Connection, *, current_model: str, limit: int
) -> list[dict[str, Any]]:
    """when_to_apply があり、埋め込み未生成 or モデル不一致のカードを limit 件返す（ADR-045 同型）。

    embed_cards 夜間ジョブが when_to_apply を埋め込む（id 昇順で安定・id と when_to_apply を返す）。
    """
    stmt = (
        select(knowledge_cards.c.id, knowledge_cards.c.when_to_apply)
        .where(knowledge_cards.c.when_to_apply.isnot(None))
        .where(knowledge_cards.c.when_to_apply != "")
        .where(
            (knowledge_cards.c.embedding.is_(None))
            | (knowledge_cards.c.embed_model.is_(None))
            | (knowledge_cards.c.embed_model != current_model)
        )
        .order_by(knowledge_cards.c.id.asc())
        .limit(limit)
    )
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


# --- 書き込み ---------------------------------------------------------------


def insert_knowledge_card(
    *,
    title: str,
    body: str,
    when_to_apply: str | None = None,
    status: str = "draft",
    level: str | None = None,
    sector17_code: str | None = None,
    theme: str | None = None,
    linked_signal_type: str | None = None,
    quant_note: str | None = None,
    always_inject: int = 0,
    source: str | None = None,
) -> int:
    """カードを 1 件挿入し新 id を返す（W1・自前 begin）。created_at/updated_at は now。

    挿入は冪等でない（POST ごとに 1 行）。埋め込みは別途（保存後に best-effort で when_to_apply を
    埋め込む＝await を書き込みトランザクション外に置く・ADR-045/C-6 の規律）。
    """
    now = datetime.now(UTC).isoformat()
    stmt = insert(knowledge_cards).values(
        title=title,
        body=body,
        when_to_apply=when_to_apply,
        status=status,
        level=level,
        sector17_code=sector17_code,
        theme=theme,
        linked_signal_type=linked_signal_type,
        quant_note=quant_note,
        always_inject=always_inject,
        source=source,
        created_at=now,
        updated_at=now,
    )
    with get_engine().begin() as conn:
        result = conn.execute(stmt)
    pk = result.inserted_primary_key
    return int(pk[0]) if pk else 0


def update_knowledge_card(card_id: int, values: dict[str, Any]) -> None:
    """編集可能な列だけを更新する（W1・自前 begin）。updated_at は now に更新。

    when_to_apply を変えたときは embedding を無効化（NULL）して、夜間 embed_cards が再埋め込みする
    （retrieval キーが古いベクトルのまま残らないようにする・ADR-045）。values の未知列は無視する。
    """
    fields = {k: v for k, v in values.items() if k in _EDITABLE_COLS}
    if not fields:
        return
    fields["updated_at"] = datetime.now(UTC).isoformat()
    if "when_to_apply" in fields:
        fields["embedding"] = None
        fields["embed_model"] = None
        fields["embedded_at"] = None
    with get_engine().begin() as conn:
        conn.execute(
            update(knowledge_cards).where(knowledge_cards.c.id == card_id).values(**fields)
        )


def set_knowledge_card_status(
    card_id: int,
    *,
    status: str,
    quant_note: str | None = None,
    linked_signal_type: str | None = None,
) -> None:
    """status を遷移する（AI 審査・人間承認の両方が使う・W1）。

    quant_note/linked_signal_type は渡されたときだけ更新（None は「変更しない」＝既存値を温存）。
    needs_quant の必要計算メモや、紐づく signal_type の付与に使う。
    """
    fields: dict[str, Any] = {
        "status": status,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    if quant_note is not None:
        fields["quant_note"] = quant_note
    if linked_signal_type is not None:
        fields["linked_signal_type"] = linked_signal_type
    with get_engine().begin() as conn:
        conn.execute(
            update(knowledge_cards).where(knowledge_cards.c.id == card_id).values(**fields)
        )


def delete_knowledge_card(card_id: int) -> int:
    """カードを 1 件削除し、削除行数を返す（W1）。"""
    with get_engine().begin() as conn:
        result = conn.execute(delete(knowledge_cards).where(knowledge_cards.c.id == card_id))
    return result.rowcount


def search_knowledge_cards(
    conn: Connection,
    query_blob: bytes,
    *,
    level: str | None = None,
    sector17_code: str | None = None,
    theme: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """active カードを when_to_apply embedding の余弦距離で近い順に返す（ADR-062・search_news 型）。

    vec_distance_cosine(embedding, :qvec) を距離昇順（近い順）。query_blob は pack_embedding 済みの
    float32 LE BLOB。status='active' かつ embedding 非 NULL の行のみ。level/sector17_code/theme で
    構造事前フィルタ。返す列は _CARD_COLS ＋ distance（embedding BLOB なし）。sqlite-vec 未ロード
    だと SQL が失敗するが握らず投げる（呼び出し側 service が空＋理由に翻訳・ADR-018）。
    """
    conds = ["status = 'active'", "embedding IS NOT NULL"]
    params: dict[str, Any] = {"qvec": query_blob, "lim": limit}
    if level is not None:
        conds.append("level = :level")
        params["level"] = level
    if sector17_code is not None:
        conds.append("sector17_code = :sector17_code")
        params["sector17_code"] = sector17_code
    if theme is not None:
        conds.append("theme = :theme")
        params["theme"] = theme
    where = " AND ".join(conds)
    cols = ", ".join(_CARD_COLS)
    stmt = text(
        f"SELECT {cols}, vec_distance_cosine(embedding, :qvec) AS distance "  # noqa: S608 — 列名は定数
        f"FROM knowledge_cards WHERE {where} ORDER BY distance ASC LIMIT :lim"
    )
    return [dict(r) for r in conn.execute(stmt, params).mappings().all()]


def update_card_embedding(
    conn: Connection, card_id: int, embedding_blob: bytes, model: str
) -> None:
    """カード 1 行の embedding/embed_model/embedded_at を更新（W2・update_news_embedding 同型）。

    commit はしない＝呼び出し側（embed_cards ジョブ／保存時 best-effort）が begin 境界を所有する。
    """
    conn.execute(
        text(
            "UPDATE knowledge_cards SET embedding = :emb, embed_model = :model, "
            "embedded_at = :at WHERE id = :id"
        ),
        {
            "emb": embedding_blob,
            "model": model,
            "at": datetime.now(UTC).isoformat(),
            "id": card_id,
        },
    )
