"""REST API（TestClient）。lifespan で alembic upgrade が走り、空 DB から動く。"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.advisor.tools.registry import CURRENT_PHASE
from app.config import settings
from app.db import repo
from app.db.engine import get_engine

STOCK = {
    "code": "72030",
    "company_name": "トヨタ自動車",
    "sector33_code": "3700",
    "sector17_code": "6",
    "market_code": "0111",
    "is_etf": 0,
    "updated_at": "2026-06-02T00:00:00+00:00",
}


def test_health(client) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
    # ADR-028: コストガード状態（画面バナーの判定材料）。空 DB なら未超過・既定 warn。
    cost = body["llm_cost"]
    assert set(cost) == {"mode", "limit_usd", "month_total_usd", "exceeded"}
    assert cost["mode"] == "warn"
    assert cost["month_total_usd"] == 0.0
    assert cost["exceeded"] is False


def test_health_phase_matches_current_phase(client) -> None:
    """/health の phase は Tool ゲートの単一の真実 CURRENT_PHASE と一致する。

    ハードコードに戻ってドリフトしないことの担保（tasks/review-2026-06-12.md C-9）。
    """
    assert client.get("/health").json()["phase"] == CURRENT_PHASE


def test_health_llm_cost_exceeded(client, monkeypatch: pytest.MonkeyPatch) -> None:
    """当月 llm_usage を上限以上積むと /health の llm_cost.exceeded=True（ADR-028）。"""
    monkeypatch.setattr(settings, "llm_cost_limit_usd", 1.0)
    month = datetime.now(UTC).strftime("%Y-%m")
    with get_engine().begin() as conn:
        repo.insert_llm_usage(
            conn,
            created_at=f"{month}-10T00:00:00+00:00",
            source="chat",
            model="test-model",
            cost_usd=2.5,
        )
    cost = client.get("/health").json()["llm_cost"]
    assert cost["month_total_usd"] == 2.5
    assert cost["exceeded"] is True


def test_stocks_empty_then_populated(client) -> None:
    assert client.get("/stocks").json() == []  # 最初は空
    repo.upsert_stocks([STOCK])
    repo.upsert_daily_quotes(
        [
            {
                "code": "72030",
                "date": "2026-03-10",
                "open": 1.0,
                "high": 2.0,
                "low": 0.5,
                "close": 1.5,
                "volume": 100.0,
                "adj_close": 1.5,
            }
        ]
    )
    stocks = client.get("/stocks").json()
    assert len(stocks) == 1 and stocks[0]["company_name"] == "トヨタ自動車"

    quotes = client.get("/quotes/72030").json()
    assert len(quotes) == 1 and quotes[0]["close"] == 1.5


def test_stock_detail_404(client) -> None:
    assert client.get("/stocks/99999").status_code == 404


# --- GET /signals（Phase 1・spec §5.1） ---


def _seed_signals() -> None:
    """company_name JOIN・date 解決・score 降順を確かめるための種データを入れる。"""
    repo.upsert_stocks([STOCK])
    repo.upsert_signals(
        [
            # 古い日付（最新解決で除外されることの確認用）。
            {
                "date": "2025-12-10",
                "code": "72030",
                "signal_type": "momentum",
                "score": 0.9,
                "payload": json.dumps({"label": "古い日", "schema_version": 1}),
            },
            # 最新日・低スコア。
            {
                "date": "2025-12-15",
                "code": "72030",
                "signal_type": "momentum",
                "score": 0.4,
                "payload": json.dumps(
                    {"label": "GC", "change_5d": 0.03, "rsi14": 41.2, "schema_version": 1}
                ),
            },
            # 最新日・高スコア・別 type（score 降順で先頭に来る）。
            {
                "date": "2025-12-15",
                "code": "72030",
                "signal_type": "volume_spike",
                "score": 0.8,
                "payload": json.dumps({"label": "出来高急増", "ratio": 4.2, "schema_version": 1}),
            },
        ]
    )


def test_signals_latest_date_and_score_order(client) -> None:
    _seed_signals()
    body = client.get("/signals").json()

    # date 省略時は最新算出日（2025-12-15）を採用し、古い 2025-12-10 は混ざらない。
    assert body["date"] == "2025-12-15"
    assert body["is_delayed"] is True  # 今日からは 7 日以上前なので遅延扱い
    assert [s["score"] for s in body["signals"]] == [0.8, 0.4]  # score 降順
    # company_name は signals JOIN stocks で補完される。
    assert body["signals"][0]["company_name"] == "トヨタ自動車"
    # payload は JSON 文字列から dict に展開され、type 固有指標も素通しされる。
    assert body["signals"][0]["payload"]["label"] == "出来高急増"
    assert body["signals"][0]["payload"]["ratio"] == 4.2


def test_signals_filter_by_type(client) -> None:
    _seed_signals()
    body = client.get("/signals?type=momentum").json()
    assert len(body["signals"]) == 1
    assert body["signals"][0]["signal_type"] == "momentum"
    assert body["signals"][0]["payload"]["change_5d"] == 0.03


def test_signals_empty_defaults_to_today(client) -> None:
    import datetime

    body = client.get("/signals").json()
    assert body["signals"] == []
    assert body["date"] == datetime.date.today().isoformat()
    assert body["is_delayed"] is False


# --- POST /batch/run（Phase 1・spec §3.8） ---


def test_batch_run_accepted_202(client, monkeypatch) -> None:
    # run_nightly を即 return のスタブに差し替え、実バッチを走らせず受付だけ検証する。
    called: list[bool] = []

    def _stub_run_nightly(*, full_backfill: bool = False):
        called.append(full_backfill)
        return []

    monkeypatch.setattr("app.routers.batch.run_nightly", _stub_run_nightly)

    resp = client.post("/batch/run", json={"full_backfill": True})
    assert resp.status_code == 202
    body = resp.json()
    assert body["started"] is True
    # BackgroundTasks はレスポンス後に走る。TestClient のコンテキスト内で実行済みのはず。
    assert called == [True]


def test_batch_run_conflict_409(client, monkeypatch) -> None:
    # ロック取得が常に失敗する状況を作り、409 が返ることを検証する。
    from app.batch import lock

    def _raise(*_args, **_kwargs):
        raise lock.BatchAlreadyRunning("テスト用の競合")

    monkeypatch.setattr("app.routers.batch.lock.acquire", _raise)

    resp = client.post("/batch/run", json={})
    assert resp.status_code == 409


def test_batch_run_swallows_background_start_conflict(client, monkeypatch) -> None:
    """受付通過後〜BackgroundTask 起動の競合は握り 500 にしない（_guard_concurrent_start）。

    受付の「取得即解放」チェックと BackgroundTask 実走の間に別バッチ（cron 等）が割り込むと
    run_nightly が BatchAlreadyRunning を送出するが、ガードが握るので受付の 202 が保たれ未捕捉
    例外にならない（握り損ねると TestClient が BackgroundTask 例外を再送出しこのテストが落ちる）。
    """
    from app.batch import lock

    def _raise_already(*, full_backfill: bool = False):
        raise lock.BatchAlreadyRunning("テスト用の起動競合")

    monkeypatch.setattr("app.routers.batch.run_nightly", _raise_already)

    resp = client.post("/batch/run", json={})
    assert resp.status_code == 202
    assert resp.json()["started"] is True
