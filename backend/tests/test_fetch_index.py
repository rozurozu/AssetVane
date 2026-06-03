"""fetch_index ジョブの単体テスト（phase2-spec.md §8・ネット非依存）。

IndexAdapter をモンキーパッチで差し替え、fetch_index.run が
upsert 行数と fetch_meta 前進を正しく行うことを検証する。
実 API は叩かない。`temp_db` フィクスチャを使い本物 DB に触れない。
"""

from __future__ import annotations

from unittest.mock import patch

from app.batch.jobs import fetch_index
from app.config import settings
from app.db import repo
from app.db.engine import get_engine

# テスト用シンボルと行データ
_TEST_SYMBOL = "^SPX"
_SAMPLE_ROWS = [
    {"symbol": "^SPX", "date": "2026-05-28", "close": 5250.36},
    {"symbol": "^SPX", "date": "2026-05-29", "close": 5265.00},
]


def test_fetch_index_run_upserts_rows_and_advances_meta(temp_db, monkeypatch) -> None:
    """fetch_index.run が index_quotes を UPSERT し、fetch_meta を最新日に前進させる。"""
    # index_symbols を テスト用の 1 シンボルに絞る
    monkeypatch.setattr(settings, "index_symbols", _TEST_SYMBOL)

    # IndexAdapter.fetch_index_quotes をスタブ化する
    with patch("app.batch.jobs.fetch_index.IndexAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.fetch_index_quotes.return_value = _SAMPLE_ROWS

        result = fetch_index.run()

    assert result.ok is True
    assert result.rows == 2

    # index_quotes に行が入っている
    with get_engine().connect() as conn:
        rows = repo.get_index_quotes(conn, "^SPX")
    assert len(rows) == 2
    assert rows[0]["date"] == "2026-05-28"
    assert rows[1]["date"] == "2026-05-29"

    # fetch_meta が最新取得日（2026-05-29）に前進している
    with get_engine().connect() as conn:
        meta = repo.get_fetch_meta(conn, "index_quotes:^SPX")
    assert meta is not None
    assert meta["last_fetched_date"] == "2026-05-29"


def test_fetch_index_run_empty_rows_advances_meta(temp_db, monkeypatch) -> None:
    """fetch_index.run が空配列を返した場合も fetch_meta を today まで前進させる。"""
    monkeypatch.setattr(settings, "index_symbols", _TEST_SYMBOL)

    with patch("app.batch.jobs.fetch_index.IndexAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.fetch_index_quotes.return_value = []

        result = fetch_index.run()

    assert result.ok is True
    assert result.rows == 0

    # fetch_meta が today に前進している
    from datetime import date

    today = date.today().isoformat()
    with get_engine().connect() as conn:
        meta = repo.get_fetch_meta(conn, "index_quotes:^SPX")
    assert meta is not None
    assert meta["last_fetched_date"] == today


def test_fetch_index_run_idempotent(temp_db, monkeypatch) -> None:
    """fetch_index.run を 2 回実行しても行数が増えない（UPSERT 冪等）。"""
    monkeypatch.setattr(settings, "index_symbols", _TEST_SYMBOL)

    with patch("app.batch.jobs.fetch_index.IndexAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.fetch_index_quotes.return_value = _SAMPLE_ROWS

        fetch_index.run()

    # 2 回目: 同じデータで再実行
    with patch("app.batch.jobs.fetch_index.IndexAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.fetch_index_quotes.return_value = _SAMPLE_ROWS

        fetch_index.run()

    # 2 回目は fetch_meta が最新日より先の start_date になるため空配列になる（スキップ）
    # または同日データを再 UPSERT しても行数は 2 のまま
    with get_engine().connect() as conn:
        rows = repo.get_index_quotes(conn, "^SPX")
    assert len(rows) == 2  # 重複しない


def test_fetch_index_run_adapter_error_returns_failure(temp_db, monkeypatch) -> None:
    """IndexAdapter がエラーを投げた場合、ok=False の JobResult を返す。"""
    monkeypatch.setattr(settings, "index_symbols", _TEST_SYMBOL)

    from app.adapters.index import IndexAdapterError

    with patch("app.batch.jobs.fetch_index.IndexAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.fetch_index_quotes.side_effect = IndexAdapterError("接続失敗")

        result = fetch_index.run()

    assert result.ok is False
    assert "^SPX" in result.detail


def test_fetch_index_run_multiple_symbols(temp_db, monkeypatch) -> None:
    """複数シンボルを処理し、それぞれ UPSERT と fetch_meta が行われる。"""
    monkeypatch.setattr(settings, "index_symbols", "^SPX,^NKX")

    spx_rows = [{"symbol": "^SPX", "date": "2026-05-28", "close": 5250.36}]
    nkx_rows = [{"symbol": "^NKX", "date": "2026-05-28", "close": 38000.0}]

    def fake_fetch(symbol: str, **kwargs: object) -> list[dict]:
        if symbol == "^SPX":
            return spx_rows
        if symbol == "^NKX":
            return nkx_rows
        return []

    with patch("app.batch.jobs.fetch_index.IndexAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.fetch_index_quotes.side_effect = fake_fetch

        result = fetch_index.run()

    assert result.ok is True
    assert result.rows == 2

    with get_engine().connect() as conn:
        spx = repo.get_index_quotes(conn, "^SPX")
        nkx = repo.get_index_quotes(conn, "^NKX")
    assert len(spx) == 1
    assert len(nkx) == 1
