"""judgment_fts（FTS5 trigram）と同期トリガ・bookend を担保する（ADR-078・D-1）。

一時 SQLite（temp_db=create_schema 経路で judgment_fts も立つ）に判断ログを差し込み、
trigram の CJK 部分一致・トリガ自動同期（insert/update/delete）・origin/code フィルタ・
rebuild（backfill/reindex）・bm25 の関連順を検証する（testing-strategy）。
"""

from __future__ import annotations

import json
from typing import Any

from app.db import repo
from app.db.engine import get_engine
from app.db.fts import rebuild_judgment_fts
from app.db.schema import advisor_journal, notable_picks, proposals, stocks


def _insert(table: Any, **values: Any) -> int:
    with get_engine().begin() as conn:
        key = conn.execute(table.insert().values(**values)).inserted_primary_key
        assert key is not None
        return int(key[0])


def _search(query: str, **kwargs: Any) -> list[dict[str, Any]]:
    with get_engine().connect() as conn:
        return repo.search_judgments(conn, query=query, **kwargs)


def _seed_three_sources() -> dict[str, int]:
    jid = _insert(
        advisor_journal,
        date="2026-01-05",
        source="nightly",
        observations="半導体不足が改善し増益基調",
        proposal="押し目で買い増しを検討",
    )
    pid = _insert(
        proposals,
        created_date="2026-01-06",
        kind="buy",
        body=json.dumps({"code": "7203", "company_name": "トヨタ", "market": "JP"}),
        rationale="好決算を受けて押し目買いが妥当",
        status="pending",
    )
    nid = _insert(
        notable_picks,
        date="2026-01-07",
        code="6758",
        reason="出来高急増と上方修正で注目",
        source="nightly",
    )
    return {"journal": jid, "proposal": pid, "notable": nid}


def test_trigram_matches_each_source(temp_db: None) -> None:
    """3 ソースそれぞれが trigram の CJK 部分一致でヒットし origin/銘柄が付く。"""
    _insert(stocks, code="6758", company_name="ソニーG")
    ids = _seed_three_sources()

    j = _search("半導体")
    assert [h["origin"] for h in j] == ["journal"]
    assert j[0]["ref_id"] == ids["journal"]
    assert j[0]["source"] == "nightly"
    assert "outcomes" not in j[0]  # journal は帰結なし（テキストのみ）

    p = _search("好決算")
    assert [h["origin"] for h in p] == ["proposal"]
    assert p[0]["code"] == "7203"
    assert p[0]["company_name"] == "トヨタ"
    assert p[0]["kind"] == "buy"
    assert p[0]["outcomes"] == []  # 採点前は空

    n = _search("上方修正")
    assert [h["origin"] for h in n] == ["notable"]
    assert n[0]["code"] == "6758"
    assert n[0]["company_name"] == "ソニーG"  # stocks から補完
    assert n[0]["kind"] == "notable"


def test_origin_and_code_filters(temp_db: None) -> None:
    """『押し目』は journal と proposal に当たり、origin/code で proposal に絞れる。"""
    ids = _seed_three_sources()
    both = {h["origin"] for h in _search("押し目")}
    assert both == {"journal", "proposal"}

    only_p = _search("押し目", origin="proposal")
    assert [h["origin"] for h in only_p] == ["proposal"]

    by_code = _search("押し目", code="7203")
    assert [h["ref_id"] for h in by_code] == [ids["proposal"]]  # journal は code NULL で除外


def test_trigger_sync_insert_update_delete(temp_db: None) -> None:
    """トリガで判断ログの insert/update/delete が judgment_fts に自動同期される。"""
    jid = _insert(
        advisor_journal, date="2026-02-01", observations="半導体の需要が旺盛", proposal=""
    )
    assert _search("半導体")  # insert 同期

    # update: 語を差し替えると旧語は消え新語で引ける。
    with get_engine().begin() as conn:
        conn.execute(
            advisor_journal.update()
            .where(advisor_journal.c.id == jid)
            .values(observations="地政学リスクで資源株が上昇")
        )
    assert _search("半導体") == []
    assert [h["ref_id"] for h in _search("地政学")] == [jid]

    # delete: 索引からも消える。
    with get_engine().begin() as conn:
        conn.execute(advisor_journal.delete().where(advisor_journal.c.id == jid))
    assert _search("地政学") == []


def test_rebuild_is_idempotent(temp_db: None) -> None:
    """rebuild（全消し＋再投入）後もヒットは 1 件のまま（重複を作らない・reindex 兼用）。"""
    _seed_three_sources()
    assert len(_search("好決算")) == 1
    with get_engine().begin() as conn:
        rebuild_judgment_fts(conn)
    assert len(_search("好決算")) == 1  # 二重登録しない


def test_bm25_orders_more_relevant_first(temp_db: None) -> None:
    """bm25 で関連の高い（短く語が凝縮した）行が先頭に来る。"""
    long_id = _insert(
        advisor_journal,
        date="2026-03-01",
        observations="エネルギー政策の話題は市場で複雑に絡み合い様々な思惑が交錯している",
        proposal="",
    )
    short_id = _insert(advisor_journal, date="2026-03-02", observations="エネルギー", proposal="")
    hits = _search("エネルギー")
    ref_ids = [h["ref_id"] for h in hits]
    assert set(ref_ids) == {long_id, short_id}
    assert ref_ids[0] == short_id  # 短く語が凝縮した行が上位
