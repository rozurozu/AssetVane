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


def _pin_symbols(monkeypatch, symbols: str) -> None:
    """取得対象を指定シンボルだけに固定する（米国業種 ETF の自動追加を無効化）。

    fetch_index._target_symbols は config の指数に US_SECTOR_ETFS を足すため、
    シンボル数を固定したいテストでは ETF タプルを空に差し替える（Phase 7 追加の副作用を遮断）。
    """
    monkeypatch.setattr(settings, "index_symbols", symbols)
    monkeypatch.setattr(fetch_index, "US_SECTOR_ETFS", ())


def test_fetch_index_run_upserts_rows_and_advances_meta(temp_db, monkeypatch) -> None:
    """fetch_index.run が index_quotes を UPSERT し、fetch_meta を最新日に前進させる。"""
    # index_symbols を テスト用の 1 シンボルに絞る（ETF 自動追加は無効化）
    _pin_symbols(monkeypatch, _TEST_SYMBOL)

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
    _pin_symbols(monkeypatch, _TEST_SYMBOL)

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
    _pin_symbols(monkeypatch, _TEST_SYMBOL)

    with patch("app.batch.jobs.fetch_index.IndexAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.fetch_index_quotes.return_value = _SAMPLE_ROWS

        fetch_index.run()

    # 2 回目: 同じデータで再実行
    with patch("app.batch.jobs.fetch_index.IndexAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.fetch_index_quotes.return_value = _SAMPLE_ROWS

        fetch_index.run()

    # 2 回目は鮮度プローブで重ね窓を取り直すが、同じデータを再 UPSERT しても行数は 2 のまま（冪等）
    with get_engine().connect() as conn:
        rows = repo.get_index_quotes(conn, "^SPX")
    assert len(rows) == 2  # 重複しない


def test_fetch_index_run_adapter_error_returns_failure(temp_db, monkeypatch) -> None:
    """IndexAdapter がエラーを投げた場合、ok=False の JobResult を返す。"""
    _pin_symbols(monkeypatch, _TEST_SYMBOL)

    from app.adapters.index import IndexAdapterError

    with patch("app.batch.jobs.fetch_index.IndexAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.fetch_index_quotes.side_effect = IndexAdapterError("接続失敗")

        result = fetch_index.run()

    assert result.ok is False
    assert "^SPX" in result.detail


def test_fetch_index_run_multiple_symbols(temp_db, monkeypatch) -> None:
    """複数シンボルを処理し、それぞれ UPSERT と fetch_meta が行われる。"""
    _pin_symbols(monkeypatch, "^SPX,^NKX")

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


def test_fetch_index_run_partial_failure_is_ok(temp_db, monkeypatch) -> None:
    """一部シンボルが失敗しても、1 本でも成功すれば ok=True（取得不可は detail に残す）。"""
    _pin_symbols(monkeypatch, "^SPX,^NKX")

    from app.adapters.index import IndexAdapterError

    spx_rows = [{"symbol": "^SPX", "date": "2026-05-28", "close": 5250.36}]

    def fake_fetch(symbol: str, **kwargs: object) -> list[dict]:
        if symbol == "^SPX":
            return spx_rows
        raise IndexAdapterError("取得手段なし")

    with patch("app.batch.jobs.fetch_index.IndexAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.fetch_index_quotes.side_effect = fake_fetch

        result = fetch_index.run()

    assert result.ok is True  # 全滅ではないので失敗扱いにしない
    assert result.rows == 1
    assert "^NKX" in result.detail
    assert "取得不可" in result.detail

    # 試行成否が fetch_meta に記録される（成功=1／失敗=0）。失敗側の last_fetched_date は据え置き。
    with get_engine().connect() as conn:
        spx_meta = repo.get_fetch_meta(conn, "index_quotes:^SPX")
        nkx_meta = repo.get_fetch_meta(conn, "index_quotes:^NKX")
    assert spx_meta is not None and spx_meta["last_attempt_ok"] == 1
    assert nkx_meta is not None and nkx_meta["last_attempt_ok"] == 0
    assert nkx_meta["last_fetched_date"] is None  # 成功歴なし＝据え置きで NULL のまま


def test_fetch_index_run_all_fail_returns_failure(temp_db, monkeypatch) -> None:
    """試行した全シンボルが失敗（総崩れ）したときだけ ok=False。"""
    _pin_symbols(monkeypatch, "^SPX,^NKX")

    from app.adapters.index import IndexAdapterError

    with patch("app.batch.jobs.fetch_index.IndexAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.fetch_index_quotes.side_effect = IndexAdapterError("接続失敗")

        result = fetch_index.run()

    assert result.ok is False
    assert "^SPX" in result.detail
    assert "^NKX" in result.detail


def test_start_date_for_symbol_overlaps_last_fetched(temp_db) -> None:
    """meta ありのとき開始日は last_fetched より前（鮮度プローブで重ねる）。"""
    from datetime import date, timedelta

    from app.batch.jobs._cursor import DEFAULT_OVERLAP_DAYS

    repo.upsert_fetch_meta("index_quotes:^SPX", "2026-06-05")
    start = fetch_index._start_date_for_symbol("^SPX", "2026-06-08")
    assert start == (date(2026, 6, 5) - timedelta(days=DEFAULT_OVERLAP_DAYS)).isoformat()
    assert start <= "2026-06-05"  # 最終取得日に重なる


def test_start_date_for_symbol_backfills_when_no_meta(temp_db) -> None:
    """meta 無し（初回）は backfill 開始（backfill_years 分前）を返す。"""
    start = fetch_index._start_date_for_symbol("^SPX", "2026-06-08")
    assert start == f"{2026 - settings.backfill_years}-06-08"


def test_fetch_index_run_no_new_data_is_ok(temp_db, monkeypatch) -> None:
    """重ね窓で最終取得日のバーだけ返る（新規データ無し）→ ok=True・attempt_ok=1・前進なし。"""
    _pin_symbols(monkeypatch, _TEST_SYMBOL)
    repo.upsert_fetch_meta("index_quotes:^SPX", "2026-05-29")

    # adapter は最終取得日のバーだけ返す（健全だが新規バー無し）。
    overlap_rows = [{"symbol": "^SPX", "date": "2026-05-29", "close": 5265.00}]
    with patch("app.batch.jobs.fetch_index.IndexAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.fetch_index_quotes.return_value = overlap_rows

        result = fetch_index.run()

    assert result.ok is True
    with get_engine().connect() as conn:
        meta = repo.get_fetch_meta(conn, "index_quotes:^SPX")
    assert meta is not None
    assert meta["last_attempt_ok"] == 1  # 失敗ではない＝digest に出ない
    assert meta["last_fetched_date"] == "2026-05-29"  # 新規無しなので前進しない


def test_target_symbols_includes_us_sector_etfs(monkeypatch) -> None:
    """_target_symbols が config 指数＋米国業種 ETF 11 本を重複なく返す（Phase 7・ADR-010）。"""
    from app.adapters.index import US_SECTOR_ETFS

    monkeypatch.setattr(settings, "index_symbols", "^SPX,^NKX,^TPX")

    symbols = fetch_index._target_symbols()

    # 指数 3 本 ＋ ETF 11 本＝14 本（重複なし）
    assert symbols[:3] == ["^SPX", "^NKX", "^TPX"]
    assert set(US_SECTOR_ETFS).issubset(set(symbols))
    assert len(symbols) == 3 + len(US_SECTOR_ETFS)
    assert len(symbols) == len(set(symbols))  # 重複なし


def test_target_symbols_dedupes_when_etf_in_config(monkeypatch) -> None:
    """config に ETF を二重指定しても _target_symbols は重複排除する。"""
    monkeypatch.setattr(settings, "index_symbols", "^SPX,XLK")

    symbols = fetch_index._target_symbols()

    assert symbols.count("XLK") == 1
    assert len(symbols) == len(set(symbols))
