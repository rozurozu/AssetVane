"""Tool 引数スキーマ（pydantic・検証用）。

設計の真実: docs/phase-specs/phase3-spec.md §4.4。各 Tool の**引数**を pydantic BaseModel で
定義し、handler が `XxxArgs(**args)` で検証する（不正引数を境界で弾く）。

返却スキーマ（dict）は spec §4.4 のコメント通りで、handler が素の dict を組み立てる
（型クラスは作らない＝registry が json.dumps して tool ロールへ）。比率・weight・cash_ratio は
0..1、遅延は `is_delayed: bool`、鮮度日は `as_of`、correlation は `{codes,labels,matrix}`、
weights は配列 `[{code,current_weight,target_weight,delta}]`（spec §4.4 の単位約束）。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class _ToolArgs(BaseModel):
    """全 Tool 引数の基底。未知キーは無視する（LLM の余分な引数で検証を落とさない）。"""

    model_config = ConfigDict(extra="ignore")


class GetIndicatorsArgs(_ToolArgs):
    """get_indicators の引数（spec §4.4）。"""

    code: str


class GetSignalsArgs(_ToolArgs):
    """get_signals の引数（spec §4.4）。すべて任意（省略時は最新日・全 type）。"""

    date: str | None = None  # 算出日 YYYY-MM-DD
    type: str | None = None  # signal_type（"momentum"/"volume_spike" 等）
    code: str | None = None


class ScreenStocksArgs(_ToolArgs):
    """screen_stocks の criteria（spec §4.4・キーは内部列名）。"""

    signal_type: str | None = None
    sector33_code: str | None = None
    min_score: float | None = None  # 0..1
    limit: int | None = None


class GetPortfolioMetricsArgs(_ToolArgs):
    """get_portfolio_metrics の引数（spec §4.4）。省略時は先頭ポートフォリオ。"""

    portfolio_id: int | None = None


class OptimizePortfolioArgs(_ToolArgs):
    """optimize_portfolio の引数（spec §4.4）。省略時は先頭ポートフォリオ。"""

    portfolio_id: int | None = None


class GetFinancialsArgs(_ToolArgs):
    """get_financials の引数（spec §4.4）。"""

    code: str


class GetAssetOverviewArgs(_ToolArgs):
    """get_asset_overview の引数（spec §4.4・引数なし）。"""


class SubmitJournalArgs(_ToolArgs):
    """submit_journal の引数（spec §4.4・§5・軸1 夜の出力受け）。

    proposed_policy_change は `{field, from, to, reason}`（任意）。
    """

    observations: str
    proposal: str | None = None
    proposed_policy_change: dict[str, object] | None = None
