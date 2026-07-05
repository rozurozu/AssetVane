"""POST /valuation/backfill-net-cash（清原式ネットキャッシュ全銘柄取得の起動口）の担保（ADR-083）。

未設定・free は 400 で拒否・pro は 202 で run_jobs を full_backfill=True で起動、を固定する。
background task の実体（run_jobs）は monkeypatch でスタブ化しネットに出さない（testing-strategy）。
"""

from __future__ import annotations

from typing import Any

from app.db import repo
from app.db.engine import get_engine


def test_rejects_when_unconfigured(client) -> None:
    """EDINET DB 未設定なら 400（起動しない）。"""
    r = client.post("/valuation/backfill-net-cash")
    assert r.status_code == 400
    assert "未設定" in r.json()["detail"]


def test_rejects_free_plan(client) -> None:
    """free プランは 400（pro が必要・ADR-083）。"""
    with get_engine().begin() as conn:
        repo.upsert_edinetdb_config(conn, {"api_key": "edb_test", "plan": "free"})
    r = client.post("/valuation/backfill-net-cash")
    assert r.status_code == 400
    assert "pro" in r.json()["detail"]


def test_starts_on_pro(client, monkeypatch) -> None:
    """pro なら 202 で run_jobs が full_backfill=True・2 ジョブで起動される。"""
    with get_engine().begin() as conn:
        repo.upsert_edinetdb_config(conn, {"api_key": "edb_test", "plan": "pro"})

    captured: dict[str, Any] = {}

    def fake_run_jobs(
        jobs: list[Any], *, label: str = "バッチ", full_backfill: bool = False
    ) -> list:
        captured["label"] = label
        captured["full_backfill"] = full_backfill
        captured["n_jobs"] = len(jobs)
        return []

    monkeypatch.setattr("app.routers.batch.run_jobs", fake_run_jobs)

    r = client.post("/valuation/backfill-net-cash")
    assert r.status_code == 202
    assert r.json()["started"] is True
    # background task が走って run_jobs が全銘柄バックフィルとして呼ばれた
    assert captured.get("full_backfill") is True
    assert captured.get("n_jobs") == 2
