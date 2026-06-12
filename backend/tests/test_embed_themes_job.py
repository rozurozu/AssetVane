"""embed_themes ジョブの埋め込み・near_duplicate 判定を検証する（ADR-050/045/006/018）。

担保すること:
- embedding 未設定なら静かに skip（ok=True・rows=0・ADR-006）。
- 設定時（embed_texts mock）に未埋め込みテーマへ embedding/embed_model が書かれる（ADR-045）。
- 機能有効なのに API 失敗なら ok=False（tag 系と契約対称・ADR-018・
  tasks/review-2026-06-12.md C-7。成功済み埋め込みは永続＝自己回復性維持）。
- near_dup 閾値内（余弦距離 <= 0.15）なら near_duplicate_of がフラグされる。
- 閾値外（直交ベクトル）ならフラグされない（None のまま）。
- 自動マージはされない＝近接でも themes の行数は不変（候補提示のみ・ADR-050）。

embed_texts は monkeypatch（ネットに出ない）。距離計算は engine がロードする実 sqlite-vec の
vec_distance_cosine で検証する（test_news_embedding と同流儀）。
"""

from __future__ import annotations

from app.batch.jobs import embed_themes
from app.config import settings
from app.db import repo
from app.db.engine import get_engine
from app.db.schema import themes


def _theme_rows() -> dict[str, dict]:
    """themes 全行を name キーの dict で返す（検証用）。"""
    with get_engine().connect() as conn:
        return {r["name"]: dict(r) for r in conn.execute(themes.select()).mappings().all()}


def _setup_embedding(monkeypatch, vectors_by_name: dict[str, list[float]]) -> None:
    """embedding を有効化し、テーマ名→ベクトルの fake embed_texts を差し込む。"""
    monkeypatch.setattr(embed_themes, "embedding_enabled", lambda: True)
    monkeypatch.setattr(settings, "embedding_model", "m1")

    async def _fake_embed(texts: list[str]) -> list[list[float]]:
        return [vectors_by_name[t] for t in texts]

    monkeypatch.setattr(embed_themes, "embed_texts", _fake_embed)


def test_skips_when_disabled(temp_db, monkeypatch) -> None:
    """embedding 未設定なら静かに skip（ok=True・rows=0・ADR-006）。"""
    monkeypatch.setattr(embed_themes, "embedding_enabled", lambda: False)
    result = embed_themes.run()
    assert result.ok is True
    assert result.rows == 0
    assert "skip" in result.detail


def test_failed_batch_returns_not_ok(temp_db, monkeypatch) -> None:
    """機能有効なのに API 失敗なら ok=False（tag 系と契約対称・ADR-018・review-2026-06-12 C-7）。

    ok=True のままだと runner の通知に乗らず語彙の埋め込みが静かに陳腐化するため、
    failed_batches > 0 で ok=False に倒すことを担保する。
    """
    repo.insert_themes_if_absent(["生成AI"], "2026-06-01T00:00:00+00:00")
    monkeypatch.setattr(embed_themes, "embedding_enabled", lambda: True)
    monkeypatch.setattr(settings, "embedding_model", "m1")

    async def _boom(texts: list[str]) -> list[list[float]]:
        raise RuntimeError("embeddings API down")

    monkeypatch.setattr(embed_themes, "embed_texts", _boom)
    result = embed_themes.run()

    assert result.ok is False
    assert result.rows == 0
    assert "バッチ失敗" in result.detail


def test_partial_success_persists_then_not_ok(temp_db, monkeypatch) -> None:
    """途中失敗でも成功済み埋め込みは永続し ok=False（自己回復性維持・ADR-018・C-7）。

    1 バッチ目成功→2 バッチ目失敗のとき、成功分の embedding/embed_model は残り
    （翌晩は未埋め込み分だけ再試行される）、結果は ok=False で通知に乗ることを担保する。
    """
    repo.insert_themes_if_absent(["生成AI", "防衛"], "2026-06-01T00:00:00+00:00")
    monkeypatch.setattr(embed_themes, "embedding_enabled", lambda: True)
    monkeypatch.setattr(settings, "embedding_model", "m1")
    monkeypatch.setattr(embed_themes, "_EMBED_BATCH", 1)  # 2 バッチに分割させる

    calls = {"n": 0}

    async def _fail_on_second(texts: list[str]) -> list[list[float]]:
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("embeddings API down")
        return [[1.0, 0.0, 0.0] for _ in texts]

    monkeypatch.setattr(embed_themes, "embed_texts", _fail_on_second)
    result = embed_themes.run()

    assert result.ok is False
    assert result.rows == 1  # 1 バッチ目の成功分は数えられている
    rows = _theme_rows()
    embedded = [r for r in rows.values() if r["embedding"] is not None]
    assert len(embedded) == 1  # 成功済み埋め込みは rollback されず永続


def test_embeds_and_writes_model(temp_db, monkeypatch) -> None:
    """全バッチ成功時は ok=True で embedding/embed_model が書かれる（ADR-045/050・C-7）。"""
    repo.insert_themes_if_absent(["生成AI", "防衛"], "2026-06-01T00:00:00+00:00")
    _setup_embedding(monkeypatch, {"生成AI": [1.0, 0.0, 0.0], "防衛": [0.0, 1.0, 0.0]})

    result = embed_themes.run()

    assert result.ok is True
    assert result.rows == 2
    rows = _theme_rows()
    assert rows["生成AI"]["embedding"] == repo.pack_embedding([1.0, 0.0, 0.0])
    assert rows["生成AI"]["embed_model"] == "m1"
    assert rows["防衛"]["embedding"] is not None
    # 再実行しても二重埋め込みしない（list_themes_needing_embedding が返さない＝冪等）。
    again = embed_themes.run()
    assert again.ok is True
    assert again.rows == 0


def test_near_duplicate_flagged_within_threshold(temp_db, monkeypatch) -> None:
    """余弦距離が閾値内（<=0.15）の近接テーマは near_duplicate_of にフラグされる（ADR-050）。"""
    repo.insert_themes_if_absent(["AI需要", "生成AI"], "2026-06-01T00:00:00+00:00")
    # ほぼ同方向のベクトル（余弦距離 ≒ 0）＝重複候補。
    _setup_embedding(monkeypatch, {"AI需要": [1.0, 0.0, 0.0], "生成AI": [1.0, 0.01, 0.0]})

    result = embed_themes.run()

    assert result.ok is True
    rows = _theme_rows()
    # 新規埋め込み分は双方向に互いを候補としてフラグする（自動マージはしない）。
    assert rows["AI需要"]["near_duplicate_of"] == "生成AI"
    assert rows["生成AI"]["near_duplicate_of"] == "AI需要"
    assert "near_dup フラグ 2 件" in result.detail


def test_no_flag_above_threshold(temp_db, monkeypatch) -> None:
    """閾値を超える（直交＝距離 1）テーマはフラグされない（None のまま・ADR-050）。"""
    repo.insert_themes_if_absent(["半導体", "インバウンド"], "2026-06-01T00:00:00+00:00")
    _setup_embedding(monkeypatch, {"半導体": [1.0, 0.0, 0.0], "インバウンド": [0.0, 1.0, 0.0]})

    result = embed_themes.run()

    assert result.ok is True
    rows = _theme_rows()
    assert rows["半導体"]["near_duplicate_of"] is None
    assert rows["インバウンド"]["near_duplicate_of"] is None
    assert "near_dup フラグ 0 件" in result.detail


def test_no_auto_merge_rows_unchanged(temp_db, monkeypatch) -> None:
    """近接でも自動マージしない＝themes の行数は不変（候補提示のみ・ADR-050）。"""
    repo.insert_themes_if_absent(["AI需要", "生成AI"], "2026-06-01T00:00:00+00:00")
    _setup_embedding(monkeypatch, {"AI需要": [1.0, 0.0, 0.0], "生成AI": [1.0, 0.0, 0.001]})

    embed_themes.run()

    rows = _theme_rows()
    assert set(rows) == {"AI需要", "生成AI"}  # 統合・削除されていない
