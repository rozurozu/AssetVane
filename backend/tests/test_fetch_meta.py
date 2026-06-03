"""fetch_meta / signals の repo 関数（Phase 1・spec §3.2）。

差分取得の進捗管理（冪等・前進）と signals の冪等 UPSERT・JOIN 補完・並び順を検証する。
一時 SQLite（temp_db フィクスチャ）で回し、本物の DB には触れない。実 API も叩かない。
"""

from __future__ import annotations

import json

from sqlalchemy import func, select

from app.db import repo
from app.db.engine import get_engine
from app.db.schema import fetch_meta

STOCK_A = {
    "code": "72030",
    "company_name": "トヨタ自動車",
    "sector33_code": "3700",
    "sector17_code": "6",
    "market_code": "0111",
    "is_etf": 0,
    "updated_at": "2026-06-02T00:00:00+00:00",
}
STOCK_B = {
    "code": "67580",
    "company_name": "ソニーグループ",
    "sector33_code": "3650",
    "sector17_code": "5",
    "market_code": "0111",
    "is_etf": 0,
    "updated_at": "2026-06-02T00:00:00+00:00",
}


def _signal(date: str, code: str, signal_type: str, score: float, **payload: object) -> dict:
    """signals 行を作る。payload は呼び出し側が json.dumps 済みの契約（spec §3.2）。"""
    return {
        "date": date,
        "code": code,
        "signal_type": signal_type,
        "score": score,
        "payload": json.dumps({"schema_version": 1, **payload}),
    }


def test_fetch_meta_idempotent_and_advances(temp_db) -> None:
    """未存在なら None、upsert で作成、再 upsert で前進（行は増えない）。"""
    with get_engine().connect() as conn:
        assert repo.get_fetch_meta(conn, "daily_quotes") is None

    repo.upsert_fetch_meta("daily_quotes", "2026-03-10")
    with get_engine().connect() as conn:
        row = repo.get_fetch_meta(conn, "daily_quotes")
    assert row is not None
    assert row["last_fetched_date"] == "2026-03-10"
    assert row["updated_at"]  # UTC now が入っている

    # 前進（同じ source を上書き）。
    repo.upsert_fetch_meta("daily_quotes", "2026-03-11")
    with get_engine().connect() as conn:
        row = repo.get_fetch_meta(conn, "daily_quotes")
        # source ごと 1 行のまま（重複しない）。
        count = conn.execute(select(func.count()).select_from(fetch_meta)).scalar()
    assert row is not None
    assert row["last_fetched_date"] == "2026-03-11"
    assert count == 1


def test_get_max_quote_date(temp_db) -> None:
    """daily_quotes が空なら None、入っていれば MAX(date)（自己修復フォールバック）。"""
    with get_engine().connect() as conn:
        assert repo.get_max_quote_date(conn) is None

    repo.upsert_stocks([STOCK_A])
    repo.upsert_daily_quotes(
        [
            {
                "code": "72030",
                "date": d,
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "volume": 1.0,
                "adj_close": 1.0,
            }
            for d in ("2026-03-10", "2026-03-12", "2026-03-11")
        ]
    )
    with get_engine().connect() as conn:
        assert repo.get_max_quote_date(conn) == "2026-03-12"


def test_list_stock_codes(temp_db) -> None:
    repo.upsert_stocks([STOCK_B, STOCK_A])
    with get_engine().connect() as conn:
        codes = repo.list_stock_codes(conn)
    assert codes == ["67580", "72030"]  # code 昇順


def test_upsert_signals_idempotent(temp_db) -> None:
    """同 (date, code, signal_type) を再投入しても重複せず、値は上書き（冪等 UPSERT）。"""
    repo.upsert_signals([_signal("2026-03-10", "72030", "momentum", 0.5, rsi14=41.2)])
    # 同キー・score 違いで再投入。
    repo.upsert_signals([_signal("2026-03-10", "72030", "momentum", 0.8, rsi14=55.0)])

    with get_engine().connect() as conn:
        rows = repo.get_signals(conn, date="2026-03-10", signal_type="momentum")
    assert len(rows) == 1  # 重複していない
    assert rows[0]["score"] == 0.8  # 上書きされている


def test_get_signals_join_and_order(temp_db) -> None:
    """company_name を JOIN 補完し、date 降順 → score 降順で返す。payload は生 TEXT のまま。"""
    repo.upsert_stocks([STOCK_A, STOCK_B])
    repo.upsert_signals(
        [
            _signal("2026-03-10", "72030", "momentum", 0.4),
            _signal("2026-03-11", "72030", "momentum", 0.6, label="GC"),
            _signal("2026-03-11", "67580", "momentum", 0.9),
        ]
    )

    with get_engine().connect() as conn:
        # date 省略 → 最新算出日（2026-03-11）を自動採用。
        rows = repo.get_signals(conn, date=None, signal_type="momentum")

    # 最新日のみ・score 降順。
    assert [r["date"] for r in rows] == ["2026-03-11", "2026-03-11"]
    assert [r["score"] for r in rows] == [0.9, 0.6]
    assert [r["code"] for r in rows] == ["67580", "72030"]
    # company_name は JOIN で補完される。
    assert rows[0]["company_name"] == "ソニーグループ"
    assert rows[1]["company_name"] == "トヨタ自動車"
    # payload は生 TEXT のまま（json.loads はルータの責務）。
    assert isinstance(rows[1]["payload"], str)
    assert json.loads(rows[1]["payload"])["label"] == "GC"


def test_get_signals_company_name_none_when_no_stock(temp_db) -> None:
    """stocks に無い code（業種コード等）は LEFT JOIN で company_name=None。"""
    repo.upsert_signals([_signal("2026-03-11", "0050", "lead_lag", 0.7)])
    with get_engine().connect() as conn:
        rows = repo.get_signals(conn, date="2026-03-11", signal_type=None)
    assert len(rows) == 1
    assert rows[0]["company_name"] is None


def test_get_signals_limit_and_type_filter(temp_db) -> None:
    repo.upsert_stocks([STOCK_A])
    repo.upsert_signals(
        [
            _signal("2026-03-11", "72030", "momentum", 0.9),
            _signal("2026-03-11", "72030", "volume_spike", 0.5),
        ]
    )
    with get_engine().connect() as conn:
        only_mom = repo.get_signals(conn, date="2026-03-11", signal_type="momentum")
        limited = repo.get_signals(conn, date="2026-03-11", signal_type=None, limit=1)
    assert len(only_mom) == 1
    assert only_mom[0]["signal_type"] == "momentum"
    assert len(limited) == 1  # limit が効く


def test_get_latest_signal_date(temp_db) -> None:
    """type 指定なし=全体の MAX、type 指定=その type の MAX。空なら None。"""
    with get_engine().connect() as conn:
        assert repo.get_latest_signal_date(conn) is None

    repo.upsert_signals(
        [
            _signal("2026-03-10", "72030", "momentum", 0.5),
            _signal("2026-03-12", "72030", "volume_spike", 0.5),
        ]
    )
    with get_engine().connect() as conn:
        assert repo.get_latest_signal_date(conn) == "2026-03-12"
        assert repo.get_latest_signal_date(conn, "momentum") == "2026-03-10"
        assert repo.get_latest_signal_date(conn, "volume_spike") == "2026-03-12"


def test_get_signals_empty_when_no_data(temp_db) -> None:
    """date 省略かつ signals が空なら空リスト（最新算出日が無い）。"""
    with get_engine().connect() as conn:
        assert repo.get_signals(conn, date=None, signal_type="momentum") == []
