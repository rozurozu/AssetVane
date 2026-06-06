"""AI Alpha Scorer の特徴量組立 — point-in-time（リーク防止）の純関数（Phase 5・ADR-014/016）。

設計の真実: docs/phase-specs/phase5-spec.md §2（特徴量リスト・リーク防止・skip 規律）。

- **DB を知らない純関数**（ADR-016）。引数は dict/DataFrame、戻り値は dict or None。
- **学習（train.py）と推論（infer.py）が特徴量定義を共有**する単一の真実。`FEATURE_NAMES` で
  列順を固定し、学習時と推論時の食い違い（静かな事故＝ADR-018）を防ぐ。
- **リーク防止（最重要）**: `as_of` 時点で**既知**の情報だけ使う。財務は `disclosed_date <= as_of`、
  価格は `date <= as_of` のみ参照し未来を覗かない（引数に未来列を持たない構造で排除）。
- **YoY は同一 `fiscal_period` タイプの直前行と突合**（FY↔前FY・四半期↔前年同四半期）。
  `fiscal_period` は J-Quants `CurPerType`（`FY`/`1Q`/`2Q`/`3Q`・年なし）。各タイプ年 1 回ゆえ
  「同タイプで disclosed_date が直前の行」がそのまま前年同期（年パース不要・堅牢）。
- **PER/PBR・EPS 成長率は通期(FY)行基準**（四半期 EPS は累計＝`valuation.py` の採用規律）。
- **数字を作らない**（ADR-014）: 分母≤0・欠損は NaN。LightGBM は NaN を欠損として扱える。
- **skip 規律**: アンカー株価が無い／ファンダが全て NaN の銘柄は None（score を出さない＝§2）。
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

# 特徴量の列順（学習・推論で共有する単一の真実。順序を変えるとモデル不整合＝ADR-018）。
FEATURE_NAMES: tuple[str, ...] = (
    "sales_growth_yoy",  # 売上 YoY（同period突合）
    "operating_profit_growth_yoy",  # 営業利益 YoY
    "profit_growth_yoy",  # 純利益 YoY
    "operating_margin",  # 営業利益率（最新開示行）
    "eps_growth_yoy",  # EPS YoY（FY 基準・累計回避）
    "per",  # PER = as_of 株価 / 最新FY EPS
    "pbr",  # PBR = as_of 株価 / 最新FY BPS
    "surprise_proxy",  # 開示日近傍の株価リターン（サプライズ代理）
    "momentum_3m",  # 3 か月（≈60 営業日）モメンタム
)

_SCHEMA_VERSION = 1
_FY = "FY"  # 通期タイプ（CurPerType）。PER/PBR/EPS 成長率はこのタイプ行を使う
_MOMENTUM_WINDOW = 60  # 3 か月 ≈ 60 営業日
_SURPRISE_WINDOW = 3  # 開示日 ±この営業日数の株価リターンをサプライズ代理に使う
_NAN = float("nan")


def _to_float(value: Any) -> float | None:
    """数値化（None/空/非数は None）。捏造はしない（ADR-014）。"""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def _yoy(curr: float | None, prev: float | None) -> float:
    """YoY 変化率 (curr/prev - 1)。欠損・分母≤0 は NaN（マイナス基準は無意味＝捏造しない）。"""
    if curr is None or prev is None or prev <= 0:
        return _NAN
    return curr / prev - 1.0


def _ratio(num: float | None, den: float | None) -> float:
    """num/den。欠損・分母≤0 は NaN（valuation.py の per/pbr 規律に揃える）。"""
    if num is None or den is None or den <= 0:
        return _NAN
    return num / den


def _latest_of_type(
    rows_asc: list[dict[str, Any]], fiscal_type: str | None, *, before: str | None = None
) -> dict[str, Any] | None:
    """disclosed_date 昇順の財務行から、指定 `fiscal_type` の最新行を返す（無ければ None）。

    `before` 指定時は disclosed_date がそれ未満の行に限る（前年同期＝直前の同タイプ行を拾う）。
    rows_asc は昇順なので、条件に合う最後の要素が最新。
    """
    if not fiscal_type:
        return None
    match: dict[str, Any] | None = None
    for r in rows_asc:
        if r.get("fiscal_period") != fiscal_type:
            continue
        if before is not None and not (r.get("disclosed_date") or "") < before:
            continue
        match = r
    return match


def _price_series(prices: pd.DataFrame, as_of: str) -> pd.Series | None:
    """`date <= as_of` の adj_close を date 昇順 Series で返す（point-in-time）。空なら None。"""
    if prices is None or "date" not in prices.columns or "adj_close" not in prices.columns:
        return None
    if prices.empty:
        return None
    filtered = pd.DataFrame(prices[prices["date"] <= as_of])
    if filtered.empty:
        return None
    filtered = filtered.sort_values("date")
    adj = pd.to_numeric(pd.Series(filtered["adj_close"]), errors="coerce")
    return pd.Series(adj).reset_index(drop=True)


def _momentum(adj: pd.Series) -> float:
    """as_of 株価 / 60 営業日前株価 - 1。窓不足・分母≤0・NaN は NaN。"""
    if len(adj) < _MOMENTUM_WINDOW + 1:
        return _NAN
    cur = adj.iloc[-1]
    base = adj.iloc[-1 - _MOMENTUM_WINDOW]
    if pd.isna(cur) or pd.isna(base) or base <= 0:
        return _NAN
    return float(cur / base - 1.0)


def _surprise(adj: pd.Series, dates: pd.Series, disclosed_date: str | None) -> float:
    """開示日近傍（±_SURPRISE_WINDOW 営業日・as_of 既知範囲のみ）の株価リターン（サプライズ代理）。

    開示直前の株価から開示直後（as_of を超えない範囲）へのリターン。算出不能なら NaN。
    """
    if disclosed_date is None or len(adj) == 0:
        return _NAN
    # 開示日以降で最初の営業日インデックス（開示直後の約定）。positional で取り .index を避ける。
    positions = np.flatnonzero(np.asarray(dates >= disclosed_date))
    if positions.size == 0:
        return _NAN
    idx = int(positions[0])
    pre = max(idx - _SURPRISE_WINDOW, 0)
    post = min(idx + _SURPRISE_WINDOW, len(adj) - 1)  # as_of 越えはしない（dates は <= as_of）
    base = adj.iloc[pre]
    end = adj.iloc[post]
    if pd.isna(base) or pd.isna(end) or base <= 0:
        return _NAN
    return float(end / base - 1.0)


def build_features_at(
    fin_rows: list[dict[str, Any]],
    prices: pd.DataFrame,
    *,
    as_of: str,
) -> dict[str, float] | None:
    """1 銘柄の as_of 時点 point-in-time 特徴量ベクトルを組む（リーク防止＝§2）。

    引数:
      fin_rows: その銘柄の financials 行（dict）。各 dict は disclosed_date/fiscal_period/
        net_sales/operating_profit/profit/eps/bps を含む（`repo.get_financials` 形・順不同可）。
      prices: その銘柄の日足 DataFrame（columns=[date, adj_close]・date 昇順）。
      as_of: 'YYYY-MM-DD'。この日時点で既知の情報だけ使う。

    戻り値: FEATURE_NAMES をキーに持つ dict（個別欠損は NaN＝LightGBM が欠損扱い）。
      アンカー株価が無い／財務が as_of までに無い／ファンダ特徴量が全て NaN なら None（skip）。
    """
    # --- 価格（point-in-time）。アンカー株価（as_of の最新終値）が無ければ skip ---
    adj = _price_series(prices, as_of)
    if adj is None:
        return None
    cur_price = adj.iloc[-1]
    if pd.isna(cur_price) or cur_price <= 0:
        return None
    known = pd.DataFrame(prices[prices["date"] <= as_of]).sort_values("date")
    dates = pd.Series(known["date"]).reset_index(drop=True)

    # --- 財務（point-in-time）。as_of までに開示済みの行のみ ---
    fin_known = [r for r in fin_rows if r.get("disclosed_date") and r["disclosed_date"] <= as_of]
    if not fin_known:
        return None
    fin_known.sort(key=lambda r: r["disclosed_date"])
    latest = fin_known[-1]
    # 同一 period タイプの直前行（＝前年同期）。
    prev_same = _latest_of_type(
        fin_known, latest.get("fiscal_period"), before=latest.get("disclosed_date")
    )
    # FY 行（EPS 成長率・PER/PBR 用。四半期 EPS の累計を避ける）。
    latest_fy = _latest_of_type(fin_known, _FY)
    prev_fy = (
        _latest_of_type(fin_known, _FY, before=latest_fy.get("disclosed_date"))
        if latest_fy
        else None
    )

    def f(row: dict[str, Any] | None, key: str) -> float | None:
        return _to_float(row.get(key)) if row else None

    sales_growth = _yoy(f(latest, "net_sales"), f(prev_same, "net_sales"))
    op_growth = _yoy(f(latest, "operating_profit"), f(prev_same, "operating_profit"))
    profit_growth = _yoy(f(latest, "profit"), f(prev_same, "profit"))
    operating_margin = _ratio(f(latest, "operating_profit"), f(latest, "net_sales"))
    eps_growth = _yoy(f(latest_fy, "eps"), f(prev_fy, "eps"))
    per = _ratio(float(cur_price), f(latest_fy, "eps"))
    pbr = _ratio(float(cur_price), f(latest_fy, "bps"))
    surprise = _surprise(adj, dates, latest.get("disclosed_date"))
    momentum_3m = _momentum(adj)

    feats: dict[str, float] = {
        "sales_growth_yoy": sales_growth,
        "operating_profit_growth_yoy": op_growth,
        "profit_growth_yoy": profit_growth,
        "operating_margin": operating_margin,
        "eps_growth_yoy": eps_growth,
        "per": per,
        "pbr": pbr,
        "surprise_proxy": surprise,
        "momentum_3m": momentum_3m,
    }

    # ファンダ特徴量が 1 つも組めない（全て NaN）なら ai_alpha（決算スコア）として無意味 → skip。
    fundamentals = (
        sales_growth,
        op_growth,
        profit_growth,
        operating_margin,
        eps_growth,
        per,
        pbr,
    )
    if all(np.isnan(v) for v in fundamentals):
        return None
    return feats
