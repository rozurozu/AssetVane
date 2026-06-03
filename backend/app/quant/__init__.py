"""数理層（quant）— シグナル・ポートフォリオ指標の「事実」を計算する純関数群。

設計の真実: docs/phase-specs/phase1-spec.md §4・docs/phase-specs/phase2-spec.md §4・
docs/data-model.md §4。

- **AI に数値を計算させない**（ADR-014）。ここで Python が SMA/RSI/出来高比・シャープ・
  最大DD・最適化などの「事実（数字）」を計算し、LLM は Tool で受け取った事実を
  解釈・提案するだけ。
- **手法はテスト済みコードで実装**（ADR-016）。各手法は「入力 DataFrame → 出力 dict/None」の
  純関数（DB I/O を持たない）で、既知系列テスト＋backtest 再計算が可能。
- パラメータは各モジュールの名前付き定数（env 不可・将来 `method_settings`＝ADR-027）。

公開関数:
- Phase 1:
  - `momentum.compute_momentum(quotes)` … SMA25/75・Wilder RSI(14)・ゴールデンクロス／RSI 反転。
  - `volume_spike.compute_volume_spike(quotes)` … 出来高急増（過去20日平均比）。
- Phase 2:
  - `portfolio.compute_portfolio_metrics(price_panel, weights, policy, labels)`
    … 相関・シャープ・MDD。
  - `portfolio.compute_deviations(weights, cash_ratio, sector_weights, policy, labels)`
    … policy 逸脱。
  - `optimize.optimize_portfolio(price_panel, policy, sectors, objective, current_weights)`
    … 平均分散最適化。
  - `backtest.backtest_portfolio(price_panel, weights, benchmark, rebalance)`
    … buy&hold バックテスト。
"""

from __future__ import annotations

from app.quant.backtest import backtest_portfolio
from app.quant.indicators import compute_indicators
from app.quant.momentum import compute_momentum
from app.quant.optimize import optimize_portfolio
from app.quant.portfolio import compute_deviations, compute_portfolio_metrics
from app.quant.volume_spike import compute_volume_spike

__all__ = [
    "backtest_portfolio",
    "compute_deviations",
    "compute_indicators",
    "compute_momentum",
    "compute_portfolio_metrics",
    "compute_volume_spike",
    "optimize_portfolio",
]
