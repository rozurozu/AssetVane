"""Phase 4 夜間巡回ジョブ（investigate_dossier）を検証する（phase4-spec.md §6・§8）。

担保すること（spec §6・U-8/L-22 裁定）:
- 古い順選出: `last_investigated_at` が古い/未調査（None）の銘柄が先に巡回される。
- stale フィルタ: 21 日以内に調査済みの銘柄は巡回しない（未調査・21 日超のみ対象）。
- N=3 制限: stale が 4 件以上でも先頭 3 件までしか巡回しない。
- 部分失敗の握り: 1 銘柄が例外でも他は巡回され、失敗があれば JobResult.ok=False。

investigate_stock（async パイプライン）は必ずモック（LLM/fetch_news/ネットに出ない＝
testing-strategy）。DB は一時 SQLite。watchlist は repo 経由で実テーブルに積む。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.batch.jobs import investigate_dossier
from app.db import repo
from app.db.engine import get_engine


def _stock(code: str) -> dict[str, Any]:
    return {
        "code": code,
        "company_name": f"会社{code}",
        "sector33_code": "3700",
        "sector17_code": "6",
        "market_code": "0111",
        "is_etf": 0,
        "updated_at": "2026-06-05T00:00:00+00:00",
    }


def _days_ago_iso(days: int) -> str:
    """現在から days 日前の ISO8601（UTC）。stale 境界の作り込みに使う。"""
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


def _seed_watchlist(code: str, last_investigated_at: str | None) -> None:
    """stocks → watchlist →（任意で）stock_dossiers を積む。

    last_investigated_at が None なら dossier を作らず未調査（list_watchlist で None になる）。
    """
    repo.upsert_stocks([_stock(code)])
    repo.add_watchlist(code)
    if last_investigated_at is not None:
        with get_engine().begin() as conn:
            repo.upsert_dossier(
                conn,
                code=code,
                summary_md="既存の要約",
                key_facts="{}",
                last_investigated_at=last_investigated_at,
                updated_at=last_investigated_at,
            )


def _stub_investigate(
    monkeypatch: pytest.MonkeyPatch,
    *,
    calls: list[str],
    fail_codes: set[str] | None = None,
) -> None:
    """investigate_stock を、巡回された code を記録するスタブに差し替える。

    fail_codes に含まれる code は例外を投げ、部分失敗の握りを検証できるようにする。
    ジョブが import した参照（investigate_dossier.investigate_stock）を差し替える。
    """
    fail = fail_codes or set()

    async def _fake(conn, code, *, mode):  # noqa: ANN001, ANN202
        calls.append(code)
        assert mode == "nightly"  # 夜間巡回は必ず nightly 経路（MCP 非依存・ADR-020）
        if code in fail:
            raise RuntimeError(f"わざと失敗: {code}")
        return {"code": code, "n_sources_added": 0}

    monkeypatch.setattr(investigate_dossier, "investigate_stock", _fake)


def test_oldest_first_and_unvisited_priority(temp_db, monkeypatch) -> None:
    """未調査（None）が最優先・調査済みは古い順に並ぶ（spec §6）。"""
    # 全件 stale（22/30 日前 or 未調査）にして並び順だけを検証する。
    _seed_watchlist("11110", _days_ago_iso(30))  # 最古
    _seed_watchlist("22220", None)  # 未調査=最優先
    _seed_watchlist("33330", _days_ago_iso(25))

    calls: list[str] = []
    _stub_investigate(monkeypatch, calls=calls)

    result = investigate_dossier.run()

    assert result.ok is True
    assert result.rows == 3
    # 未調査(22220) → 30日前(11110) → 25日前(33330) の順
    assert calls == ["22220", "11110", "33330"]


def test_stale_filter_skips_recent(temp_db, monkeypatch) -> None:
    """21 日以内に調査済みの銘柄は巡回対象から外れる（spec §6・L-22）。"""
    _seed_watchlist("11110", _days_ago_iso(5))  # 最近（巡回しない）
    _seed_watchlist("22220", _days_ago_iso(21))  # ちょうど 21 日（>21 ではない＝stale ではない）
    _seed_watchlist("33330", _days_ago_iso(22))  # 21 日超（stale）
    _seed_watchlist("44440", None)  # 未調査（stale）

    calls: list[str] = []
    _stub_investigate(monkeypatch, calls=calls)

    result = investigate_dossier.run()

    assert result.ok is True
    assert set(calls) == {"33330", "44440"}  # 最近の 2 件は除外
    assert result.rows == 2


def test_n_limit_three(temp_db, monkeypatch) -> None:
    """stale が 4 件以上でも先頭 N=3 件しか巡回しない（spec §6・U-8）。"""
    _seed_watchlist("11110", _days_ago_iso(40))
    _seed_watchlist("22220", _days_ago_iso(35))
    _seed_watchlist("33330", _days_ago_iso(30))
    _seed_watchlist("44440", _days_ago_iso(25))  # これは N=3 で溢れる（最も新しい stale）

    calls: list[str] = []
    _stub_investigate(monkeypatch, calls=calls)

    result = investigate_dossier.run()

    assert result.rows == 3
    assert len(calls) == investigate_dossier.DOSSIER_NIGHTLY_COUNT == 3
    # 古い順 3 件＝40/35/30 日前。25 日前(44440)は溢れる。
    assert calls == ["11110", "22220", "33330"]
    assert "44440" not in calls


def test_partial_failure_does_not_stop_others(temp_db, monkeypatch) -> None:
    """1 銘柄が例外でも他は巡回され、失敗があれば JobResult.ok=False（ADR-018）。"""
    _seed_watchlist("11110", _days_ago_iso(40))
    _seed_watchlist("22220", _days_ago_iso(35))
    _seed_watchlist("33330", _days_ago_iso(30))

    calls: list[str] = []
    _stub_investigate(monkeypatch, calls=calls, fail_codes={"22220"})

    result = investigate_dossier.run()

    # 失敗銘柄も含め 3 件すべて試行される（後続を止めない）。
    assert calls == ["11110", "22220", "33330"]
    assert result.ok is False  # 失敗が 1 件でもあれば ok=False
    assert result.rows == 2  # 成功は 2 件
    assert "22220" in result.detail


def test_no_targets_returns_ok(temp_db, monkeypatch) -> None:
    """巡回対象が無い（stale 無し）場合は ok=True・rows=0（後続を止めない）。"""
    _seed_watchlist("11110", _days_ago_iso(3))  # 最近のみ＝stale 無し

    calls: list[str] = []
    _stub_investigate(monkeypatch, calls=calls)

    result = investigate_dossier.run()

    assert result.ok is True
    assert result.rows == 0
    assert calls == []
