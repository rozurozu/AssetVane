"""fetch_us_quotes のバッチ分割 UPSERT と差分カーソル前進の検証（ADR-002/018/039）。

担保: fake fetch_quotes でシンボルをバッチ分割して UPSERT すること・全銘柄共通カーソル
fetch_meta['us_daily_quotes'] が取得最大 date まで前進すること・部分失敗で後続を止めず ok を
正しく決めること・full_backfill と差分の開始日分岐。fake adapter で実 HTTP に出ない
（testing-strategy）。
"""

from __future__ import annotations

from typing import Any

from app.batch.jobs import fetch_us_quotes
from app.config import settings
from app.db import repo
from app.db.engine import get_engine


class _FakeAdapter:
    """fetch_quotes だけ持つ fake（symbol→行リスト or 例外）。呼ばれた from_/to を記録する。"""

    def __init__(self, by_symbol: dict[str, Any]) -> None:
        self._by_symbol = by_symbol
        self.calls: list[tuple[str, str | None, str | None]] = []

    def fetch_quotes(
        self, symbol: str, from_: str | None = None, to: str | None = None
    ) -> list[dict[str, Any]]:
        self.calls.append((symbol, from_, to))
        val = self._by_symbol[symbol]
        if isinstance(val, Exception):
            raise val
        return val


def _q(symbol: str, date: str, close: float) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "date": date,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1.0,
        "adj_close": close,
    }


def _seed_universe(symbols: list[str]) -> None:
    repo.upsert_us_stocks(
        [{"symbol": s, "company_name": s, "is_etf": 0, "updated_at": "t"} for s in symbols]
    )


def test_batched_upsert_and_cursor_advances(temp_db, monkeypatch) -> None:
    """3 銘柄を batch_size=2 で分割 UPSERT し、カーソルが取得最大 date まで前進する。"""
    _seed_universe(["AAA", "BBB", "CCC"])
    monkeypatch.setattr(settings, "us_quotes_batch_size", 2)
    fake = _FakeAdapter(
        {
            "AAA": [_q("AAA", "2026-06-05", 10.0), _q("AAA", "2026-06-08", 11.0)],
            "BBB": [_q("BBB", "2026-06-08", 20.0)],
            "CCC": [_q("CCC", "2026-06-09", 30.0)],  # 最大 date
        }
    )
    result = fetch_us_quotes.run(adapter=fake)  # type: ignore[arg-type]
    assert result.ok is True
    assert result.rows == 4  # 2 + 1 + 1 行

    with get_engine().connect() as conn:
        aaa = repo.get_us_quotes(conn, "AAA")
        ccc = repo.get_us_quotes(conn, "CCC")
        meta = repo.get_fetch_meta(conn, "us_daily_quotes")
    assert [r["date"] for r in aaa] == ["2026-06-05", "2026-06-08"]  # date 昇順
    assert ccc[0]["close"] == 30.0
    # 全銘柄共通カーソルは取得最大 date（CCC の 2026-06-09）まで前進。
    assert meta["last_fetched_date"] == "2026-06-09"


def test_idempotent_reupsert(temp_db) -> None:
    """同じ行を 2 回流しても重複しない（(symbol,date) UPSERT 冪等・ADR-002）。"""
    _seed_universe(["AAA"])
    fake = _FakeAdapter({"AAA": [_q("AAA", "2026-06-08", 10.0)]})
    fetch_us_quotes.run(adapter=fake)  # type: ignore[arg-type]
    fetch_us_quotes.run(adapter=fake)  # type: ignore[arg-type]
    with get_engine().connect() as conn:
        rows = repo.get_us_quotes(conn, "AAA")
    assert len(rows) == 1


def test_partial_failure_not_total_is_ok(temp_db, monkeypatch) -> None:
    """1 銘柄失敗でも 1 本でも成功すれば ok=True（総崩れのみ失敗・fetch_index 同型）。"""
    _seed_universe(["AAA", "BAD"])
    monkeypatch.setattr(settings, "us_quotes_batch_size", 10)
    fake = _FakeAdapter({"AAA": [_q("AAA", "2026-06-08", 10.0)], "BAD": RuntimeError("boom")})
    result = fetch_us_quotes.run(adapter=fake)  # type: ignore[arg-type]
    assert result.ok is True
    assert result.rows == 1
    assert "失敗 1 件" in result.detail


def test_total_failure_is_not_ok(temp_db, monkeypatch) -> None:
    """試行した全シンボルが失敗（総崩れ）なら ok=False（runner が Discord 通知）。"""
    _seed_universe(["BAD1", "BAD2"])
    monkeypatch.setattr(settings, "us_quotes_batch_size", 10)
    fake = _FakeAdapter({"BAD1": RuntimeError("x"), "BAD2": RuntimeError("y")})
    result = fetch_us_quotes.run(adapter=fake)  # type: ignore[arg-type]
    assert result.ok is False


def test_full_backfill_vs_incremental_start(temp_db, monkeypatch) -> None:
    """full_backfill は BACKFILL_YEARS 分頭から・差分はカーソル翌日から（開始日分岐）。"""
    _seed_universe(["AAA"])
    monkeypatch.setattr(settings, "backfill_years", 2)
    # まず差分用カーソルを置く。
    repo.upsert_fetch_meta("us_daily_quotes", "2026-06-08")

    fake = _FakeAdapter({"AAA": [_q("AAA", "2026-06-09", 10.0)]})
    fetch_us_quotes.run(adapter=fake, full_backfill=False)  # type: ignore[arg-type]
    # 差分: カーソル 2026-06-08 の翌日 2026-06-09 から（曜日関係なく翌日）。
    assert fake.calls[-1][1] == "2026-06-09"

    fake2 = _FakeAdapter({"AAA": [_q("AAA", "2026-06-09", 10.0)]})
    fetch_us_quotes.run(adapter=fake2, full_backfill=True)  # type: ignore[arg-type]
    # full_backfill: from_ は today-2 年（年部分が 2 年前）。月日は today 依存なので年だけ確認。
    from datetime import date

    expected_year = date.today().replace(year=date.today().year - 2).year
    assert fake2.calls[-1][1].startswith(str(expected_year))
