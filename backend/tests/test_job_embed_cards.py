"""embed_cards 夜間ジョブ・即時埋め込みの機能オフ耐性を検証（ADR-062・ADR-045/006）。

担保: embedding 未設定なら run() は ok=True・rows=0 で静かに skip、embed_card_best_effort は no-op
（カードは保存済みのまま・embed_model は付かない）。embedding が有効なときの実 API 呼び出しは
test 環境では行わない（ネット非依存・testing-strategy）。
"""

from __future__ import annotations

import pytest

from app.batch.jobs import embed_cards
from app.db import repo
from app.db.engine import get_engine


@pytest.mark.usefixtures("temp_db")
def test_run_skips_when_embedding_disabled() -> None:
    """embedding 未設定（embedding_config 無し）なら ok=True・rows=0 で skip。"""
    repo.insert_knowledge_card(title="t", body="b", when_to_apply="cond")
    result = embed_cards.run()
    assert result.ok is True
    assert result.rows == 0
    assert "skip" in result.detail


@pytest.mark.usefixtures("temp_db")
def test_best_effort_noop_when_disabled() -> None:
    """機能オフのとき即時埋め込みは no-op（embed_model は付かない・ADR-062 追補）。"""
    cid = repo.insert_knowledge_card(title="t", body="b", when_to_apply="cond")
    embed_cards.embed_card_best_effort(cid)  # 機能オフなら静かに何もしない
    with get_engine().connect() as conn:
        row = repo.get_knowledge_card(conn, cid)
    assert row is not None
    assert row["embed_model"] is None


@pytest.mark.usefixtures("temp_db")
def test_best_effort_noop_for_missing_card() -> None:
    """存在しない card_id でも静かに no-op（例外を投げない）。"""
    embed_cards.embed_card_best_effort(99999)  # get が None → 何もしない
