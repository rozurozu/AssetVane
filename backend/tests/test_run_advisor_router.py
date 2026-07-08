"""POST /batch/run-advisor（夜AI 単体起動口）の担保（ADR-092）。

nightly 面が設定済みなら 202 で run_jobs を label="夜AI"・1 ジョブで起動、未設定なら 400 で拒否
（/settings 誘導・ADR-018）。background task の実体（run_jobs）は monkeypatch でスタブ化しネットに
出さない（testing-strategy）。client フィクスチャは全 FACES を seed するので既定で nightly は
設定済み（202 経路）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import update

from app.db.engine import get_engine
from app.db.schema import llm_face_config


def test_starts_when_nightly_configured(client, monkeypatch) -> None:
    """nightly 面が設定済みなら 202 で run_jobs が label='夜AI'・1 ジョブ・非フルで起動される。"""
    captured: dict[str, Any] = {}

    def fake_run_jobs(
        jobs: list[Any], *, label: str = "バッチ", full_backfill: bool = False
    ) -> list:
        captured["label"] = label
        captured["n_jobs"] = len(jobs)
        captured["full_backfill"] = full_backfill
        return []

    monkeypatch.setattr("app.routers.batch.run_jobs", fake_run_jobs)

    r = client.post("/batch/run-advisor")
    assert r.status_code == 202
    assert r.json()["started"] is True
    # background task が走って run_jobs が夜AI 1 ジョブとして呼ばれた（fetch/score は混ぜない）。
    assert captured.get("label") == "夜AI"
    assert captured.get("n_jobs") == 1
    assert captured.get("full_backfill") is False


def test_rejects_when_nightly_unconfigured(client, monkeypatch) -> None:
    """nightly 面が未設定なら 400（起動しない・/settings 誘導・ADR-018）。"""
    # seed 済みの nightly 面の provider を外す（resolve_face が FaceNotConfiguredError を投げる）。
    with get_engine().begin() as conn:
        conn.execute(
            update(llm_face_config)
            .where(llm_face_config.c.face == "nightly")
            .values(provider_id=None)
        )

    called = {"v": False}

    def fake_run_jobs(*a: Any, **k: Any) -> list:
        called["v"] = True
        return []

    monkeypatch.setattr("app.routers.batch.run_jobs", fake_run_jobs)

    r = client.post("/batch/run-advisor")
    assert r.status_code == 400
    assert "nightly" in r.json()["detail"]
    assert called["v"] is False  # 400 で先に弾くので background task は投入されない
