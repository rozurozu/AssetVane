"""日米業種リードラグ・シグナル — 部分空間正則化付き主成分分析（PCA）。

設計の真実: 論文「部分空間正則化付き主成分分析を用いた日米業種リードラグ投資戦略」
（人工知能学会 金融情報学研究会 SIG-FIN-036-13・中川ほか）。Phase 7 の中核手法。

仮説: 取引時間帯が重ならないため、先に閉じる米国市場で確定した情報（当日 close-to-close
リターン rcc_{U,t}）が、後で開く日本市場の翌営業日 open-to-close リターン roc_{J,t+1} へ
波及する。日米を結合した相関行列の上位固有空間（K 次元）を部分空間正則化付き PCA で
安定推定し、米国当日ショックを射影して因子スコアを得、日本側ローディングで復元して
翌営業日シグナル ŝ_{J,t+1} を構成する。

- **純関数・DB 非依存**（ADR-014/016）。入力は素の `pd.DataFrame`、戻り値は素の dict/None。
  入力 DataFrame を破壊変更しない・欠損は NaN のまま埋めない・データ不足は None/空・
  例外で落とさない。
- **numpy + pandas のみ**（scipy 不可）。対称行列の固有分解は `numpy.linalg.eigh`。
- ユニバース・定数は名前付きモジュール定数（env 不可・将来 method_settings＝ADR-027）。
- 固有ベクトルの符号は VJ・VU の同一列間で相殺するため、符号不定でも伝播行列 B は不変。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# ── ユニバース（順序固定。論文 §4.1）────────────────────────────────────
# 米国: Select Sector SPDR ETF（S&P500 の GICS 11 業種）。N_U = 11。
US_SYMBOLS: list[str] = [
    "XLB",
    "XLE",
    "XLF",
    "XLI",
    "XLK",
    "XLP",
    "XLU",
    "XLV",
    "XLY",
    "XLC",
    "XLRE",
]
# 日本: NEXT FUNDS TOPIX-17 業種別 ETF（配当込み）。N_J = 17。
JP_SYMBOLS: list[str] = [
    "1617",
    "1618",
    "1619",
    "1620",
    "1621",
    "1622",
    "1623",
    "1624",
    "1625",
    "1626",
    "1627",
    "1628",
    "1629",
    "1630",
    "1631",
    "1632",
    "1633",
]

# シクリカル(+1)/ディフェンシブ(−1)/その他(0) ラベル（論文 §4.1）。
# v3（シクリカル・ディフェンシブ符号ベクトル）の構築に使う。
CYCLICAL: dict[str, int] = {
    # 米国
    "XLB": 1,
    "XLE": 1,
    "XLF": 1,
    "XLRE": 1,
    "XLK": -1,
    "XLP": -1,
    "XLU": -1,
    "XLV": -1,
    # 日本
    "1618": 1,
    "1625": 1,
    "1629": 1,
    "1631": 1,
    "1617": -1,
    "1621": -1,
    "1627": -1,
    "1630": -1,
}

# ── 手法パラメータ（論文の確定値・§3）─────────────────────────────────
L: int = 60  # 推定ウィンドウ長 W_t = {t-L, ..., t-1}
K0: int = 3  # 事前部分空間 V0 の次元（v1=グローバル・v2=国スプレッド・v3=シクリカル/ディフェ）
K: int = 3  # 抽出する上位固有ベクトル数
LAMBDA: float = 0.9  # 正則化係数 Creg = (1-λ)Ct + λC0
Q: float = 0.3  # 3 分位ロングショートの分位点割合 q ∈ (0, 1/2)

_TRADING_DAYS: int = 252  # 年率換算の営業日数（R/R の年率化）
_SCHEMA_VERSION: int = 1


def _gram_schmidt_columns(matrix: np.ndarray) -> np.ndarray:
    """列ベクトルを左から順に直交化・正規化する（古典的 Gram-Schmidt）。

    matrix: (N, k)。戻り値は (N, k) の正規直交列（VᵀV≈I）。
    ゼロ近傍に縮退した列はそのまま（ノルム 0）で残す（呼び出し側で K0=3 を保証）。
    """
    n_rows, n_cols = matrix.shape
    out = np.zeros((n_rows, n_cols), dtype=float)
    for j in range(n_cols):
        v = matrix[:, j].astype(float).copy()
        for i in range(j):
            # 既に確定した直交列 out[:, i] への射影成分を引く。
            v = v - float(out[:, i] @ matrix[:, j]) * out[:, i]
        norm = float(np.linalg.norm(v))
        if norm > 1e-12:
            out[:, j] = v / norm
    return out


def _build_v0(symbols: list[str], cyclical: dict[str, int], n_us: int) -> np.ndarray:
    """事前固有ベクトル V0 ∈ R^{N×K0}（列直交）を構築する（論文 §3.1）。

    symbols: US+JP を連結した順序（長さ N）。n_us: 米国の本数（先頭から）。
    v1 ∝ 1（グローバル）/ v2 ∝ (1_US, −1_JP)（国スプレッド）/
    v3 = シクリカル(+1)/ディフェンシブ(−1)/その他(0) 符号ベクトル。
    v2・v3 を v1 へ、v3 を v2 へ直交化し、各列を正規化して [v1, v2, v3] を返す。
    """
    n = len(symbols)
    v1 = np.ones(n, dtype=float)
    v2 = np.array([1.0 if i < n_us else -1.0 for i in range(n)], dtype=float)
    v3 = np.array([float(cyclical.get(sym, 0)) for sym in symbols], dtype=float)
    raw = np.column_stack([v1, v2, v3])  # (N, 3)
    return _gram_schmidt_columns(raw)


def _correlation(std_returns: np.ndarray) -> np.ndarray:
    """標準化リターン行列（L×N）から相関行列（N×N）を返す。

    `np.corrcoef(rowvar=False)`。列に定数（分散 0）があると NaN になり得るが、
    呼び出し側で窓内の σ を検査して回避する。
    """
    return np.corrcoef(std_returns, rowvar=False)


def _build_c0(cfull: np.ndarray, v0: np.ndarray) -> np.ndarray:
    """事前エクスポージャー C0 を構築する（論文 §3.1 式(10)-(12)）。

    D0 = diag(V0ᵀ Cfull V0) → Craw0 = V0 D0 V0ᵀ → C0 = Δ^{-1/2} Craw0 Δ^{-1/2}
    （Δ = diag(Craw0)）→ 対角を 1 に強制。
    """
    d0 = np.diag(np.diag(v0.T @ cfull @ v0))  # K0×K0（対角のみ残す）
    craw0 = v0 @ d0 @ v0.T  # N×N
    delta = np.diag(craw0)
    # Δ^{-1/2}。対角がゼロ/負（数値誤差）に縮退した行は単位スケールにフォールバック。
    safe = np.where(delta > 1e-12, delta, 1.0)
    inv_sqrt = 1.0 / np.sqrt(safe)
    c0 = craw0 * np.outer(inv_sqrt, inv_sqrt)
    np.fill_diagonal(c0, 1.0)  # diag(C0)=1 を強制
    return c0


def _top_k_eigvecs(creg: np.ndarray, k: int) -> np.ndarray:
    """対称行列 creg の上位 K 固有ベクトル（降順）を返す（N×K）。

    `numpy.linalg.eigh` は固有値昇順で返すため反転して上位 K を採る。
    """
    eigvals, eigvecs = np.linalg.eigh(creg)  # 昇順
    order = np.argsort(eigvals)[::-1]  # 降順
    top = order[:k]
    return eigvecs[:, top]


def _signal_at(
    win_cc: np.ndarray,
    shock_cc: np.ndarray,
    c0: np.ndarray,
    *,
    n_us: int,
    lambda_: float,
    k: int,
) -> np.ndarray | None:
    """1 時点 t の日本側シグナルベクトル ŝ_{J,t+1} ∈ R^{N_J} を算出する（論文 §3.2-3.3）。

    win_cc: 窓 W_t の close-to-close リターン（L×N・US が先頭 n_us 列）。
    shock_cc: 当日 t の close-to-close リターン（N,）。米国ブロックのみ使う。
    1) 窓内 μ,σ(ddof=0) で標準化 → 窓内相関 C_t
    2) Creg_t = (1-λ)C_t + λC0
    3) eigh で上位 K 固有ベクトル V^{(K)}_t（N×K）→ US/JP ブロック分割
    4) US 当日ショック標準化 z_{U,t} → f_t = V^{(K)ᵀ}_{U,t} z_{U,t}
       → ŝ_{J,t+1} = V^{(K)}_{J,t} f_t
    窓内に分散 0 や NaN があれば None。
    """
    mu = win_cc.mean(axis=0)
    sigma = win_cc.std(axis=0, ddof=0)
    if not np.all(np.isfinite(mu)) or not np.all(np.isfinite(sigma)):
        return None
    if np.any(sigma <= 1e-12):
        return None  # 定数列は標準化・相関が定義できない

    std_win = (win_cc - mu) / sigma
    c_t = _correlation(std_win)
    if not np.all(np.isfinite(c_t)):
        return None

    creg = (1.0 - lambda_) * c_t + lambda_ * c0
    v_k = _top_k_eigvecs(creg, k)  # N×K
    v_u = v_k[:n_us, :]  # N_U×K
    v_j = v_k[n_us:, :]  # N_J×K

    # 米国当日ショックを窓の μ,σ で標準化（論文 式(17)）。
    z_u = (shock_cc[:n_us] - mu[:n_us]) / sigma[:n_us]
    if not np.all(np.isfinite(z_u)):
        return None

    f_t = v_u.T @ z_u  # K（因子スコア・式(18)）
    s_j = v_j @ f_t  # N_J（日本側シグナル・式(19)）
    return s_j


def _extract_matrix(rcc: pd.DataFrame, symbols: list[str]) -> tuple[np.ndarray, list[str]] | None:
    """rcc から symbols 列を抽出し (値配列, 日付 list) を返す（入力は破壊しない）。

    symbols のいずれかが欠落していれば None。値は float ndarray（NaN は保持）。
    """
    if rcc is None or rcc.empty:
        return None
    missing = [s for s in symbols if s not in rcc.columns]
    if missing:
        return None
    sub = rcc.loc[:, symbols]  # 列の射影（コピー的・元 df は不変）
    values = sub.to_numpy(dtype=float)
    dates = [str(d) for d in rcc.index]
    return values, dates


def compute_lead_lag_signal(
    rcc: pd.DataFrame,
    *,
    us_symbols: list[str] = US_SYMBOLS,
    jp_symbols: list[str] = JP_SYMBOLS,
    cyclical: dict[str, int] = CYCLICAL,
    base_end: str | None = None,
    lambda_: float = LAMBDA,
    k: int = K,
    k0: int = K0,
    window: int = L,
) -> dict | None:
    """最新行 t を基準に日本業種の翌営業日リードラグ・シグナルを算出する純関数。

    設計の真実: 論文 SIG-FIN-036-13 §3。ADR-014/016（純関数・DB 非依存）。

    rcc: index=date（共通営業日・昇順）, columns ⊇ us+jp, 値=close-to-close リターン。
    base_end: Cfull（事前エクスポージャー）を作るベース期間の終端（YYYY-MM-DD）。
        None なら全期間で代用。
    戻り値: {"as_of": "YYYY-MM-DD"(=t), "signals": {jp_symbol: float}} | None（データ不足）。
        最新行が t、その翌日 t+1 への予測シグナル。

    データ不足（行数 < window+1）・列欠落・窓の縮退（分散 0/NaN）では None を返す
    （例外で落とさない・欠損を埋めない＝ADR-014）。
    """
    symbols = list(us_symbols) + list(jp_symbols)
    n_us = len(us_symbols)
    extracted = _extract_matrix(rcc, symbols)
    if extracted is None:
        return None
    values, dates = extracted
    n_rows = values.shape[0]
    # 最新行 t を窓外の「当日ショック」とし、その直前 window 行を窓 W_t とする。
    if n_rows < window + 1:
        return None

    v0 = _build_v0(symbols, cyclical, n_us)

    # Cfull のベース行列: base_end までの行（無ければ全期間）。標準化後の相関。
    base_values = values
    if base_end is not None:
        mask = [d <= base_end for d in dates]
        if any(mask):
            base_values = values[np.array(mask)]
    if base_values.shape[0] < 2:
        return None
    base_mu = base_values.mean(axis=0)
    base_sigma = base_values.std(axis=0, ddof=0)
    if np.any(base_sigma <= 1e-12) or not np.all(np.isfinite(base_sigma)):
        return None
    base_std = (base_values - base_mu) / base_sigma
    cfull = _correlation(base_std)
    if not np.all(np.isfinite(cfull)):
        return None
    c0 = _build_c0(cfull, v0)

    win_cc = values[n_rows - 1 - window : n_rows - 1]  # 窓 W_t（L 行）
    shock_cc = values[n_rows - 1]  # 当日 t
    if np.any(~np.isfinite(win_cc)) or np.any(~np.isfinite(shock_cc[:n_us])):
        return None

    s_j = _signal_at(win_cc, shock_cc, c0, n_us=n_us, lambda_=lambda_, k=k)
    if s_j is None:
        return None

    signals = {sym: float(s_j[i]) for i, sym in enumerate(jp_symbols)}
    return {"as_of": str(dates[-1]), "signals": signals}


def _spearman_ic(signal: np.ndarray, realized: np.ndarray) -> float | None:
    """横断 Spearman 順位相関（IC）を 1 時点ぶん計算する。

    signal・realized: ともに長さ N_J。NaN を含む要素は除外。
    有効ペアが 2 未満、または順位が定数なら None。scipy 不使用＝順位化して corrcoef。
    """
    mask = np.isfinite(signal) & np.isfinite(realized)
    if int(mask.sum()) < 2:
        return None
    s = signal[mask]
    r = realized[mask]
    rank_s = pd.Series(s).rank().to_numpy()
    rank_r = pd.Series(r).rank().to_numpy()
    if np.std(rank_s) <= 1e-12 or np.std(rank_r) <= 1e-12:
        return None
    return float(np.corrcoef(rank_s, rank_r)[0, 1])


def _long_short_weights(signal: np.ndarray, q: float) -> np.ndarray | None:
    """3 分位ロングショートの等ウェイトを返す（論文 §2.2 式(3)-(6)）。

    上位 q をロング(+1/|L|)・下位 q をショート(−1/|S|)・他 0。Σw=0, Σ|w|=2。
    有効銘柄が少なく上位/下位が空、または完全重複なら None。
    """
    mask = np.isfinite(signal)
    n_valid = int(mask.sum())
    if n_valid < 2:
        return None
    n_pick = max(1, int(np.floor(q * n_valid)))
    idx = np.where(mask)[0]
    order = idx[np.argsort(signal[idx])]  # 昇順
    bottom = order[:n_pick]
    top = order[-n_pick:]
    # ロングとショートが重ならないように（小ユニバース保護）。
    if set(top.tolist()) & set(bottom.tolist()):
        return None
    w = np.zeros_like(signal, dtype=float)
    w[top] = 1.0 / len(top)
    w[bottom] = -1.0 / len(bottom)
    return w


def validate_lead_lag(
    rcc: pd.DataFrame,
    roc: pd.DataFrame,
    *,
    us_symbols: list[str] = US_SYMBOLS,
    jp_symbols: list[str] = JP_SYMBOLS,
    cyclical: dict[str, int] = CYCLICAL,
    base_end: str | None = None,
    lambda_: float = LAMBDA,
    k: int = K,
    k0: int = K0,
    window: int = L,
) -> dict:
    """履歴で各 t のシグナル s_{j,t} と実現 roc_{j,t+1} を突き合わせ検証指標を返す純関数。

    設計の真実: 論文 SIG-FIN-036-13 §4.2（AR/RISK/R/R）。ADR-014/016。

    rcc: index=date（昇順）, columns ⊇ us+jp, close-to-close リターン。
    roc: index=date, columns=jp_symbols, JP の open-to-close リターン（翌日実現の評価用）。
    戻り値:
      {"ic": 横断 Spearman の平均, "hit_rate": LS 日次が正の割合,
       "rr": 3 分位 LS 日次リターンの年率 R/R(252 換算),
       "n": 評価サンプル数, "first": 最初の評価日, "last": 最後の評価日}。
    データ不足時は ic/hit_rate/rr=0.0, n=0, first/last="" を返す（例外で落とさない）。
    """
    empty = {"ic": 0.0, "hit_rate": 0.0, "rr": 0.0, "n": 0, "first": "", "last": ""}
    symbols = list(us_symbols) + list(jp_symbols)
    n_us = len(us_symbols)

    extracted = _extract_matrix(rcc, symbols)
    if extracted is None:
        return empty
    values, dates = extracted
    n_rows = values.shape[0]
    if n_rows < window + 2:
        return empty

    if roc is None or roc.empty:
        return empty
    if any(s not in roc.columns for s in jp_symbols):
        return empty
    roc_sub = roc.loc[:, jp_symbols]
    roc_by_date = {str(d): roc_sub.iloc[i].to_numpy(dtype=float) for i, d in enumerate(roc.index)}

    v0 = _build_v0(symbols, cyclical, n_us)

    # Cfull は固定（ベース期間 base_end まで、無ければ全期間）。
    base_values = values
    if base_end is not None:
        mask = np.array([d <= base_end for d in dates])
        if mask.any():
            base_values = values[mask]
    if base_values.shape[0] < 2:
        return empty
    base_mu = base_values.mean(axis=0)
    base_sigma = base_values.std(axis=0, ddof=0)
    if np.any(base_sigma <= 1e-12) or not np.all(np.isfinite(base_sigma)):
        return empty
    cfull = _correlation((base_values - base_mu) / base_sigma)
    if not np.all(np.isfinite(cfull)):
        return empty
    c0 = _build_c0(cfull, v0)

    ics: list[float] = []
    ls_returns: list[float] = []
    eval_dates: list[str] = []

    # 各 t（窓終端＝t）について、当日ショック values[t] で s_{J,t+1} を作り、
    # 翌営業日 dates[t+1] の roc と突き合わせる。t は window..n_rows-2。
    for t in range(window, n_rows - 1):
        win_cc = values[t - window : t]
        shock_cc = values[t]
        if np.any(~np.isfinite(win_cc)) or np.any(~np.isfinite(shock_cc[:n_us])):
            continue
        s_j = _signal_at(win_cc, shock_cc, c0, n_us=n_us, lambda_=lambda_, k=k)
        if s_j is None:
            continue
        next_date = dates[t + 1]
        realized = roc_by_date.get(next_date)
        if realized is None:
            continue

        ic = _spearman_ic(s_j, realized)
        if ic is not None:
            ics.append(ic)

        w = _long_short_weights(s_j, Q)
        if w is not None:
            # ロング/ショート銘柄の realized が NaN だと寄与が NaN になるため、
            # 有効ウェイト×有効 realized のみ合算（NaN は 0 寄与）。
            valid = np.isfinite(realized) & (w != 0.0)
            if valid.any():
                ret = float(np.nansum(w[valid] * realized[valid]))
                ls_returns.append(ret)
                eval_dates.append(next_date)

    if not eval_dates:
        return empty

    rets = np.array(ls_returns, dtype=float)
    ar = float(rets.mean()) * _TRADING_DAYS  # 年率リターン
    risk = float(rets.std(ddof=1)) * np.sqrt(_TRADING_DAYS) if len(rets) > 1 else 0.0
    rr = ar / risk if risk > 1e-12 else 0.0
    hit_rate = float((rets > 0).mean())
    ic_mean = float(np.mean(ics)) if ics else 0.0

    return {
        "ic": ic_mean,
        "hit_rate": hit_rate,
        "rr": rr,
        "n": len(eval_dates),
        "first": eval_dates[0],
        "last": eval_dates[-1],
    }
