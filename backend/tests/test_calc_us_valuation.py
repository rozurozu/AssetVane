"""calc_us_valuation ジョブの run() wrapper を担保する（ADR-031/048・ADR-018）。

build_us_valuation_snapshots（service）/ upsert_us_valuation_snapshots（repo）を monkeypatch し、
wrapper が「service の行を repo に渡し JobResult に rows/ok を映す」「例外を握って ok=False に畳む
（runner が Discord 通知・ADR-018）」を満たすことだけを検証する（service/repo 本体は別テスト）。
"""

from __future__ import annotations

from app.batch.jobs import calc_us_valuation


def test_calc_us_valuation_ok(temp_db, monkeypatch) -> None:
    """正常系: service が返した行数を repo へ渡し ok=True・rows を JobResult に映す。"""
    monkeypatch.setattr(
        calc_us_valuation.us_valsvc,
        "build_us_valuation_snapshots",
        lambda conn: [{"symbol": "AAPL"}, {"symbol": "MSFT"}],
    )
    monkeypatch.setattr(
        calc_us_valuation.repo,
        "upsert_us_valuation_snapshots",
        lambda rows: len(rows),
    )

    result = calc_us_valuation.run()

    assert result.name == "calc_us_valuation"
    assert result.ok is True
    assert result.rows == 2


def test_calc_us_valuation_failure_ok_false(temp_db, monkeypatch) -> None:
    """例外系: service が投げたらジョブ境界で握り ok=False・rows=0（runner が通知・ADR-018）。"""

    def _boom(conn):  # noqa: ANN001, ANN202
        raise RuntimeError("yfinance レート制限")

    monkeypatch.setattr(calc_us_valuation.us_valsvc, "build_us_valuation_snapshots", _boom)

    result = calc_us_valuation.run()

    assert result.ok is False
    assert result.rows == 0
    assert "失敗" in result.detail
