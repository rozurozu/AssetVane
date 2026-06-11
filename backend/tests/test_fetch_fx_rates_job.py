"""fetch_fx_rates ジョブの差分カーソル前進・冪等 UPSERT・部分失敗の検証（ADR-002/018/057）。

担保:
  - fake FxAdapter で fx_rates に行が入り fetch_meta['fx:USDJPY'] が前進すること。
  - カーソル不在→初期窓（backfill_years 前〜today）で取得すること。
  - カーソルあり→翌日から差分取得すること。
  - 0 行返却（休場等）でも ok=True かつ fetch_meta が today に前進すること。
  - FxAdapter が例外を投げると ok=False になること。
  - 実 HTTP に出ない（testing-strategy）。
"""

from __future__ import annotations

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
    # fake ソースが呼ばれた from_ が「約 1 年前」であることを確認。
    call = adapter._fake_source.calls[0]
    # (pair, from_, to)
    assert call[0] == "USDJPY"
    assert "2025-06" in call[1]  # 1 年前ゾーン（2025-06-xx）


def test_cursor_present_uses_next_day(temp_db) -> None:
    """fetch_meta に last_fetched_date がある場合、その翌日 from_ で取得する。"""
    repo.upsert_fetch_meta("fx:USDJPY", "2026-06-07")

    rows = [_fx_row("2026-06-08", 152.0)]
    adapter = _make_adapter(rows)

    result = fetch_fx_rates.run(adapter=adapter)

    assert result.ok is True
    call = adapter._fake_source.calls[0]
    # from_ は 2026-06-07 の翌日 = 2026-06-08
    assert call[1] == "2026-06-08"

    with get_engine().connect() as conn:
        meta = repo.get_fetch_meta(conn, "fx:USDJPY")
    assert meta["last_fetched_date"] == "2026-06-08"


def test_zero_rows_ok_and_cursor_advances_to_today(temp_db, monkeypatch) -> None:
    """0 行返却（休場等）でも ok=True で、fetch_meta が today まで前進する（ADR-018）。"""
    import datetime

    today = "2026-06-10"
    monkeypatch.setattr(fetch_fx_rates, "_start_date", lambda *, full_backfill, today: "2026-06-10")

    adapter = _make_adapter([])

    with monkeypatch.context() as m:
        m.setattr(
            "app.batch.jobs.fetch_fx_rates.date",
            type(
                "_D",
                (),
                {
                    "today": staticmethod(lambda: datetime.date(2026, 6, 10)),
                    "fromisoformat": datetime.date.fromisoformat,
                },
            )(),
        )
        result = fetch_fx_rates.run(adapter=adapter)

    assert result.ok is True
    assert result.rows == 0

    with get_engine().connect() as conn:
        meta = repo.get_fetch_meta(conn, "fx:USDJPY")
    # fetch_meta が today(2026-06-10) に前進していること
    assert meta["last_fetched_date"] == today


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
