"""日足取得ジョブのスタブテスト（spec §3.3/§3.4・§8）。

実 API は叩かない。adapter.fetch_daily_quotes_by_date をスタブ化し、空配列日（非営業日）と
データ日を混在させて「空日スキップ・fetch_meta 前進・UPSERT 行数」を検証する。
date.today() はフェイクで固定し、営業日ループ範囲を決定的にする。
"""

from __future__ import annotations

from datetime import date

import pytest

from app.adapters.jquants import JQuantsCoverageError
from app.batch import state
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
    # 2026-06-01(月) を最終取得済みとして仕込む → start は overlap 5 日を重ねて 05-27(水)
    # （ADR-093: 他 4 ジョブと同じ鮮度プローブ。取得済み日の再取得は UPSERT で冪等）。
    repo.upsert_fetch_meta("daily_quotes", "2026-06-01")

    # 営業日: 05-27(水) 05-28(木) 05-29(金) 06-01(月) 06-02(火) 06-03(水) 06-04(木) 06-05(金)。
    # 05-27〜06-01・06-03 は空（祝日想定でスキップ）、他はデータあり。
    by_date = {
        "2026-06-02": [_quote("72030", "2026-06-02"), _quote("67580", "2026-06-02")],
        "2026-06-04": [_quote("72030", "2026-06-04")],
        "2026-06-05": [_quote("72030", "2026-06-05")],
    }
    fake = _FakeAdapter(by_date)
    monkeypatch.setattr(fetch_quotes, "build_jquants_adapter", lambda: fake)

    result = fetch_quotes.run(full_backfill=False)

    # overlap 込みで 05-27〜06-05 の平日 8 日を叩く。土日は candidate_days が除外。
    assert fake.calls == [
        "2026-05-27",
        "2026-05-28",
        "2026-05-29",
        "2026-06-01",
        "2026-06-02",
        "2026-06-03",
        "2026-06-04",
        "2026-06-05",
    ]
    assert result.ok is True
    # UPSERT 行数: 2 + 1 + 1 = 4（空日は 0）。
    assert result.rows == 4

    # fetch_meta は空日も含め最終営業日まで前進している（06-05 はデータありなので前進する）。
    with get_engine().connect() as conn:
        meta = repo.get_fetch_meta(conn, "daily_quotes")
        assert meta is not None
        assert meta["last_fetched_date"] == "2026-06-05"
        # daily_quotes に 4 行入っている。
        assert repo.get_max_quote_date(conn) == "2026-06-05"


def test_empty_today_does_not_advance_cursor(temp_db, _patch, monkeypatch) -> None:
    """**当日**の空レスではカーソルを進めない（ADR-093 の芯・ロックイン防止の回帰テスト）。

    J-Quants はまだデータの無い日を 400 でなく 200 + {"data": []} で返す。夜間バッチは 02:00 に
    走るので当日の日足は必ず未掲載＝空。旧実装はこれを「祝日」とみなして fetch_meta を today まで
    前進させ、翌晩の start が today に張り付いて前日以前を永久に取り逃した（2026-07-02〜07-13 の
    日足欠損）。当日が空なら fetch_meta は**前日のまま**据え置き、翌晩に取り直せること。
    """
    repo.upsert_fetch_meta("daily_quotes", "2026-06-04")  # start=05-30(土)→候補は 06-01 から

    # today(06-05・金) だけ空＝まだ未掲載。それ以前はデータあり。
    by_date = {
        "2026-06-01": [_quote("72030", "2026-06-01")],
        "2026-06-02": [_quote("72030", "2026-06-02")],
        "2026-06-03": [_quote("72030", "2026-06-03")],
        "2026-06-04": [_quote("72030", "2026-06-04")],
    }
    fake = _FakeAdapter(by_date)
    monkeypatch.setattr(fetch_quotes, "build_jquants_adapter", lambda: fake)

    result = fetch_quotes.run(full_backfill=False)

    # 当日も「取りには行く」（掲載されていれば取れる）。取れなかっただけ。
    assert fake.calls[-1] == "2026-06-05"
    assert result.ok is True
    assert result.rows == 4

    with get_engine().connect() as conn:
        meta = repo.get_fetch_meta(conn, "daily_quotes")
        assert meta is not None
        # ★ today(06-05) には進まない。進めると翌晩 start=06-05 で 06-04 以前を取り逃す。
        assert meta["last_fetched_date"] == "2026-06-04"


def test_cursor_ahead_of_data_is_pulled_back(temp_db, _patch, monkeypatch) -> None:
    """カーソルが実データより先へ飛んでいたら実データの翌日まで引き戻す（ADR-093 自己修復）。

    ロックイン済みの実機状態（fetch_meta=today なのに daily_quotes は 05-26 止まり＝乖離が overlap
    の 5 日より大きい）を再現する。overlap だけでは乖離に追いつけず穴が永久に残るため、
    max(daily_quotes.date)+1 を start の上限にして穴の先頭から取り直す＝手で fetch_meta を巻き戻さ
    なくても次のバッチで埋まること。
    """
    # 実データは 05-26(火) まで。カーソルだけ today(06-05) に張り付いている＝ロックイン後の状態。
    repo.upsert_daily_quotes([_quote("72030", "2026-05-26")])
    repo.upsert_fetch_meta("daily_quotes", "2026-06-05")

    by_date = {
        d: [_quote("72030", d)]
        for d in (
            "2026-05-27",
            "2026-05-28",
            "2026-05-29",
            "2026-06-01",
            "2026-06-02",
            "2026-06-03",
            "2026-06-04",
        )
        # 06-05(today) は未掲載＝空。
    }
    fake = _FakeAdapter(by_date)
    monkeypatch.setattr(fetch_quotes, "build_jquants_adapter", lambda: fake)

    result = fetch_quotes.run(full_backfill=False)

    # ★ start は実データの翌日 05-27 まで引き戻る（overlap 由来の 05-31 だと 05-27〜05-29 が
    #   永久に穴のまま残る）。
    assert fake.calls[0] == "2026-05-27"
    assert result.ok is True
    assert result.rows == 7  # 05-27〜06-04 の平日 7 日ぶん＝穴が埋まる

    with get_engine().connect() as conn:
        assert repo.get_max_quote_date(conn) == "2026-06-04"
        meta = repo.get_fetch_meta(conn, "daily_quotes")
        assert meta is not None
        assert meta["last_fetched_date"] == "2026-06-04"  # 当日は空なので据え置き


def test_failure_returns_not_ok(temp_db, _patch, monkeypatch) -> None:
    repo.upsert_fetch_meta("daily_quotes", "2026-06-03")

    class _Boom:
        def fetch_daily_quotes_by_date(self, d: str) -> list[dict]:
            raise RuntimeError("boom")

    monkeypatch.setattr(fetch_quotes, "build_jquants_adapter", lambda: _Boom())
    result = fetch_quotes.run(full_backfill=False)
    assert result.ok is False
    assert "boom" in result.detail


def test_coverage_frontier_stops_cleanly(temp_db, _patch, monkeypatch) -> None:
    """契約範囲外の日付（400=JQuantsCoverageError）に達したら ok=True で打ち切る（前線到達）。

    本番投入の実走（2026-06-04）で、Free の提供範囲外日が空レスでなく 400 を返し、毎晩の差分が
    失敗扱いになった回帰防止。前線の日は fetch_meta に進めない（翌晩 d から再試行できるように）。
    """
    repo.upsert_fetch_meta("daily_quotes", "2026-06-01")  # start=2026-05-27（overlap 5 日）

    cov_msg = "Your subscription covers the following dates: 2024-03-12 ~ 2026-06-03 ..."

    class _CoverageAdapter:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def fetch_daily_quotes_by_date(self, d: str) -> list[dict]:
            self.calls.append(d)
            # 06-02・06-03 は取得でき、06-04 以降は範囲外（400）。
            if d >= "2026-06-04":
                raise JQuantsCoverageError(f"GET /v2/equities/bars/daily 契約範囲外: {cov_msg}")
            return [_quote("72030", d)]

    fake = _CoverageAdapter()
    monkeypatch.setattr(fetch_quotes, "build_jquants_adapter", lambda: fake)

    result = fetch_quotes.run(full_backfill=False)

    assert result.ok is True  # 前線到達は失敗ではない
    # overlap 込みで 05-27 から叩き、06-04 で 400 → break。
    assert fake.calls == [
        "2026-05-27",
        "2026-05-28",
        "2026-05-29",
        "2026-06-01",
        "2026-06-02",
        "2026-06-03",
        "2026-06-04",
    ]
    assert result.rows == 6  # 05-27〜06-03 の平日 6 日ぶん（各 1 行）
    with get_engine().connect() as conn:
        meta = repo.get_fetch_meta(conn, "daily_quotes")
        assert meta is not None
        # 前線の 06-04 には進めず、取得できた最終日 06-03 のまま（翌晩 06-04 から再試行）。
        assert meta["last_fetched_date"] == "2026-06-03"


def test_full_backfill_start_uses_backfill_years(temp_db, _patch, monkeypatch) -> None:
    # full_backfill は fetch_meta を無視し today - backfill_years から開始する。
    from app.config import settings

    monkeypatch.setattr(settings, "backfill_years", 2)
    # fetch_meta を仕込んでも full_backfill では使われない。
    repo.upsert_fetch_meta("daily_quotes", "2026-06-04")

    fake = _FakeAdapter({})  # 全日空（非営業日扱い）でも start 範囲が広いことを確認
    monkeypatch.setattr(fetch_quotes, "build_jquants_adapter", lambda: fake)

    result = fetch_quotes.run(full_backfill=True)
    # 最初の呼び出し日が 2024-06-05（today=2026-06-05 の 2 年前・平日）であること。
    assert fake.calls[0] == "2024-06-05"
    assert result.ok is True


def test_stop_mid_dayloop_breaks(temp_db, _patch, monkeypatch) -> None:
    """営業日境界で should_stop を見て中断し、残り営業日を取得しない（ADR-036 追補）。

    カーソルを 2026-06-02 に置き start=2026-05-28（木・overlap 5 日）にする。1 営業日処理中に
    停止要求 → 翌営業日（05-29 金）のループ先頭で break。取れた日まで fetch_meta 前進・detail に
    中断表示。
    """
    repo.upsert_fetch_meta("daily_quotes", "2026-06-02")  # start=2026-05-28（overlap 5 日）

    class _StoppingAdapter(_FakeAdapter):
        def fetch_daily_quotes_by_date(self, d):  # type: ignore[override]
            rows = super().fetch_daily_quotes_by_date(d)
            state.request_stop()  # 1 営業日処理中に停止要求が来た状況を模す
            return rows

    fake = _StoppingAdapter({"2026-05-28": [_quote("7203", "2026-05-28")]})
    monkeypatch.setattr(fetch_quotes, "build_jquants_adapter", lambda: fake)

    state.begin(full_backfill=False)  # request_stop は running 中のみ受理されるため
    try:
        result = fetch_quotes.run(full_backfill=False)
    finally:
        state.end()

    assert fake.calls == ["2026-05-28"]  # 05-29 の営業日先頭で break
    assert result.ok is True
    assert "停止により中断" in result.detail
    with get_engine().connect() as conn:
        meta = repo.get_fetch_meta(conn, "daily_quotes")
    assert meta is not None
    assert meta["last_fetched_date"] == "2026-05-28"  # 取れた日まで前進
