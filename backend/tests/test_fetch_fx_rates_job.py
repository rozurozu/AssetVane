"""fetch_fx_rates ジョブの差分カーソル前進・冪等 UPSERT・部分失敗の検証（ADR-002/018/057）。

担保:
  - fake FxAdapter で fx_rates に行が入り fetch_meta['fx:USDJPY'] が前進すること。
  - カーソル不在→初期窓（backfill_years 前〜today）で取得すること。
  - カーソルあり→_REFETCH_OVERLAP_DAYS 日重ねた地点から差分取得すること（鮮度プローブ・
    tasks/review-2026-06-12.md C-1/C-2・test_fetch_index.py のミラー）。
  - 0 行の正常返却はアダプタ契約違反（ADR-018: 0 行＝raise）として ok=False・カーソル据え置き。
  - FxAdapter が例外を投げると ok=False になること。
  - 実 HTTP に出ない（testing-strategy）。
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from app.adapters.fx import FxAdapterError, FxSource
from app.batch.jobs import fetch_fx_rates
from app.db import repo
from app.db.engine import get_engine

# ---------------------------------------------------------------------------
# テスト用 fake FxSource / FxAdapter
# ---------------------------------------------------------------------------


class _FakeSource(FxSource):
    """fetch_rates に固定の行リストか例外を返す fake ソース（ADR-010 テスト注入）。"""

    name = "fake"

    def __init__(self, result: list[dict[str, Any]] | Exception) -> None:
        self._result = result
        self.calls: list[tuple[str, str | None, str | None]] = []

    def fetch_rates(
        self, pair: str, from_: str | None = None, to: str | None = None
    ) -> list[dict[str, Any]]:
        self.calls.append((pair, from_, to))
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _make_adapter(rows_or_exc: list[dict[str, Any]] | Exception):
    """_FakeSource を内包した FxAdapter を返す（sources= で直接注入・ADR-010）。"""
    from app.adapters.fx import FxAdapter

    src = _FakeSource(rows_or_exc)
    adapter = FxAdapter(sources=[src])
    adapter._fake_source = src  # テストからコール検証したいとき用
    return adapter


def _fx_row(date: str, rate: float = 149.5) -> dict[str, Any]:
    return {"date": date, "pair": "USDJPY", "rate": rate}


# ---------------------------------------------------------------------------
# テスト本体
# ---------------------------------------------------------------------------


def test_upsert_and_cursor_advances(temp_db) -> None:
    """fake rows が fx_rates に UPSERT され、fetch_meta['fx:USDJPY'] が最大 date まで前進する。"""
    rows = [
        _fx_row("2026-06-05", 149.0),
        _fx_row("2026-06-06", 150.0),
        _fx_row("2026-06-08", 151.0),  # 最大 date
    ]
    adapter = _make_adapter(rows)
    result = fetch_fx_rates.run(adapter=adapter)

    assert result.ok is True
    assert result.rows == 3

    with get_engine().connect() as conn:
        meta = repo.get_fetch_meta(conn, "fx:USDJPY")
        fx = repo.get_latest_fx_rate(conn, "USDJPY")

    assert meta is not None
    assert meta["last_fetched_date"] == "2026-06-08"
    assert fx is not None
    assert fx["rate"] == pytest.approx(151.0)
    assert fx["date"] == "2026-06-08"


def test_cursor_absent_uses_backfill_start(temp_db, monkeypatch) -> None:
    """fetch_meta 不在時は backfill_years 前から取得を試みる（初期窓チェック）。"""
    from app.config import settings

    monkeypatch.setattr(settings, "backfill_years", 1)

    rows = [_fx_row("2026-06-08", 149.0)]
    adapter = _make_adapter(rows)

    result = fetch_fx_rates.run(adapter=adapter)

    assert result.ok is True
    # fake ソースが呼ばれた from_ が backfill_years 前（今日 − backfill_years 年）であることを確認。
    # 実行日に依存しないよう today から動的に期待値を出す（決め打ちだと年跨ぎで壊れる）。
    today = date.today()
    expected_start = today.replace(year=today.year - settings.backfill_years).isoformat()
    call = adapter._fake_source.calls[0]
    # (pair, from_, to)
    assert call[0] == "USDJPY"
    assert call[1] == expected_start  # 初期窓＝今日 − backfill_years 年（カーソル不在）


def test_cursor_present_overlaps_last_fetched(temp_db) -> None:
    """fetch_meta あり→last_fetched_date に重ねた from_ で取得する（鮮度プローブ・C-1/C-2）。

    test_fetch_index.py の test_start_date_for_symbol_overlaps_last_fetched のミラー。
    重ね窓により週末でも直近営業日が窓に入り、02:00 JST の途中値も翌晩に確定値で
    UPSERT 上書きされる（自己修復・C-2）。
    """
    from datetime import date, timedelta

    from app.batch.jobs._cursor import DEFAULT_OVERLAP_DAYS

    repo.upsert_fetch_meta("fx:USDJPY", "2026-06-07")

    rows = [_fx_row("2026-06-08", 152.0)]
    adapter = _make_adapter(rows)

    result = fetch_fx_rates.run(adapter=adapter)

    assert result.ok is True
    call = adapter._fake_source.calls[0]
    # from_ は 2026-06-07 から _REFETCH_OVERLAP_DAYS 日重ねた地点（= 2026-06-02）。
    assert call[1] == (date(2026, 6, 7) - timedelta(days=DEFAULT_OVERLAP_DAYS)).isoformat()
    assert call[1] <= "2026-06-07"  # 最終取得日に重なる

    with get_engine().connect() as conn:
        meta = repo.get_fetch_meta(conn, "fx:USDJPY")
    assert meta["last_fetched_date"] == "2026-06-08"


def test_no_new_data_keeps_cursor_and_ok(temp_db) -> None:
    """重ね窓で最終取得日のバーだけ返る（新規データ無し）→ ok=True・カーソルは据え置き。

    test_fetch_index.py の test_fetch_index_run_no_new_data_is_ok のミラー（C-1）。
    アダプタは ≥1 行返している（0 行 raise の契約に整合）ので失敗ではない。
    """
    repo.upsert_fetch_meta("fx:USDJPY", "2026-06-07")

    adapter = _make_adapter([_fx_row("2026-06-07", 150.0)])
    result = fetch_fx_rates.run(adapter=adapter)

    assert result.ok is True
    with get_engine().connect() as conn:
        meta = repo.get_fetch_meta(conn, "fx:USDJPY")
    assert meta["last_fetched_date"] == "2026-06-07"  # 新規無しなので前進しない（後退もしない）


def test_zero_rows_is_contract_violation_not_ok(temp_db) -> None:
    """0 行の正常返却は契約違反として ok=False・カーソルは前進しない（ADR-018・C-1）。

    アダプタは 0 行で FxAdapterError を raise する契約のため、0 行が素通りするのは壊れた応答
    （全行 date 欠落等）だけ。旧「休場としてカーソル前進」分岐の撤去を担保する。
    """
    repo.upsert_fetch_meta("fx:USDJPY", "2026-06-07")

    adapter = _make_adapter([])
    result = fetch_fx_rates.run(adapter=adapter)

    assert result.ok is False
    assert result.rows == 0
    assert "契約違反" in result.detail

    with get_engine().connect() as conn:
        meta = repo.get_fetch_meta(conn, "fx:USDJPY")
    assert meta["last_fetched_date"] == "2026-06-07"  # 据え置き（today へ前進しない）


def test_adapter_exception_returns_not_ok(temp_db) -> None:
    """FxAdapter が例外を投げると ok=False を返す（ADR-018 ジョブ境界での握り）。"""
    adapter = _make_adapter(FxAdapterError("yfinance 障害（テスト）"))

    result = fetch_fx_rates.run(adapter=adapter)

    assert result.ok is False
    assert "FX レート取得失敗" in result.detail


def test_idempotent_reupsert(temp_db) -> None:
    """同じ行を 2 回 UPSERT しても重複しない（ADR-002 冪等）。"""
    rows = [_fx_row("2026-06-08", 149.0)]
    adapter = _make_adapter(rows)

    fetch_fx_rates.run(adapter=adapter)
    result2 = fetch_fx_rates.run(adapter=_make_adapter(rows))

    assert result2.ok is True
    with get_engine().connect() as conn:
        fx = repo.get_latest_fx_rate(conn, "USDJPY")
    assert fx["rate"] == pytest.approx(149.0)
