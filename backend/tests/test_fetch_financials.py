"""fetch_financials ジョブのスタブテスト（ADR-031・全銘柄 by-date 方式・ネット非依存）。

実 API は叩かない。adapter.fetch_financials(date=...) をスタブ化し、営業日ループで
全銘柄の財務を UPSERT・fetch_meta 前進・coverage 打ち切りを検証する（fetch_quotes と同型）。
date.today() はフェイクで固定して営業日ループ範囲を決定的にする。
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import func, select

from app.adapters.jquants import JQuantsCoverageError, JQuantsError
from app.batch.jobs import fetch_financials
from app.db import repo
from app.db.engine import get_engine
from app.db.schema import financials as financials_table


class _FakeAdapter:
    """fetch_financials(date=...) だけを持つスタブ。未登録日は空配列（開示なし）。"""

    def __init__(self, by_date: dict[str, list[dict]]) -> None:
        self._by_date = by_date
        self.calls: list[str] = []

    def fetch_financials(self, code=None, date: str | None = None) -> list[dict]:  # noqa: A002
        assert date is not None  # 本ジョブは常に日付を指定して呼ぶ
        self.calls.append(date)
        return self._by_date.get(date, [])


class _FakeDate(date):
    @classmethod
    def today(cls) -> date:  # type: ignore[override]
        return date(2026, 6, 5)  # 金曜


def _stock(code: str) -> dict:
    return {
        "code": code,
        "company_name": f"会社{code}",
        "sector33_code": "3700",
        "sector17_code": "6",
        "market_code": "0111",
        "is_etf": 0,
        "updated_at": "2026-06-04T00:00:00+00:00",
    }


def _fin(code: str, d: str, period: str = "FY") -> dict:
    return {
        "code": code,
        "disclosed_date": d,
        "fiscal_period": period,
        "net_sales": 1.0,
        "operating_profit": 1.0,
        "profit": 1.0,
        "eps": 100.0,
        "bps": 1000.0,
        "dividend_per_share": 30.0,
        "shares_outstanding": 1_000_000.0,
        "treasury_shares": 0.0,
    }


@pytest.fixture
def _patch(monkeypatch):
    monkeypatch.setattr(fetch_financials, "date", _FakeDate)


def test_universe_by_date_upserts_and_advances_meta(temp_db, _patch, monkeypatch) -> None:
    repo.upsert_stocks([_stock("72030"), _stock("67580"), _stock("99840")])
    repo.upsert_fetch_meta("financials", "2026-06-01")  # start=2026-06-02(火)
    by_date = {
        "2026-06-02": [_fin("72030", "2026-06-02"), _fin("67580", "2026-06-02")],
        "2026-06-04": [_fin("99840", "2026-06-04")],
        # 06-03・06-05 は開示なし（空）
    }
    fake = _FakeAdapter(by_date)
    monkeypatch.setattr(fetch_financials, "build_jquants_adapter", lambda: fake)

    result = fetch_financials.run(full_backfill=False)

    assert fake.calls == ["2026-06-02", "2026-06-03", "2026-06-04", "2026-06-05"]
    assert result.ok is True
    assert result.rows == 3  # 2 + 0 + 1 + 0
    with get_engine().connect() as conn:
        count = conn.execute(select(func.count()).select_from(financials_table)).scalar()
        meta = repo.get_fetch_meta(conn, "financials")
    assert count == 3
    assert meta is not None
    assert meta["last_fetched_date"] == "2026-06-05"  # 空日も含め前進


def test_idempotent(temp_db, _patch, monkeypatch) -> None:
    repo.upsert_stocks([_stock("72030")])
    by_date = {"2026-06-04": [_fin("72030", "2026-06-04")]}
    for _ in range(2):
        repo.upsert_fetch_meta("financials", "2026-06-03")  # 毎回 start=06-04 に戻す
        fake = _FakeAdapter(by_date)
        monkeypatch.setattr(fetch_financials, "build_jquants_adapter", lambda fake=fake: fake)
        fetch_financials.run(full_backfill=False)
    with get_engine().connect() as conn:
        count = conn.execute(select(func.count()).select_from(financials_table)).scalar()
    assert count == 1  # 重複しない


def test_coverage_frontier_stops_cleanly(temp_db, _patch, monkeypatch) -> None:
    repo.upsert_stocks([_stock("72030")])
    repo.upsert_fetch_meta("financials", "2026-06-01")  # start=06-02

    class _CoverageAdapter:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def fetch_financials(self, code=None, date: str | None = None) -> list[dict]:  # noqa: A002
            assert date is not None  # 本ジョブは常に日付を指定して呼ぶ
            self.calls.append(date)
            if date >= "2026-06-04":
                raise JQuantsCoverageError("契約範囲外")
            return [_fin("72030", date)]

    fake = _CoverageAdapter()
    monkeypatch.setattr(fetch_financials, "build_jquants_adapter", lambda: fake)

    result = fetch_financials.run(full_backfill=False)
    assert result.ok is True
    assert fake.calls == ["2026-06-02", "2026-06-03", "2026-06-04"]
    assert result.rows == 2
    with get_engine().connect() as conn:
        meta = repo.get_fetch_meta(conn, "financials")
    assert meta is not None
    assert meta["last_fetched_date"] == "2026-06-03"  # 前線には進めない


def test_failure_returns_not_ok(temp_db, _patch, monkeypatch) -> None:
    repo.upsert_fetch_meta("financials", "2026-06-03")

    class _Boom:
        def fetch_financials(self, code=None, date=None) -> list[dict]:  # noqa: A002
            raise JQuantsError("API 失敗")

    monkeypatch.setattr(fetch_financials, "build_jquants_adapter", lambda: _Boom())
    result = fetch_financials.run(full_backfill=False)
    assert result.ok is False
    assert "API 失敗" in result.detail


def test_full_backfill_start_uses_backfill_years(temp_db, _patch, monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "backfill_years", 2)
    repo.upsert_fetch_meta("financials", "2026-06-04")  # full では無視される
    fake = _FakeAdapter({})
    monkeypatch.setattr(fetch_financials, "build_jquants_adapter", lambda: fake)

    result = fetch_financials.run(full_backfill=True)
    assert fake.calls[0] == "2024-06-05"  # today - 2 年
    assert result.ok is True
