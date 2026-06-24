"""sync_master ジョブの単体テスト（spec §3.6・裁定 L-5・ネット非依存）。

担保（review-2026-06-12 §3 のテスト穴埋め）:
- fetch_master_all に結果があれば stocks を UPSERT（主経路）・code 無し行は弾く。
- fetch_master_all が空なら daily_quotes 起点の不足 code を fetch_master で後追い補完
  （フォールバック）。
- 補完対象 code が無ければ ok=True・rows=0（同期不要）。
- 例外（JQuantsError 等）は握って ok=False の JobResult を返す（runner が Discord 通知）。
build_jquants_adapter を patch し実 HTTP に出ない。
temp_db で本物 DB に触れない（testing-strategy）。
"""

from __future__ import annotations

from unittest.mock import patch

from app.adapters.jquants import JQuantsError
from app.batch.jobs import sync_master
from app.db import repo
from app.db.engine import get_engine


def _master_row(code: str) -> dict[str, object]:
    """upsert_stocks が受ける最小の銘柄マスタ行（is_etf/updated_at 込み）。"""
    return {"code": code, "company_name": f"会社{code}", "is_etf": 0, "updated_at": "t"}


def test_run_upserts_via_fetch_master_all(temp_db) -> None:
    """fetch_master_all に結果があれば stocks を UPSERT し ok=True（主経路）。"""
    with patch("app.batch.jobs.sync_master.build_jquants_adapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.fetch_master_all.return_value = [_master_row("72030"), _master_row("67580")]

        result = sync_master.run()

    assert result.ok is True
    assert result.rows == 2
    assert "fetch_master_all" in result.detail
    instance.fetch_master.assert_not_called()  # 全件取得が成功したらフォールバックしない
    with get_engine().connect() as conn:
        assert {s["code"] for s in repo.list_stocks(conn)} == {"72030", "67580"}


def test_run_filters_rows_without_code(temp_db) -> None:
    """fetch_master_all が code 欠落行を混ぜても弾いて UPSERT する（PK NULL 防止）。"""
    with patch("app.batch.jobs.sync_master.build_jquants_adapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.fetch_master_all.return_value = [
            _master_row("72030"),
            {"company_name": "コード無し", "is_etf": 0, "updated_at": "t"},
        ]

        result = sync_master.run()

    assert result.ok is True
    assert result.rows == 1
    with get_engine().connect() as conn:
        assert {s["code"] for s in repo.list_stocks(conn)} == {"72030"}


def test_run_fallback_completes_missing_codes(temp_db) -> None:
    """fetch_master_all が空なら daily_quotes 起点の不足 code を後追い補完する。"""
    # daily_quotes に code を撒き、stocks には無い状態を作る（補完対象）。
    repo.upsert_daily_quotes([{"code": "99990", "date": "2026-06-01", "close": 100.0}])

    with patch("app.batch.jobs.sync_master.build_jquants_adapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.fetch_master_all.return_value = []  # 全件取得は使えない
        instance.fetch_master.return_value = [_master_row("99990")]

        result = sync_master.run()

    assert result.ok is True
    assert result.rows == 1
    assert "後追い補完" in result.detail
    instance.fetch_master.assert_called_once_with(["99990"])  # 不足 code だけを少 req で取る
    with get_engine().connect() as conn:
        assert repo.get_stock(conn, "99990") is not None


def test_run_fallback_no_missing_is_noop(temp_db) -> None:
    """fetch_master_all が空かつ不足 code も無ければ ok=True・rows=0（同期不要）。"""
    # daily_quotes の code は全て stocks に既存 → 補完対象ゼロ。
    repo.upsert_stocks([_master_row("72030")])
    repo.upsert_daily_quotes([{"code": "72030", "date": "2026-06-01", "close": 100.0}])

    with patch("app.batch.jobs.sync_master.build_jquants_adapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.fetch_master_all.return_value = []

        result = sync_master.run()

    assert result.ok is True
    assert result.rows == 0
    assert "補完対象 code なし" in result.detail
    instance.fetch_master.assert_not_called()


def test_run_adapter_error_returns_failure(temp_db) -> None:
    """例外（JQuantsError 等）は握って ok=False の JobResult を返す（ADR-018）。"""
    with patch("app.batch.jobs.sync_master.build_jquants_adapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.fetch_master_all.side_effect = JQuantsError("接続失敗")

        result = sync_master.run()

    assert result.ok is False
    assert result.rows == 0
    assert "失敗" in result.detail
