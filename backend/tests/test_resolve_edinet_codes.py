"""resolve_edinet_codes（sec_code→edinet_code 全件スイープ）ジョブの担保（ADR-083・ADR-018）。

未設定なら静かに skip（ok=True）・設定済みなら /companies を全ページ舐めて stocks に実在する code の
edinet_code だけ一括反映・月次 cadence 内なら skip・full_backfill は cadence を無視、を固定する。
ネットに出ず一時 SQLite で回す（testing-strategy）。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.batch.jobs import resolve_edinet_codes
from app.db import repo
from app.db.engine import get_engine


class _FakeListAdapter:
    """list_companies をページ配列から返す fake（edinetdb.jp 非依存）。"""

    def __init__(self, pages: list[list[dict[str, Any]]]) -> None:
        self._pages = pages
        self.last_budget: dict[str, int | None] = {"monthly_remaining": 500}
        self.calls = 0

    def list_companies(self, *, page: int = 1, per_page: int = 100) -> dict[str, Any]:
        self.calls += 1
        idx = page - 1
        data = self._pages[idx] if 0 <= idx < len(self._pages) else []
        return {
            "data": data,
            "meta": {"pagination": {"page": page, "total_pages": len(self._pages)}},
        }


def test_skip_when_unconfigured(temp_db) -> None:
    """未登録なら ok=True で静かに skip（ADR-064）。"""
    result = resolve_edinet_codes.run()
    assert result.ok is True
    assert result.rows == 0
    assert "skip" in result.detail


def test_sweeps_and_bulk_sets_existing_codes(temp_db, monkeypatch) -> None:
    """/companies を舐めて stocks に実在する sec_code の edinet_code だけ焼く（未収載は無視）。"""
    with get_engine().begin() as conn:
        repo.upsert_edinetdb_config(conn, {"api_key": "edb_test", "plan": "pro"})
    repo.upsert_stocks(
        [
            {"code": "72030", "company_name": "トヨタ", "updated_at": "2026-07-01"},
            {"code": "79740", "company_name": "任天堂", "updated_at": "2026-07-01"},
        ]
    )
    pages = [
        [
            {"sec_code": "72030", "edinet_code": "E02144"},
            {"sec_code": "79740", "edinet_code": "E02367"},
            {"sec_code": "99999", "edinet_code": "E99999"},  # stocks に無い→無視
            {"sec_code": "10000", "edinet_code": None},  # edinet_code 欠落→除く
        ],
    ]
    fake = _FakeListAdapter(pages)
    monkeypatch.setattr(resolve_edinet_codes, "build_edinetdb_adapter", lambda conn: fake)

    result = resolve_edinet_codes.run(full_backfill=True)
    assert result.ok is True
    assert result.rows == 2  # 実在する 2 件だけ更新

    with get_engine().connect() as conn:
        assert repo.get_stock(conn, "72030")["edinet_code"] == "E02144"
        assert repo.get_stock(conn, "79740")["edinet_code"] == "E02367"


def test_cadence_skip_and_full_backfill_override(temp_db, monkeypatch) -> None:
    """月次 cadence 内は skip（API を叩かない）・full_backfill は cadence を無視してスイープ。"""
    with get_engine().begin() as conn:
        repo.upsert_edinetdb_config(conn, {"api_key": "edb_test", "plan": "pro"})
    repo.upsert_fetch_meta("edinet_code_sweep", date.today().isoformat())  # 今日スイープ済み
    fake = _FakeListAdapter([[{"sec_code": "72030", "edinet_code": "E02144"}]])
    monkeypatch.setattr(resolve_edinet_codes, "build_edinetdb_adapter", lambda conn: fake)

    r1 = resolve_edinet_codes.run()  # 定常＝cadence 内で skip
    assert r1.ok is True
    assert r1.rows == 0
    assert "cadence" in r1.detail
    assert fake.calls == 0  # list_companies を叩いていない

    r2 = resolve_edinet_codes.run(full_backfill=True)  # full＝cadence 無視でスイープ
    assert r2.ok is True
    assert fake.calls > 0
