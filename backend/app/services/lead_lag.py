"""日米業種リードラグ・シグナルの下ごしらえ＋組み立て（ADR-014/016・ADR-010・SIG-FIN-036-13）。

設計の真実: 論文「部分空間正則化付き主成分分析を用いた日米業種リードラグ投資戦略」
（SIG-FIN-036-13）。Phase 7。quant.lead_lag（純関数・DB 非依存）と repo の間に立ち、

  1. repo から JP（daily_quotes・open/adj_close・5桁コード）と US（index_quotes・close）を読み、
  2. 日米共通営業日で整合させ rcc（close-to-close・28列）と roc（JP open-to-close・17列）を組み、
  3. quant.compute_lead_lag_signal / validate_lead_lag を同一 base_end で呼び、
  4. signals 行群（signal_type='lead_lag'・横断 0..1 正規化 score・payload に生値/和名/検証指標）
     と model メタ dict を返す。

数値計算そのものは quant.lead_lag に委ね、ここは下ごしらえとオーケストレーションのみ
（ADR-014/016）。欠損は NaN のまま quant に渡す（補間しない）。書き込み（upsert_signals）は
呼び出し側（batch/jobs/calc_lead_lag）が行う（ADR-014「事実は Python が計算」）。
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import Connection

from app.db import repo
from app.quant.lead_lag import (
    JP_SYMBOLS,
    US_SYMBOLS,
    compute_lead_lag_signal,
    validate_lead_lag,
)
from app.reference.sector_codes import S17_TO_TOPIX17_ETF, SECTOR17_LABELS_JA


# ── JP 業種 ETF コードの桁マッピング（確定済み契約）──────────────────────────
# quant の JP_SYMBOLS は 4桁（"1617".."1633"）だが、DB（daily_quotes.code）は J-Quants 慣習で
# 5桁形（4桁＋"0"。例 "1617"→"16170"）に正規化されて格納される（adapters/jquants._to_jq_code）。
# service が DB を読むときは 5桁で引き、quant に渡す列名は 4桁へ戻す（quant の契約を変えない）。
def _to_db_code(jp4: str) -> str:
    """quant の 4桁 JP コード → DB の 5桁コード（"1617" → "16170"）。"""
    return f"{jp4}0"


_JP_DB_CODES: list[str] = [_to_db_code(s) for s in JP_SYMBOLS]

# ── JP 業種 ETF（TOPIX-17）の業種和名（payload.label）──────────────────────────
# 和名の SSOT は app/reference/sector_codes に集約した（ADR-053）。lead_lag は instrument 空間
# （ETF ティッカー "1617".."1633"）でキーを持つので、reference の S17 和名を ETF キーへ導出する
# （値も挙動も従来の写経マップと不変）。
JP_SECTOR_LABELS: dict[str, str] = {
    etf: SECTOR17_LABELS_JA[s17] for s17, etf in S17_TO_TOPIX17_ETF.items()
}

# ── base_end（Cfull 固定用ベース期間の終端）の決め方 ─────────────────────────
# 論文は約5年の in-sample で事前エクスポージャー Cfull を推定する（§4）。本実装は
# 「共通営業日の最古から BASE_YEARS 年」をベース期間とし、その終端を base_end とする
# （compute と validate に同一値で渡し、Cfull を固定する）。共通履歴が BASE_YEARS 年に満たない
# 場合は base_end=None（quant 側が全期間を Cfull に代用）に倒す。
BASE_YEARS: int = 5
_TRADING_DAYS_PER_YEAR: int = 252

# notable（上位/下位分位）判定の分位割合。quant.lead_lag.Q（3分位 LS）と揃える。
_NOTABLE_Q: float = 0.3
_SCHEMA_VERSION: int = 1


def _level_series(rows: list[dict[str, Any]], value_key: str) -> pd.Series:
    """date 昇順の行群から「レベル（価格水準）」Series（index=date）を作る。

    value_key の値（close / adj_close）を date インデックスの Series にする（リターン化はしない）。
    リターン化の前にレベルを共通営業日へ inner join するのがバグ修正の要：先に pct_change すると
    join 先頭行に部分 NaN が残り、quant の base 統計（全期間）が NaN で None/empty に落ちる。
    欠損は NaN のまま・補間しない（ADR-014）。
    """
    if not rows:
        return pd.Series(dtype=float)
    s = pd.Series({r["date"]: r.get(value_key) for r in rows}, dtype=float, name=value_key)
    return s.sort_index()


def _series_open_to_close(rows: list[dict[str, Any]]) -> pd.Series:
    """date 昇順の行群から同日 open-to-close リターン Series（(close_raw-open)/open）を作る。

    JP の翌日実現評価（roc）用。**raw の open / close** を使う（バグ修正：adj_close は調整係数で
    スケールされた値なので raw open と混ぜると毎日ズレ、検証 IC/hit_rate/rr が歪む）。同日内リターン
    なので raw 同士で完結する。open が 0/None/None close の日は NaN（割れない・補間しない）。
    """
    if not rows:
        return pd.Series(dtype=float)
    closes: dict[str, float] = {}
    for r in rows:
        o = r.get("open")
        c = r.get("close")  # raw close（adj_close ではない）
        if o is None or c is None or o == 0:
            closes[r["date"]] = float("nan")
        else:
            closes[r["date"]] = (c - o) / o
    return pd.Series(closes, dtype=float).sort_index()


def _build_frames(
    conn: Connection,
) -> tuple[pd.DataFrame, pd.DataFrame] | None:
    """rcc（close-to-close・日米28列）と roc（JP open-to-close・17列）を組んで返す。

    手順（バグ1修正＝「レベルを共通営業日で揃えてから pct_change」）:
      1. US は index_quotes の close、JP は daily_quotes の adj_close の **レベル** Series を作る。
      2. レベルを `pd.concat(..., join="inner")` で**共通営業日**に揃え、列を US+JP 順に reindex。
      3. そこで `.pct_change()` → `.dropna(how="any")`。これで quant に渡る rcc は NaN ゼロの
         clean panel（先頭1行だけ落ちる）になり、論文の「共通に観測可能な営業日のみ」にも忠実。
    roc は JP の raw open/close から同日 open-to-close リターン（列名＝4桁 quant コード）。
    データ皆無（どちらかのブロックが全列空）なら None。
    """
    us_raw = repo.get_index_closes_by_symbols(conn, list(US_SYMBOLS))
    jp_raw = repo.get_daily_ohlc_by_codes(conn, _JP_DB_CODES)

    # US: 各 symbol を close レベル Series へ（列名＝素ティッカー）。
    us_levels: dict[str, pd.Series] = {}
    for sym in US_SYMBOLS:
        us_levels[sym] = _level_series(us_raw.get(sym, []), "close")

    # JP: adj_close レベル（close-to-close 用）と open-to-close（同日 raw）。列名は 4桁 quant。
    jp_levels: dict[str, pd.Series] = {}
    jp_oc: dict[str, pd.Series] = {}
    for jp4 in JP_SYMBOLS:
        rows = jp_raw.get(_to_db_code(jp4), [])
        jp_levels[jp4] = _level_series(rows, "adj_close")
        jp_oc[jp4] = _series_open_to_close(rows)

    # 全列が空なら計算対象なし。
    if all(s.empty for s in us_levels.values()) or all(s.empty for s in jp_levels.values()):
        return None

    # 1-2: US+JP の close レベルを共通営業日（日付の積集合）で inner join し、列順を US+JP に。
    all_levels = {**us_levels, **jp_levels}
    levels = pd.concat(all_levels, axis=1, join="inner")  # 共通営業日のみ
    levels = levels.reindex(columns=list(US_SYMBOLS) + list(JP_SYMBOLS))
    levels = levels.sort_index()

    # 3: 揃ったレベルから日次リターン化 → 先頭 pct_change NaN 等を全列ありで落とす（clean panel）。
    rcc = levels.pct_change().dropna(how="any")

    # roc: JP の open-to-close（rcc と独立の日付軸で揃える・足りない日は NaN のまま）。
    roc = pd.concat(jp_oc, axis=1, join="outer")
    roc = roc.reindex(columns=list(JP_SYMBOLS))
    roc = roc.sort_index()

    if rcc.empty:
        return None
    return rcc, roc


def _decide_base_end(rcc: pd.DataFrame) -> str | None:
    """共通営業日の最古から BASE_YEARS 年を Cfull のベース期間とし、その終端日を返す。

    共通履歴が BASE_YEARS 年に満たなければ None（quant が全期間を Cfull に代用）。
    base_end は文字列 'YYYY-MM-DD'（rcc.index は date 文字列）。
    """
    dates = [str(d) for d in rcc.index]
    if len(dates) < 2:
        return None
    n_base = BASE_YEARS * _TRADING_DAYS_PER_YEAR
    if len(dates) <= n_base:
        return None  # 全期間を Cfull に代用（quant 側で base_end=None と同義）
    return dates[n_base - 1]


def _normalize_scores(signals: dict[str, float]) -> dict[str, float]:
    """17 業種の生シグナルを横断 0..1 に min-max 正規化する（score 列用・ADR-026）。

    全値が同一（レンジ 0）なら一律 0.5（順位が付かない＝中庸）。NaN は 0.5 に倒す
    （score は NOT NULL・欠損で行を落とさない）。生値は payload に残す。
    """
    vals = np.array([signals[s] for s in JP_SYMBOLS], dtype=float)
    finite = vals[np.isfinite(vals)]
    if finite.size == 0:
        return {s: 0.5 for s in JP_SYMBOLS}
    lo = float(finite.min())
    hi = float(finite.max())
    span = hi - lo
    out: dict[str, float] = {}
    for s in JP_SYMBOLS:
        v = signals[s]
        if not np.isfinite(v):
            out[s] = 0.5
        elif span <= 1e-12:
            out[s] = 0.5
        else:
            out[s] = (float(v) - lo) / span
    return out


def _notable_flags(signals: dict[str, float], q: float = _NOTABLE_Q) -> dict[str, bool]:
    """上位/下位 q 分位（3分位 LS の選抜銘柄）に入るかの notable フラグを返す。

    quant の long-short 選抜（floor(q*n) 銘柄を上下から）と同じ規律で「目立つ業種」を立てる。
    有効値が少なく選抜が空なら全 False。
    """
    pairs = [(s, signals[s]) for s in JP_SYMBOLS if np.isfinite(signals[s])]
    n_valid = len(pairs)
    flags = {s: False for s in JP_SYMBOLS}
    if n_valid < 2:
        return flags
    n_pick = max(1, int(np.floor(q * n_valid)))
    ordered = sorted(pairs, key=lambda kv: kv[1])  # 昇順
    for s, _ in ordered[:n_pick]:  # 下位
        flags[s] = True
    for s, _ in ordered[-n_pick:]:  # 上位
        flags[s] = True
    return flags


def build_lead_lag_signals(conn: Connection) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """日米業種リードラグの signals 行群と model メタ dict を返す（ADR-014/016・SIG-FIN-036-13）。

    rcc/roc を組み立て → base_end を決め → quant.compute_lead_lag_signal（最新 as_of の翌日
    シグナル）と quant.validate_lead_lag（履歴検証指標）を同一 base_end で呼ぶ。signals 行は
    JP 業種ごと 1 行（signal_type='lead_lag'・code=5桁・score=横断 0..1・payload に生値/和名/
    notable/検証指標）。最新 as_of 日のみ upsert 対象として返す（古い日付は焼かない）。

    データ不足（履歴不足・列欠落・窓縮退）で compute が None のときは行 0・meta に理由を入れて返す
    （例外で落とさない＝呼び出し側ジョブは ok=True/rows=0 にできる）。書き込みは呼び出し側。
    """
    frames = _build_frames(conn)
    if frames is None:
        return [], {"as_of": None, "reason": "no_data", "schema_version": _SCHEMA_VERSION}
    rcc, roc = frames

    base_end = _decide_base_end(rcc)
    result = compute_lead_lag_signal(rcc, base_end=base_end)
    if result is None:
        return [], {
            "as_of": None,
            "reason": "insufficient_history",
            "base_end": base_end,
            "n_rows": int(rcc.shape[0]),
            "schema_version": _SCHEMA_VERSION,
        }

    validation = validate_lead_lag(rcc, roc, base_end=base_end)

    as_of = result["as_of"]
    raw_signals = result["signals"]  # {jp4: float}
    scores = _normalize_scores(raw_signals)
    notable = _notable_flags(raw_signals)

    rows: list[dict[str, Any]] = []
    for jp4 in JP_SYMBOLS:
        raw = raw_signals[jp4]
        payload = {
            "signal": None if not np.isfinite(raw) else float(raw),
            "label": JP_SECTOR_LABELS.get(jp4, jp4),
            "notable": bool(notable[jp4]),
            "model_as_of": as_of,
            "ic": validation["ic"],
            "hit_rate": validation["hit_rate"],
            "rr": validation["rr"],
            "k": 3,
            "lambda": 0.9,
            "window": 60,
            "schema_version": _SCHEMA_VERSION,
        }
        rows.append(
            {
                "date": as_of,  # 最新 as_of 日のみ（compute は最新行 t を返す）
                "code": _to_db_code(jp4),  # signals.code は 5桁の DB コード
                "signal_type": "lead_lag",
                "score": float(scores[jp4]),
                "payload": payload,  # dict（json.dumps は呼び出し側ジョブの責務）
            }
        )

    meta: dict[str, Any] = {
        "as_of": as_of,
        "base_end": base_end,
        "ic": validation["ic"],
        "hit_rate": validation["hit_rate"],
        "rr": validation["rr"],
        "n": validation["n"],
        "first": validation["first"],
        "last": validation["last"],
        "window": 60,
        "k": 3,
        "lambda": 0.9,
        "n_rows": int(rcc.shape[0]),
        "schema_version": _SCHEMA_VERSION,
    }
    return rows, meta
