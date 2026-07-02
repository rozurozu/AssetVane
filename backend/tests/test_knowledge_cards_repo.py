"""knowledge_cards repo の CRUD・status 遷移・active 抽出・埋め込み対象抽出を検証（ADR-062）。

担保: insert→get/list（embedding BLOB は返さない）・status 絞り込み・list_active（active のみ）・
update（when_to_apply 変更で embedding 無効化）・set_status（quant_note 温存）・delete・
list_cards_needing_embedding（when_to_apply あり＆未埋め込みのみ）。一時 SQLite・LLM/ネット非依存。
"""

from __future__ import annotations

import pytest

from app.db import repo
from app.db.engine import get_engine


@pytest.mark.usefixtures("temp_db")
def test_insert_and_get_excludes_embedding() -> None:
    """挿入→取得で row が返り、embedding BLOB はキーに含まれない。"""
    cid = repo.insert_knowledge_card(
        title="t1", body="b1", when_to_apply="cond1", status="draft", level="market"
    )
    with get_engine().connect() as conn:
        row = repo.get_knowledge_card(conn, cid)
    assert row is not None
    assert row["title"] == "t1"
    assert row["status"] == "draft"
    assert row["level"] == "market"
    assert "embedding" not in row  # BLOB は UI に返さない


@pytest.mark.usefixtures("temp_db")
def test_list_filter_and_active() -> None:
    """status 絞り込みと list_active（active のみ）を検証。"""
    a = repo.insert_knowledge_card(title="active1", body="b", status="active")
    repo.insert_knowledge_card(title="draft1", body="b", status="draft")
    with get_engine().connect() as conn:
        assert len(repo.list_knowledge_cards(conn)) == 2
        assert len(repo.list_knowledge_cards(conn, status="active")) == 1
        actives = repo.list_active_knowledge_cards(conn)
    assert len(actives) == 1
    assert actives[0]["id"] == a


@pytest.mark.usefixtures("temp_db")
def test_update_changes_fields_and_invalidates_embedding() -> None:
    """編集可能列を更新し、when_to_apply 変更で embed_model が NULL に戻る。"""
    cid = repo.insert_knowledge_card(title="t", body="b", when_to_apply="old")
    # 疑似的に埋め込み済みにする（embed_model を入れておく）。
    with get_engine().begin() as conn:
        repo.update_card_embedding(conn, cid, b"\x00\x00\x00\x00", "model-x")
    repo.update_knowledge_card(cid, {"body": "b2", "when_to_apply": "new"})
    with get_engine().connect() as conn:
        row = repo.get_knowledge_card(conn, cid)
    assert row is not None
    assert row["body"] == "b2"
    assert row["when_to_apply"] == "new"
    assert row["embed_model"] is None  # when_to_apply 変更で embedding 無効化


@pytest.mark.usefixtures("temp_db")
def test_set_status_keeps_existing_quant_note() -> None:
    """set_status は渡されない quant_note を温存し、status だけ更新する。"""
    cid = repo.insert_knowledge_card(title="t", body="b")
    repo.set_knowledge_card_status(cid, status="needs_quant", quant_note="X を計算")
    repo.set_knowledge_card_status(cid, status="active")
    with get_engine().connect() as conn:
        row = repo.get_knowledge_card(conn, cid)
    assert row is not None
    assert row["status"] == "active"
    assert row["quant_note"] == "X を計算"  # quant_note=None は温存


@pytest.mark.usefixtures("temp_db")
def test_delete() -> None:
    """削除すると取得できなくなる。"""
    cid = repo.insert_knowledge_card(title="t", body="b")
    assert repo.delete_knowledge_card(cid) == 1
    with get_engine().connect() as conn:
        assert repo.get_knowledge_card(conn, cid) is None


@pytest.mark.usefixtures("temp_db")
def test_list_cards_needing_embedding() -> None:
    """body があり未埋め込み/モデル不一致のみ返す（when_to_apply 空でも対象・ADR-062 追補）。"""
    need = repo.insert_knowledge_card(title="need", body="b", when_to_apply="cond")
    nowta = repo.insert_knowledge_card(title="nowta", body="b", when_to_apply=None)
    done = repo.insert_knowledge_card(title="done", body="b", when_to_apply="cond2")
    with get_engine().begin() as conn:
        repo.update_card_embedding(conn, done, b"\x00\x00\x00\x00", "m")
    with get_engine().connect() as conn:
        rows = repo.list_cards_needing_embedding(conn, current_model="m", limit=10)
    ids = {r["id"] for r in rows}
    assert need in ids
    assert nowta in ids  # when_to_apply なしも body で対象（本文ベース埋め込み）
    assert done not in ids  # 同一モデルで埋め込み済みは除外
    assert all(r["body"] for r in rows)  # body は必ずある（埋め込み元の合成テキスト）
