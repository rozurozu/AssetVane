"""日足取得ジョブのスタブテスト（spec §3.3/§3.4・§8）。

実 API は叩かない。adapter.fetch_daily_quotes_by_date をスタブ化し、空配列日（非営業日）と
データ日を混在させて「空日スキップ・fetch_meta 前進・UPSERT 行数」を検証する。
date.today() はフェイクで固定し、営業日ループ範囲を決定的にする。
"""

from __future__ import annotations

from datetime import date

import pytest

from app.batch.jobs import fetch_quotes
from app.db import repo
from app.db.engine import get_engine


class _FakeAdapter:
    """fetch_daily_quotes_by_date だけを持つスタブ（ネットを張らない）。

    by_date: {日付文字列: 返す行リスト} の対応。未登録の日は空配列（非営業日扱い）。
    """

    def __init__(self, by_date: dict[str, list[dict]]) -> None:
        self._by_date = by_date
        self.calls: list[str] = []

    def fetch_daily_quotes_by_date(self, d: str) -> list[dict]:
        self.calls.append(d)
        return self._by_date.get(d, [])


class _FakeDate(date):
    """date.today() を固定するためのフェイク。"""

    @classmethod
    def today(cls) -> date:  # type: ignore[override]
        return date(2026, 6, 5)  # 金曜


def _quote(code: str, d: str) -> dict:
    return {
        "code": code,
        "date": d,
        "open": 100.0,
        "high": 110.0,
        "low": 90.0,
        "close": 105.0,
        "volume": 1000.0,
        "adj_close": 105.0,
    }


@pytest.fixture
def _patch(monkeypatch):
    """fetch_quotes が使う date を固定する。adapter はテスト側で個別に差し替える。"""
    monkeypatch.setattr(fetch_quotes, "date", _FakeDate)


def test_skips_empty_days_and_advances_fetch_meta(temp_db, _patch, monkeypatch) -> None:
    # 2026-06-01(月) を最終取得済みとして仕込む → start=2026-06-02(火)。
    repo.upsert_fetch_meta("daily_quotes", "2026-06-01")

    # 営業日: 06-02(火) 06-03(水) 06-04(木) 06-05(金)。
    # 06-03 は空（祝日想定でスキップ）、他はデータあり。
    by_date = {
        "2026-06-02": [_quote("72030", "2026-06-02"), _quote("67580", "2026-06-02")],
        "2026-06-04": [_quote("72030", "2026-06-04")],
        "2026-06-05": [_quote("72030", "2026-06-05")],
    }
    fake = _FakeAdapter(by_date)
    monkeypatch.setattr(fetch_quotes, "JQuantsAdapter", lambda: fake)

    result = fetch_quotes.run(full_backfill=False)

    # 営業日 4 日ぶん（06-02〜06-05）を叩く。土日は candidate_days が除外。
    assert fake.calls == ["2026-06-02", "2026-06-03", "2026-06-04", "2026-06-05"]
    assert result.ok is True
    # UPSERT 行数: 2 + 0 + 1 + 1 = 4。
    assert result.rows == 4

    # fetch_meta は空日も含め最終営業日まで前進している。
    with get_engine().connect() as conn:
        meta = repo.get_fetch_meta(conn, "daily_quotes")
        assert meta is not None
        assert meta["last_fetched_date"] == "2026-06-05"
        # daily_quotes に 4 行入っている。
        assert repo.get_max_quote_date(conn) == "2026-06-05"


def test_failure_returns_not_ok(temp_db, _patch, monkeypatch) -> None:
    repo.upsert_fetch_meta("daily_quotes", "2026-06-03")

    class _Boom:
        def fetch_daily_quotes_by_date(self, d: str) -> list[dict]:
            raise RuntimeError("boom")

    monkeypatch.setattr(fetch_quotes, "JQuantsAdapter", lambda: _Boom())
    result = fetch_quotes.run(full_backfill=False)
    assert result.ok is False
    assert "boom" in result.detail


def test_full_backfill_start_uses_backfill_years(temp_db, _patch, monkeypatch) -> None:
    # full_backfill は fetch_meta を無視し today - backfill_years から開始する。
    from app.config import settings

    monkeypatch.setattr(settings, "backfill_years", 2)
    # fetch_meta を仕込んでも full_backfill では使われない。
    repo.upsert_fetch_meta("daily_quotes", "2026-06-04")

    fake = _FakeAdapter({})  # 全日空（非営業日扱い）でも start 範囲が広いことを確認
    monkeypatch.setattr(fetch_quotes, "JQuantsAdapter", lambda: fake)

    result = fetch_quotes.run(full_backfill=True)
    # 最初の呼び出し日が 2024-06-05（today=2026-06-05 の 2 年前・平日）であること。
    assert fake.calls[0] == "2024-06-05"
    assert result.ok is True
