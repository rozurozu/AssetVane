"""日米業種リードラグの service／job／router／AI Tool を end-to-end で検証する（Phase 7）。

設計の真実: 論文 SIG-FIN-036-13・ADR-005/014/016。ネットには出ない（一時 SQLite に合成系列を
seed して build→upsert→GET /lead-lag が通ることを確認）。quant 純関数の数理は
test_quant_lead_lag 系（第1波）が担うので、ここは「DB ↔ service ↔ repo ↔ router/Tool」の
配線・桁マッピング・正規化・遅延判定・Phase ゲートに絞る。
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pytest

from app.db import repo
from app.db.engine import get_engine
from app.quant.lead_lag import JP_SYMBOLS, US_SYMBOLS
from app.services import lead_lag as svc

# 営業日 60 窓 + ベース + 当日を十分賄う長さ（quant は window+1 行必要）。
_N_DAYS = 90


def _dates(n: int) -> list[str]:
    """連番の擬似営業日 'YYYY-MM-DD'（暦の厳密さは問わない・順序だけ昇順で揃う）。"""
    out: list[str] = []
    y, m, d = 2025, 1, 1
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}-{d:02d}")
        d += 1
        if d > 28:
            d = 1
            m += 1
            if m > 12:
                m = 1
                y += 1
    return out


def _seed_market(
    n: int = _N_DAYS,
    *,
    jp_codes: list[str] | None = None,
    us_offset: int = 0,
    jp_offset: int = 0,
    total_days: int | None = None,
) -> list[str]:
    """US 業種 ETF（index_quotes）と JP 業種 ETF（daily_quotes）に相関のある合成系列を seed する。

    US の各業種にランダムウォークを与え、JP は「対応する US＋固有ノイズ」で生成して日米に
    リードラグ構造（共通因子）を持たせる。jp_codes 省略時は全 17 業種の 5桁 DB コードを使う。

    カレンダー差の再現: `us_offset`/`jp_offset` で各市場の系列が始まる暦日インデックスをずらせる
    （実データは US と JP で開始日・休場日が異なり inner join 先頭行に部分 NaN が出る）。各系列は
    その offset から n 日ぶん載せる。total_days はカレンダー全長（既定は十分に長く取る）。

    raw close と adj_close を**別の値**にする（adj_close = raw close × 累積調整係数）。これで
    open-to-close が raw を使っているか（adj_close を誤用していないか）をテストが区別できる。
    戻り値は US 系列の暦日リスト（参考）。
    """
    rng = np.random.default_rng(42)
    jp_codes = jp_codes or [f"{s}0" for s in JP_SYMBOLS]
    calendar = _dates(total_days if total_days is not None else (n + max(us_offset, jp_offset) + 5))

    us_dates = calendar[us_offset : us_offset + n]
    jp_dates = calendar[jp_offset : jp_offset + n]

    # US: 各 symbol の close（ランダムウォーク・正の水準）。
    us_rows: list[dict[str, Any]] = []
    us_returns: dict[str, np.ndarray] = {}
    for sym in US_SYMBOLS:
        ret = rng.normal(0.0, 0.01, size=n)
        us_returns[sym] = ret
        level = 100.0 * np.cumprod(1.0 + ret)
        for i, dt in enumerate(us_dates):
            us_rows.append({"symbol": sym, "date": dt, "close": float(level[i])})

    # JP: 17 業種。各 JP を US の i 番目（巡回）＋固有ノイズで作り共通因子を持たせる。
    # adj_close は raw close に累積調整係数を掛けて raw と別値にする（roc の raw 使用検証）。
    jp_rows: list[dict[str, Any]] = []
    for j, code in enumerate(jp_codes):
        base = us_returns[US_SYMBOLS[j % len(US_SYMBOLS)]]
        idio = rng.normal(0.0, 0.008, size=n)
        ret = 0.6 * base + idio
        raw_level = 100.0 * np.cumprod(1.0 + ret)
        adj_factor = np.cumprod(np.full(n, 1.001))  # adj_close ≠ raw close を保証する係数
        for i, dt in enumerate(jp_dates):
            close = float(raw_level[i])
            open_ = close * (1.0 - 0.5 * float(ret[i]))  # 同日 open<->close に動きを持たせる
            jp_rows.append(
                {
                    "code": code,
                    "date": dt,
                    "open": open_,
                    "high": max(open_, close),
                    "low": min(open_, close),
                    "close": close,
                    "volume": 1000.0,
                    "adj_close": close * float(adj_factor[i]),
                }
            )

    repo.upsert_index_quotes(us_rows)
    repo.upsert_daily_quotes(jp_rows)
    return us_dates


# ---------------------------------------------------------------------------
# repo の読み取り関数
# ---------------------------------------------------------------------------


def test_repo_readers_shape(temp_db: None) -> None:
    """get_index_closes_by_symbols / get_daily_ohlc_by_codes が symbol/code 別に昇順で返す。"""
    _seed_market(n=10, jp_codes=["16170", "16180"])
    with get_engine().connect() as conn:
        us = repo.get_index_closes_by_symbols(conn, ["XLB", "XLE"])
        jp = repo.get_daily_ohlc_by_codes(conn, ["16170", "16180"])
    assert set(us) == {"XLB", "XLE"}
    assert len(us["XLB"]) == 10
    assert us["XLB"][0]["date"] < us["XLB"][-1]["date"]  # 昇順
    assert set(jp) == {"16170", "16180"}
    # raw close も返す（roc の open-to-close が raw を使うため＝バグ2修正）。
    assert {"date", "open", "close", "adj_close"} <= set(jp["16170"][0])
    # 空入力は空 dict。
    with get_engine().connect() as conn:
        assert repo.get_index_closes_by_symbols(conn, []) == {}
        assert repo.get_daily_ohlc_by_codes(conn, []) == {}


# ---------------------------------------------------------------------------
# service: build_lead_lag_signals
# ---------------------------------------------------------------------------


def test_build_no_data_returns_empty(temp_db: None) -> None:
    """データ皆無なら rows=[]・meta.reason=no_data（例外で落とさない）。"""
    with get_engine().connect() as conn:
        rows, meta = svc.build_lead_lag_signals(conn)
    assert rows == []
    assert meta["reason"] == "no_data"


def test_build_produces_17_rows(temp_db: None) -> None:
    """十分な履歴で 17 業種ぶんの signals 行と検証メタを返す（桁/和名/score/notable を確認）。"""
    _seed_market()
    with get_engine().connect() as conn:
        rows, meta = svc.build_lead_lag_signals(conn)

    assert len(rows) == 17
    # signal_type・5桁 code・score 範囲・和名 label・最新 as_of。
    for r in rows:
        assert r["signal_type"] == "lead_lag"
        assert len(r["code"]) == 5 and r["code"].endswith("0")  # 5桁 DB コード
        assert 0.0 <= r["score"] <= 1.0
        assert r["date"] == meta["as_of"]
        payload = r["payload"]
        assert payload["label"]  # 業種和名が入る
        assert isinstance(payload["notable"], bool)
        assert payload["schema_version"] == 1
        assert payload["window"] == 60 and payload["k"] == 3 and payload["lambda"] == 0.9
    # 全業種が同じ as_of（最新行 t）。
    assert len({r["date"] for r in rows}) == 1
    # 検証指標がメタに入る（IC は -1..1 内）。
    assert -1.0 <= meta["ic"] <= 1.0
    assert 0.0 <= meta["hit_rate"] <= 1.0
    # code は 4桁ではなく 5桁にマップされている（"16170" 等）。
    db_codes = {r["code"] for r in rows}
    assert "16170" in db_codes and "16330" in db_codes


def test_build_handles_mismatched_calendars(temp_db: None) -> None:
    """実データ形（US と JP の開始日が異なり join 先頭行に部分 NaN）でも 17 行返す（バグ1回帰）。

    旧実装は「リターン化してから join」で rcc 先頭行に US 列 NaN が残り、quant の base 統計が
    NaN→None/empty に落ちて rows=0（insufficient_history）になっていた。レベルを共通営業日で
    揃えてから pct_change する修正で、clean panel になり非空 rows を返すことを担保する。
    """
    # US は暦日 0 起点、JP は 7 日ずらして開始（取引カレンダー差を再現）。共通区間は十分長く。
    _seed_market(n=_N_DAYS, us_offset=0, jp_offset=7, total_days=_N_DAYS + 20)
    with get_engine().connect() as conn:
        rows, meta = svc.build_lead_lag_signals(conn)
    assert meta.get("reason") != "insufficient_history"
    assert len(rows) == 17
    assert meta["as_of"] is not None


def test_build_score_normalized_cross_section(temp_db: None) -> None:
    """score は 17 業種横断で 0..1 に正規化される（最小 0・最大 1 がそれぞれ存在）。"""
    _seed_market()
    with get_engine().connect() as conn:
        rows, _ = svc.build_lead_lag_signals(conn)
    scores = sorted(r["score"] for r in rows)
    assert scores[0] == pytest.approx(0.0, abs=1e-9)
    assert scores[-1] == pytest.approx(1.0, abs=1e-9)


def test_open_to_close_uses_raw_close_not_adj(temp_db: None) -> None:
    """open-to-close は raw close から計算する（adj_close を誤用しない＝バグ2回帰）。"""
    rows = [
        {"date": "2025-01-01", "open": 100.0, "close": 110.0, "adj_close": 220.0},
    ]
    s = svc._series_open_to_close(rows)
    # raw: (110-100)/100 = 0.10。adj 誤用なら (220-100)/100 = 1.20 になる。
    assert s.iloc[0] == pytest.approx(0.10)
    # open=0 / None は NaN（割れない・補間しない）。
    bad = svc._series_open_to_close([{"date": "2025-01-02", "open": 0.0, "close": 5.0}])
    assert np.isnan(bad.iloc[0])


# ---------------------------------------------------------------------------
# batch job: calc_lead_lag
# ---------------------------------------------------------------------------


def test_job_upserts_signals(temp_db: None) -> None:
    """calc_lead_lag.run が build→upsert を通し signals に lead_lag が 17 行入る（冪等）。"""
    from app.batch.jobs import calc_lead_lag

    _seed_market()
    result = calc_lead_lag.run()
    assert result.ok is True
    assert result.rows == 17

    with get_engine().connect() as conn:
        date = repo.get_latest_signal_date(conn, "lead_lag")
        signals = repo.get_signals(conn, date, "lead_lag", limit=100)
    assert len(signals) == 17
    # payload は json 文字列で焼かれている（job が json.dumps した契約）。
    parsed = json.loads(signals[0]["payload"])
    assert parsed["schema_version"] == 1

    # 再実行しても重複しない（同日同 type・冪等 UPSERT）。
    calc_lead_lag.run()
    with get_engine().connect() as conn:
        again = repo.get_signals(conn, date, "lead_lag", limit=100)
    assert len(again) == 17


def test_job_degraded_is_not_failure(temp_db: None) -> None:
    """データ不足（縮退）は ok=True/rows=0（失敗にしない＝ADR-018）。"""
    from app.batch.jobs import calc_lead_lag

    result = calc_lead_lag.run()  # seed なし
    assert result.ok is True
    assert result.rows == 0


# ---------------------------------------------------------------------------
# REST: GET /lead-lag
# ---------------------------------------------------------------------------


def test_get_lead_lag_empty(client: Any) -> None:
    """台帳が空でも 200・ranking=[]・as_of=None・meta.plan を返す（widget が壊れない）。"""
    res = client.get("/lead-lag")
    assert res.status_code == 200
    body = res.json()
    assert body["as_of"] is None
    assert body["ranking"] == []
    assert "plan" in body["meta"]
    assert "is_delayed" in body["meta"]


def test_get_lead_lag_after_job(client: Any) -> None:
    """job 実行後は ranking が score 降順・meta に検証指標と lambda が入る。"""
    from app.batch.jobs import calc_lead_lag

    _seed_market()
    calc_lead_lag.run()

    res = client.get("/lead-lag")
    assert res.status_code == 200
    body = res.json()
    assert body["as_of"] is not None
    assert len(body["ranking"]) == 17
    scores = [r["score"] for r in body["ranking"]]
    assert scores == sorted(scores, reverse=True)  # score 降順
    first = body["ranking"][0]
    assert {"code", "label", "score", "signal"} <= set(first)
    meta = body["meta"]
    # JSON キーは "lambda"（Python 名 lambda_ のエイリアス）。
    assert "lambda" in meta
    assert meta["window"] == 60 and meta["k"] == 3
    # free プランは遅延扱い（既定 settings.jquants_plan=free）。
    assert meta["is_delayed"] is True
    assert meta["plan"] == "free"


# ---------------------------------------------------------------------------
# AI Tool: get_lead_lag（GET と同じ事実）
# ---------------------------------------------------------------------------


def test_tool_get_lead_lag_matches_facts(temp_db: None) -> None:
    """handle_get_lead_lag が GET /lead-lag と同じ ranking/meta の事実を返す（ADR-014）。"""
    import asyncio

    from app.advisor.tools import handlers
    from app.batch.jobs import calc_lead_lag

    _seed_market()
    calc_lead_lag.run()

    out = asyncio.run(handlers.handle_get_lead_lag({}))
    assert "error" not in out
    assert out["as_of"] is not None
    assert len(out["ranking"]) == 17
    scores = [r["score"] for r in out["ranking"]]
    assert scores == sorted(scores, reverse=True)
    assert "ic" in out["meta"] and "lambda" in out["meta"]


def test_tool_get_lead_lag_empty(temp_db: None) -> None:
    """台帳が空でも error にせず as_of=None/ranking=[]（ループを落とさない）。"""
    import asyncio

    from app.advisor.tools import handlers

    out = asyncio.run(handlers.handle_get_lead_lag({}))
    assert out["as_of"] is None
    assert out["ranking"] == []
