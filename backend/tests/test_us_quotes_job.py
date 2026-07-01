"""fetch_us_quotes のバッチ分割 UPSERT と差分カーソル前進の検証（ADR-002/018/039）。

担保: fake fetch_quotes でシンボルをバッチ分割して UPSERT すること・全銘柄共通カーソル
fetch_meta['us_daily_quotes'] が取得最大 date まで前進すること・部分失敗で後続を止めず ok を
正しく決めること・full_backfill と差分の開始日分岐・差分開始日がカーソルに
_REFETCH_OVERLAP_DAYS 日重なること（鮮度プローブ・tasks/review-2026-06-12.md C-1/C-2・
test_fetch_index.py のミラー）。fake adapter で実 HTTP に出ない（testing-strategy）。
"""

from __future__ import annotations

from typing import Any

from app.batch import state
from app.batch.jobs import fetch_us_quotes
from app.config import settings
from app.db import repo
from app.db.engine import get_engine


class _FakeAdapter:
    """fetch_quotes_bulk だけ持つ fake（symbol→行 or 例外）。呼ばれたバッチと from_/to を記録。

    バルク契約（ADR-055）に合わせ、例外指定の symbol は raise せず dict から落とす（部分欠損＝応答に
    含まれない）。`calls` は 1 バッチ＝1 要素で `(symbols タプル, from_, to)` を記録する。
    """

    def __init__(self, by_symbol: dict[str, Any]) -> None:
        self._by_symbol = by_symbol
        self.calls: list[tuple[tuple[str, ...], str | None, str | None]] = []

    def fetch_quotes_bulk(
        self, symbols: list[str], from_: str | None = None, to: str | None = None
    ) -> dict[str, list[dict[str, Any]]]:
        self.calls.append((tuple(symbols), from_, to))
        out: dict[str, list[dict[str, Any]]] = {}
        for symbol in symbols:
            val = self._by_symbol[symbol]
            if isinstance(val, Exception):
                continue  # 単数版の raise を「dict から落とす＝部分欠損」へ翻訳（バルク契約）
            out[symbol] = val
        return out


def _q(symbol: str, date: str, close: float) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "date": date,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1.0,
        "adj_close": close,
    }


def _seed_universe(symbols: list[str]) -> None:
    repo.upsert_us_stocks(
        [{"symbol": s, "company_name": s, "is_etf": 0, "updated_at": "t"} for s in symbols]
    )


def test_batched_upsert_and_cursor_advances(temp_db, monkeypatch) -> None:
    """3 銘柄を batch_size=2 で分割 UPSERT し、カーソルが取得最大 date まで前進する。"""
    _seed_universe(["AAA", "BBB", "CCC"])
    monkeypatch.setattr(settings, "us_quotes_batch_size", 2)
    fake = _FakeAdapter(
        {
            "AAA": [_q("AAA", "2026-06-05", 10.0), _q("AAA", "2026-06-08", 11.0)],
            "BBB": [_q("BBB", "2026-06-08", 20.0)],
            "CCC": [_q("CCC", "2026-06-09", 30.0)],  # 最大 date
        }
    )
    result = fetch_us_quotes.run(adapter=fake)  # type: ignore[arg-type]
    assert result.ok is True
    assert result.rows == 4  # 2 + 1 + 1 行

    with get_engine().connect() as conn:
        aaa = repo.get_us_quotes(conn, "AAA")
        ccc = repo.get_us_quotes(conn, "CCC")
        meta = repo.get_fetch_meta(conn, "us_daily_quotes")
    assert [r["date"] for r in aaa] == ["2026-06-05", "2026-06-08"]  # date 昇順
    assert ccc[0]["close"] == 30.0
    # 全銘柄共通カーソルは取得最大 date（CCC の 2026-06-09）まで前進。
    assert meta["last_fetched_date"] == "2026-06-09"


def test_idempotent_reupsert(temp_db) -> None:
    """同じ行を 2 回流しても重複しない（(symbol,date) UPSERT 冪等・ADR-002）。"""
    _seed_universe(["AAA"])
    fake = _FakeAdapter({"AAA": [_q("AAA", "2026-06-08", 10.0)]})
    fetch_us_quotes.run(adapter=fake)  # type: ignore[arg-type]
    fetch_us_quotes.run(adapter=fake)  # type: ignore[arg-type]
    with get_engine().connect() as conn:
        rows = repo.get_us_quotes(conn, "AAA")
    assert len(rows) == 1


def test_partial_failure_not_total_is_ok(temp_db, monkeypatch) -> None:
    """1 銘柄失敗でも 1 本でも成功すれば ok=True（総崩れのみ失敗・fetch_index 同型）。"""
    _seed_universe(["AAA", "BAD"])
    monkeypatch.setattr(settings, "us_quotes_batch_size", 10)
    fake = _FakeAdapter({"AAA": [_q("AAA", "2026-06-08", 10.0)], "BAD": RuntimeError("boom")})
    result = fetch_us_quotes.run(adapter=fake)  # type: ignore[arg-type]
    assert result.ok is True
    assert result.rows == 1
    assert "失敗 1 件" in result.detail


def test_total_failure_is_not_ok(temp_db, monkeypatch) -> None:
    """試行した全シンボルが失敗（総崩れ）なら ok=False（runner が Discord 通知）。"""
    _seed_universe(["BAD1", "BAD2"])
    monkeypatch.setattr(settings, "us_quotes_batch_size", 10)
    fake = _FakeAdapter({"BAD1": RuntimeError("x"), "BAD2": RuntimeError("y")})
    result = fetch_us_quotes.run(adapter=fake)  # type: ignore[arg-type]
    assert result.ok is False


def test_write_failure_does_not_advance_cursor_past_unpersisted(temp_db, monkeypatch) -> None:
    """#4: バッチ書き込みが失敗した date でカーソルを進めない（未永続データを飛び越えない）。

    旧実装は UPSERT 前に max_date を積み、書き込み失敗後もその date までカーソルを前進させたため、
    未永続シンボルの履歴を差分 overlap の外へ恒久欠落させていた。成功バッチの date だけで進める。
    """
    _seed_universe(["AAA", "BBB"])
    monkeypatch.setattr(settings, "us_quotes_batch_size", 1)  # 1 バッチ=1 銘柄
    fake = _FakeAdapter(
        {
            "AAA": [_q("AAA", "2026-06-05", 10.0)],  # 成功・永続
            "BBB": [_q("BBB", "2026-06-09", 20.0)],  # より新しいが書き込みが失敗する
        }
    )
    real_upsert = repo.upsert_us_daily_quotes

    def _fail_on_bbb(conn: Any, rows: list[dict[str, Any]]) -> int:
        if any(r["symbol"] == "BBB" for r in rows):
            raise RuntimeError("write boom")
        return real_upsert(conn, rows)

    monkeypatch.setattr(repo, "upsert_us_daily_quotes", _fail_on_bbb)
    result = fetch_us_quotes.run(adapter=fake)  # type: ignore[arg-type]

    assert result.ok is True  # AAA は成功しているので総崩れではない
    with get_engine().connect() as conn:
        bbb = repo.get_us_quotes(conn, "BBB")
        meta = repo.get_fetch_meta(conn, "us_daily_quotes")
    assert bbb == []  # BBB は未永続
    assert meta["last_fetched_date"] == "2026-06-05"  # 永続した AAA まで。未永続 06-09 は飛ばさない
    assert "失敗 1 件" in result.detail  # BBB は失敗として計上


def test_write_failure_all_batches_is_not_ok(temp_db, monkeypatch) -> None:
    """#4: 全バッチの書き込みが失敗したら総崩れで ok=False（旧実装は ok=True で無通知だった）。"""
    _seed_universe(["AAA", "BBB"])
    monkeypatch.setattr(settings, "us_quotes_batch_size", 10)  # 1 バッチに 2 銘柄
    fake = _FakeAdapter(
        {"AAA": [_q("AAA", "2026-06-08", 10.0)], "BBB": [_q("BBB", "2026-06-08", 20.0)]}
    )

    def _always_fail(conn: Any, rows: list[dict[str, Any]]) -> int:
        raise RuntimeError("write boom")

    monkeypatch.setattr(repo, "upsert_us_daily_quotes", _always_fail)
    result = fetch_us_quotes.run(adapter=fake)  # type: ignore[arg-type]

    assert result.ok is False  # 書き込み全滅＝総崩れ（symbol 単位で failed を積む）
    with get_engine().connect() as conn:
        meta = repo.get_fetch_meta(conn, "us_daily_quotes")
    assert meta is None  # カーソルは前進しない（未永続）


def test_full_backfill_vs_incremental_start(temp_db, monkeypatch) -> None:
    """full_backfill は BACKFILL_YEARS 分頭から・差分はカーソルに重ねた地点から（開始日分岐）。"""
    _seed_universe(["AAA"])
    monkeypatch.setattr(settings, "backfill_years", 2)
    # まず差分用カーソルを置く。
    repo.upsert_fetch_meta("us_daily_quotes", "2026-06-08")

    fake = _FakeAdapter({"AAA": [_q("AAA", "2026-06-09", 10.0)]})
    fetch_us_quotes.run(adapter=fake, full_backfill=False)  # type: ignore[arg-type]
    # 差分: カーソル 2026-06-08 から _REFETCH_OVERLAP_DAYS 日重ねた 2026-06-03 から
    # （鮮度プローブ・C-1。週末でも直近営業日が窓に入る）。
    assert fake.calls[-1][1] == "2026-06-03"

    fake2 = _FakeAdapter({"AAA": [_q("AAA", "2026-06-09", 10.0)]})
    fetch_us_quotes.run(adapter=fake2, full_backfill=True)  # type: ignore[arg-type]
    # full_backfill: from_ は today-2 年（年部分が 2 年前）。月日は today 依存なので年だけ確認。
    from datetime import date

    expected_year = date.today().replace(year=date.today().year - 2).year
    assert fake2.calls[-1][1].startswith(str(expected_year))


def test_start_date_overlaps_last_fetched(temp_db) -> None:
    """meta ありのとき開始日は last_fetched より前（鮮度プローブで重ねる・C-1/C-2）。

    test_fetch_index.py の test_start_date_for_symbol_overlaps_last_fetched のミラー。
    重ね窓により 02:00 JST の途中足も翌晩に確定足で UPSERT 上書きされる（自己修復・C-2）。
    """
    from datetime import date, timedelta

    from app.batch.jobs._cursor import DEFAULT_OVERLAP_DAYS

    repo.upsert_fetch_meta("us_daily_quotes", "2026-06-05")
    start = fetch_us_quotes._start_date(full_backfill=False, today="2026-06-08")
    assert start == (date(2026, 6, 5) - timedelta(days=DEFAULT_OVERLAP_DAYS)).isoformat()
    assert start <= "2026-06-05"  # 最終取得日に重なる


def test_no_new_data_keeps_cursor_and_ok(temp_db) -> None:
    """重ね窓で最終取得日のバーだけ返る（新規データ無し）→ ok=True・カーソルは据え置き。

    test_fetch_index.py の test_fetch_index_run_no_new_data_is_ok のミラー（C-1）。
    アダプタは ≥1 行返している（0 行 raise の契約に整合）ので失敗ではない。
    """
    _seed_universe(["AAA"])
    repo.upsert_fetch_meta("us_daily_quotes", "2026-06-08")

    fake = _FakeAdapter({"AAA": [_q("AAA", "2026-06-08", 10.0)]})
    result = fetch_us_quotes.run(adapter=fake)  # type: ignore[arg-type]

    assert result.ok is True
    with get_engine().connect() as conn:
        meta = repo.get_fetch_meta(conn, "us_daily_quotes")
    assert meta["last_fetched_date"] == "2026-06-08"  # 新規無しなので前進しない（後退もしない）


def test_stop_mid_batch_breaks_loop(temp_db, monkeypatch) -> None:
    """バッチ境界で should_stop を見て中断し、残りシンボルを取得しない（ADR-036 追補）。

    batch_size=1（1 バッチ=1 銘柄）にし、1 件目取得中に停止要求 → 次バッチ先頭で break。
    取れた分はカーソル前進し冪等再開でき、detail に「停止により中断」が載る。
    """
    _seed_universe(["AAA", "BBB", "CCC"])
    monkeypatch.setattr(settings, "us_quotes_batch_size", 1)

    class _StoppingAdapter(_FakeAdapter):
        def fetch_quotes_bulk(self, symbols, from_=None, to=None):  # type: ignore[override]
            out = super().fetch_quotes_bulk(symbols, from_, to)
            state.request_stop()  # 1 バッチ目取得中に WebUI から停止が来た状況を模す
            return out

    fake = _StoppingAdapter(
        {
            "AAA": [_q("AAA", "2026-06-05", 10.0)],
            "BBB": [_q("BBB", "2026-06-05", 20.0)],
            "CCC": [_q("CCC", "2026-06-05", 30.0)],
        }
    )
    state.begin(full_backfill=False)  # request_stop は running 中のみ受理されるため
    try:
        result = fetch_us_quotes.run(adapter=fake)  # type: ignore[arg-type]
    finally:
        state.end()

    assert [c[0] for c in fake.calls] == [
        ("AAA",)
    ]  # BBB/CCC のバッチ先頭で break（1 バッチ=1 銘柄）
    assert result.ok is True
    assert "停止により中断" in result.detail
    with get_engine().connect() as conn:
        meta = repo.get_fetch_meta(conn, "us_daily_quotes")
    assert meta["last_fetched_date"] == "2026-06-05"  # 取れた分でカーソル前進
