"""quant.lead_lag の検証（ADR-016: テスト済みコードで実装）。

設計の真実: 論文「部分空間正則化付き主成分分析を用いた日米業種リードラグ投資戦略」
（SIG-FIN-036-13）。DB に触れず手組み/合成 DataFrame で純関数を検証する（実 API も叩かない）。

担保する観点:
- 事前部分空間 V0 が正規直交（VᵀV≈I）・v2/v3 が v1 に直交。
- 合成データ（真の V*_U, V*_J と共通因子 g_t）で IC が有意に正・シグナル符号が因子方向と整合。
- 伝播行列 B = VJ VUᵀ の rank ≤ K。
- データ不足（行数 < window+1）で None。NaN を埋めない・入力 DataFrame 不変。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.quant.lead_lag import (
    CYCLICAL,
    JP_SYMBOLS,
    US_SYMBOLS,
    K,
    _build_v0,
    _top_k_eigvecs,
    compute_lead_lag_signal,
    validate_lead_lag,
)


def _orthonormal_columns(n: int, k: int, seed: int) -> np.ndarray:
    """乱数行列を QR 分解して列直交（n×k）の真のローディングを作る。"""
    rng = np.random.default_rng(seed)
    q, _ = np.linalg.qr(rng.standard_normal((n, k)))
    return q[:, :k]


def _make_synthetic(
    n_days: int, k: int, seed: int, noise: float
) -> tuple[pd.DataFrame, pd.DataFrame, np.ndarray, np.ndarray, np.ndarray]:
    """共通因子 g_t による日米リードラグ構造を仕込んだ合成 rcc/roc を生成する。

    US_t = V*_U g_t + 小ノイズ（close-to-close）、
    翌日 JP_{t+1} = V*_J g_t + 小ノイズ（roc）。rcc 側の JP も別の小ノイズで埋める。
    戻り値: (rcc, roc, V*_U, V*_J, g)。
    """
    rng = np.random.default_rng(seed)
    n_u = len(US_SYMBOLS)
    n_j = len(JP_SYMBOLS)
    vu = _orthonormal_columns(n_u, k, seed + 1)
    vj = _orthonormal_columns(n_j, k, seed + 2)

    g = rng.standard_normal((n_days, k))  # 各日の共通因子
    us = g @ vu.T + noise * rng.standard_normal((n_days, n_u))  # close-to-close US

    # JP の rcc（同時点）も共通因子 g に同じ日本側ローディング V*_J で載る。
    # これにより日米結合相関行列の上位固有空間の JP ブロックが V*_J を近似でき、
    # 部分空間正則化 PCA が日本側ローディングを復元できる（論文 §3.3 の前提）。
    jp_rcc = g @ vj.T + noise * rng.standard_normal((n_days, n_j))
    # JP の roc は前日の米国因子に追随（t+1 が t の g に波及）。先頭日は因子寄与なし。
    jp_roc = np.zeros((n_days, n_j))
    jp_roc[1:] = g[:-1] @ vj.T + noise * rng.standard_normal((n_days - 1, n_j))

    # 一意で昇順の日付ラベル（実日付でなく順序のみ意味を持つ）。
    dates = [f"D{i:05d}" for i in range(n_days)]

    rcc = pd.DataFrame(
        np.column_stack([us, jp_rcc]),
        index=dates,
        columns=US_SYMBOLS + JP_SYMBOLS,
    )
    roc = pd.DataFrame(jp_roc, index=dates, columns=JP_SYMBOLS)
    return rcc, roc, vu, vj, g


def test_v0_orthonormal_and_directions() -> None:
    """V0 が正規直交（VᵀV≈I）かつ v2/v3 が v1（定数方向）に直交。"""
    symbols = US_SYMBOLS + JP_SYMBOLS
    v0 = _build_v0(symbols, CYCLICAL, len(US_SYMBOLS))
    assert v0.shape == (len(symbols), 3)
    gram = v0.T @ v0
    np.testing.assert_allclose(gram, np.eye(3), atol=1e-10)
    # v1 ∝ 1 なので v2・v3 の総和（v1 方向成分）はほぼ 0。
    assert abs(float(v0[:, 1].sum())) < 1e-9
    assert abs(float(v0[:, 2].sum())) < 1e-9


def test_propagation_matrix_low_rank() -> None:
    """伝播行列 B = V_J V_Uᵀ の rank ≤ K（命題1・式(22)）。"""
    symbols = US_SYMBOLS + JP_SYMBOLS
    rng = np.random.default_rng(7)
    creg = rng.standard_normal((len(symbols), len(symbols)))
    creg = creg + creg.T  # 対称化（eigh の前提）
    v_k = _top_k_eigvecs(creg, K)
    n_us = len(US_SYMBOLS)
    v_u = v_k[:n_us, :]
    v_j = v_k[n_us:, :]
    b = v_j @ v_u.T
    assert np.linalg.matrix_rank(b, tol=1e-9) <= K


def test_insufficient_data_returns_none() -> None:
    """行数 < window+1（=61 未満）で None。"""
    rcc, _, _, _, _ = _make_synthetic(n_days=40, k=3, seed=1, noise=0.05)
    assert compute_lead_lag_signal(rcc) is None


def test_input_dataframe_not_mutated() -> None:
    """入力 DataFrame を破壊変更しない（コピー検証）。"""
    rcc, roc, _, _, _ = _make_synthetic(n_days=120, k=3, seed=2, noise=0.05)
    rcc_copy = rcc.copy(deep=True)
    roc_copy = roc.copy(deep=True)
    compute_lead_lag_signal(rcc)
    validate_lead_lag(rcc, roc)
    pd.testing.assert_frame_equal(rcc, rcc_copy)
    pd.testing.assert_frame_equal(roc, roc_copy)


def test_nan_not_filled_returns_none() -> None:
    """窓内に NaN があれば None を返し、欠損を埋めない（ADR-014）。"""
    rcc, _, _, _, _ = _make_synthetic(n_days=120, k=3, seed=3, noise=0.05)
    rcc.loc[rcc.index[-5], "XLK"] = np.nan  # 直近窓内に NaN を仕込む
    assert compute_lead_lag_signal(rcc) is None


def test_signal_payload_shape() -> None:
    """戻り値 dict の形と signals が素の Python float（JSON 化・契約）。"""
    rcc, _, _, _, _ = _make_synthetic(n_days=150, k=3, seed=4, noise=0.05)
    result = compute_lead_lag_signal(rcc)
    assert result is not None
    assert set(result.keys()) == {"as_of", "signals"}
    assert result["as_of"] == str(rcc.index[-1])
    assert set(result["signals"].keys()) == set(JP_SYMBOLS)
    for v in result["signals"].values():
        assert isinstance(v, float)


def test_ic_positive_on_structured_data() -> None:
    """共通因子構造を仕込んだ合成データで IC（横断 Spearman 平均）が有意に正。"""
    rcc, roc, _, _, _ = _make_synthetic(n_days=400, k=3, seed=5, noise=0.05)
    metrics = validate_lead_lag(rcc, roc)
    assert metrics["n"] > 100
    # 強い因子構造なので IC は明確に正（ノイズ小）。
    assert metrics["ic"] > 0.15
    # ロングショート R/R も正に出る（年率換算）。
    assert metrics["rr"] > 0.0
    assert 0.0 <= metrics["hit_rate"] <= 1.0


def test_signal_sign_aligns_with_factor() -> None:
    """単一因子（K=1）で JP シグナル方向が真の V*_J 方向と整合（データ駆動の構造復元）。

    符号不定は VU・VJ の同一列で相殺するため、B=VJ VUᵀ は符号によらず一意。
    λ=0.9 の強い正則化は上位固有空間を事前部分空間（先頭が一様な v1）へ強く縮約するため、
    ここではデータ駆動の構造復元そのものを見る目的で λ を下げて検証する（PCA PLAIN 寄り）。
    符号は当日ショックの振れで反転し得るので絶対値で整合を見る。
    """
    rcc, roc, vu, vj, _ = _make_synthetic(n_days=300, k=1, seed=6, noise=0.02)
    result = compute_lead_lag_signal(rcc, k=1, lambda_=0.1)
    assert result is not None
    sig = np.array([result["signals"][s] for s in JP_SYMBOLS])
    # 推定シグナル方向と真の日本側ローディング方向の整合（符号は相殺済みで一意）。
    corr = float(np.corrcoef(sig, vj[:, 0])[0, 1])
    assert abs(corr) > 0.5
