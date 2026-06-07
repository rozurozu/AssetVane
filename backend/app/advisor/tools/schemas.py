"""Tool 引数スキーマ（pydantic・検証用）。

設計の真実: docs/phase-specs/phase3-spec.md §4.4。各 Tool の**引数**を pydantic BaseModel で
定義し、handler が `XxxArgs(**args)` で検証する（不正引数を境界で弾く）。

返却スキーマ（dict）は spec §4.4 のコメント通りで、handler が素の dict を組み立てる
（型クラスは作らない＝registry が json.dumps して tool ロールへ）。比率・weight・cash_ratio は
0..1、遅延は `is_delayed: bool`、鮮度日は `as_of`、correlation は `{codes,labels,matrix}`、
weights は配列 `[{code,current_weight,target_weight,delta}]`（spec §4.4 の単位約束）。

proposed_policy_change は単一 `{field, to}`（ADR-013）に構造強制する。`field` は policy 列の
enum（`DEFAULT_POLICY` のキーと一致）に締め、多フィールド patch を LLM に出させない。受理側は
`coerce_policy_change` が単一形に適合しなければ None に倒す（無人運用を落とさない＝ADR-018）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

# 非力なモデルが「省略」のつもりで渡す null 相当の文字列（小文字化して判定）。
_NULLISH_STRINGS = {"none", "null", ""}

# 提案で変更できる policy の構造化コア列（ADR-013・services/policy.py の DEFAULT_POLICY と一致）。
# rationale は即時更新（U-7）で提案対象外なので含めない。一致はドリフトガードテストで担保する。
PolicyField = Literal[
    "risk_tolerance",
    "time_horizon",
    "target_cash_ratio",
    "max_position_weight",
    "sector_caps",
    "target_return",
    "no_leverage",
    "exclusions",
]


class _ToolArgs(BaseModel):
    """全 Tool 引数の基底。未知キーは無視する（LLM の余分な引数で検証を落とさない）。"""

    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def _coerce_nullish_strings(cls, data: object) -> object:
        """任意引数に来る "None"/"null"/"" を実 None に正規化する（ADR-018・頑健性）。

        非力なモデルは省略すべき任意引数に文字列 "None" 等を入れてくる（例: portfolio_id="None"
        で int 検証が落ちる）。これを「省略」と同義に倒し、submission 全体を弾かないようにする。
        """
        if isinstance(data, dict):
            return {
                k: (None if isinstance(v, str) and v.strip().lower() in _NULLISH_STRINGS else v)
                for k, v in data.items()
            }
        return data


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


class InvestigateStockArgs(_ToolArgs):
    """investigate_stock の引数（spec §4・正本）。

    引数は `code` のみ（spec §4）。`mode` は呼び出し文脈で決まり（チャット Tool 経由＝リッチ）、
    LLM には見せない。handler が内部で `mode="chat"` を補う（spec §4.1）。
    """

    code: str


class GetDossierArgs(_ToolArgs):
    """get_dossier の引数（spec §4・正本）。"""

    code: str


class FetchNewsArgs(_ToolArgs):
    """fetch_news の引数（spec §4・正本）。

    since は発行下限日 'YYYY-MM-DD'（任意・省略時は取得側既定）。mode は文脈で決まり LLM 非露出。
    """

    code: str
    since: str | None = None


class GetGeneralNewsArgs(_ToolArgs):
    """get_general_news の引数（ADR-034・引数なし）。

    銘柄に紐づかない一般ニュース（市況・マクロ・世界情勢）の直近台帳を返すだけなので引数は無い
    （カテゴリ指定もせず全カテゴリを返す＝get_asset_overview と同じ空モデル）。
    """


class GetLeadLagArgs(_ToolArgs):
    """get_lead_lag の引数（Phase 7・SIG-FIN-036-13・引数なし）。

    日米業種リードラグの最新ランキング＋検証指標を返すだけなので引数は無い
    （get_general_news / get_asset_overview と同じ空モデル）。
    """


class ProposedPolicyChange(_ToolArgs):
    """方針変更案（単一フィールド・ADR-013）。

    policy は「1 変更ずつ育てる」（ADR-013）。`field` を policy 列の enum に締め、`to` を必須に
    することで、LLM に見せる JSON Schema が単一形を要求する＝多フィールド patch を出させない。
    余剰キーは `extra="ignore"`（_ToolArgs 継承）で無視するため、多列 patch は `field`/`to` を
    欠いて required 検証で弾かれる。受理側は coerce_policy_change が不適合を None に倒す。
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    field: PolicyField = Field(
        description="変更する policy 列。1 提案=1 列（ADR-013）。複数なら最も効く 1 つに絞る。"
    )
    to: object = Field(description="変更後の値（比率は 0..1）。")
    from_: object | None = Field(
        default=None, alias="from", description="変更前の値（任意・監査用）。"
    )
    reason: str | None = Field(default=None, description="変更理由（任意）。")


class SubmitJournalArgs(_ToolArgs):
    """submit_journal の引数（spec §4.4・§5・軸1 夜の出力受け）。

    proposed_policy_change は単一 `{field, to}`（任意・ADR-013）。複数列を直したい晩は最も効く
    1 件に絞る（多列 patch は受理側で破棄される）。
    """

    observations: str
    proposal: str | None = None
    proposed_policy_change: ProposedPolicyChange | None = Field(
        default=None,
        description="方針変更案（任意・単一 {field, to}）。複数列は最も効く 1 件に絞る。",
    )


def coerce_policy_change(raw: object) -> dict[str, object] | None:
    """raw を単一 `{field, to}` の policy 変更案に正規化する。適合しなければ None（ADR-013/018）。

    nightly（proposal 起票判定）と handle_submit_journal（受理ゲート）が共有する単一の真実。
    多フィールド patch・非 dict・必須欠け・`to` が None のものはすべて None に倒し、無人運用を
    落とさない（適用不能 proposal を queue に入れない＝U-10 裁定①）。返す dict は
    apply_policy_change がそのまま食える `{field, to, from?, reason?}` 形。
    """
    if raw is None:
        return None
    try:
        model = ProposedPolicyChange.model_validate(raw)
    except ValidationError:
        return None
    # 変更後の値が無い提案は意味を成さない（0/0.0 は有効なので is None で判定）。
    if model.to is None:
        return None
    result: dict[str, object] = {"field": model.field, "to": model.to}
    if model.from_ is not None:
        result["from"] = model.from_
    if model.reason is not None:
        result["reason"] = model.reason
    return result
