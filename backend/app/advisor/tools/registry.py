"""Tool レジストリ — スキーマ＋handler＋min_phase の単一の真実（spec §4.1）。

設計の真実: docs/phase-specs/phase3-spec.md §4.1・§4.4。

各 Tool を「OpenAI tools スキーマ（parameters）＋handler 関数＋min_phase」で 1 か所に宣言し、
スキーマと実装がズレないようにする。dispatch（段2）は `REGISTRY[name].handler(args)` を呼び、
`openai_tools(phase)` で min_phase ゲートして LLM に露出する Tool を絞る。

dossier 系（P4・investigate_stock / get_dossier / fetch_news）は min_phase=4 で登録する。
`openai_tools(available_phase)` が available_phase>=4 のときだけ LLM に露出する（Phase ゲート）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.advisor.tools import handlers
from app.advisor.tools.schemas import (
    FetchNewsArgs,
    GetDossierArgs,
    GetFinancialsArgs,
    GetIndicatorsArgs,
    GetPortfolioMetricsArgs,
    GetSignalsArgs,
    InvestigateStockArgs,
    OptimizePortfolioArgs,
    ScreenStocksArgs,
    SubmitJournalArgs,
)

# 現在の投入フェーズ（段2 の dispatch が openai_tools(phase) に渡す）。
CURRENT_PHASE: int = 3


@dataclass(frozen=True)
class ToolDef:
    """1 Tool の定義（spec §4.1）。"""

    name: str
    description: str  # LLM 向け説明（いつ呼ぶか）
    parameters: dict[str, object]  # OpenAI Function Calling 用 JSON Schema（引数）
    handler: Callable[[dict[str, object]], Awaitable[dict]]  # handlers.py の実体
    min_phase: int  # 投入フェーズ（1/2/3/4…）


def _schema(model: type) -> dict[str, object]:
    """pydantic モデルから OpenAI Function Calling 用 JSON Schema を作る。

    OpenAI は `additionalProperties` を見るため、`extra="ignore"` 由来の余計なキー差を避けて
    そのまま渡す（pydantic v2 の model_json_schema 出力で十分機能する）。
    """
    return model.model_json_schema()


# 引数なし Tool（get_asset_overview）の空スキーマ。
_EMPTY_SCHEMA: dict[str, object] = {"type": "object", "properties": {}}


REGISTRY: dict[str, ToolDef] = {
    # --- Phase 1 ---
    "get_indicators": ToolDef(
        name="get_indicators",
        description=(
            "指定銘柄の最新の指標（SMA25/75・RSI14・出来高MA20・調整後終値）を取得する。"
            "個別銘柄のトレンドや過熱感を定量的に語る前に必ず呼ぶ。"
        ),
        parameters=_schema(GetIndicatorsArgs),
        handler=handlers.handle_get_indicators,
        min_phase=1,
    ),
    "get_signals": ToolDef(
        name="get_signals",
        description=(
            "夜間バッチが事前計算したシグナル（momentum / volume_spike 等）を取得する。"
            "「今どんな兆候が出ているか」を尋ねられたとき・候補探しの起点に呼ぶ。"
        ),
        parameters=_schema(GetSignalsArgs),
        handler=handlers.handle_get_signals,
        min_phase=1,
    ),
    "screen_stocks": ToolDef(
        name="screen_stocks",
        description=(
            "シグナルを条件（signal_type / sector33_code / min_score / limit）で絞り込み、"
            "候補銘柄を指標付きで列挙する。スクリーニング・候補抽出のときに呼ぶ。"
        ),
        parameters=_schema(ScreenStocksArgs),
        handler=handlers.handle_screen_stocks,
        min_phase=1,
    ),
    # --- Phase 2 ---
    "get_portfolio_metrics": ToolDef(
        name="get_portfolio_metrics",
        description=(
            "保有ポートフォリオの相関・シャープレシオ・最大ドローダウン・年率リターン/"
            "ボラティリティと policy 逸脱を取得する。配分やリスクを論じる前に必ず呼ぶ。"
        ),
        parameters=_schema(GetPortfolioMetricsArgs),
        handler=handlers.handle_get_portfolio_metrics,
        min_phase=2,
    ),
    "optimize_portfolio": ToolDef(
        name="optimize_portfolio",
        description=(
            "policy 制約付き平均分散最適化を実行し、目標ウェイトと現状からの差分を取得する。"
            "「どう配分し直すべきか」「リバランス案は」と問われたら呼ぶ。"
        ),
        parameters=_schema(OptimizePortfolioArgs),
        handler=handlers.handle_optimize_portfolio,
        min_phase=2,
    ),
    "get_financials": ToolDef(
        name="get_financials",
        description=(
            "指定銘柄の直近の財務（売上・営業利益・純利益・EPS・BPS）を取得する。"
            "割安・割高や業績の良し悪しを語る前に呼ぶ（PER 単体で結論しない）。"
        ),
        parameters=_schema(GetFinancialsArgs),
        handler=handlers.handle_get_financials,
        min_phase=2,
    ),
    "get_asset_overview": ToolDef(
        name="get_asset_overview",
        description=(
            "総資産（株式・現金・外部資産）の内訳・配分・損益・policy 逸脱・推移を取得する。"
            "資産全体の状況や配分バランスを論じるときに呼ぶ。"
        ),
        parameters=_EMPTY_SCHEMA,
        handler=handlers.handle_get_asset_overview,
        min_phase=2,
    ),
    # --- Phase 3 ---
    "submit_journal": ToolDef(
        name="submit_journal",
        description=(
            "夜の分析の結論（所見・提案・方針変更案）を投資日記として記録する。"
            "軸1（夜の分析AI）が分析の最終ターンで 1 度だけ呼ぶ。"
            "方針変更案（proposed_policy_change）は 1 提案 = 1 列の単一 {field, to} 形で渡す"
            "（ADR-013。複数列を直したいときは最も効く 1 つに絞る）。"
        ),
        parameters=_schema(SubmitJournalArgs),
        handler=handlers.handle_submit_journal,
        min_phase=3,
    ),
    # --- Phase 4（Stock Dossier）---
    "get_dossier": ToolDef(
        name="get_dossier",
        description=(
            "指定銘柄の既存ドシエ（定性調査レポートの markdown・key_facts）と"
            "ソース台帳（要約＋URL・本文なし）を取得する。"
            "銘柄の物語・直近トピックを語る前に、まず既存の調査結果を読むときに呼ぶ。"
            "未調査なら summary_md は空で返る（その場合は investigate_stock で調査する）。"
        ),
        parameters=_schema(GetDossierArgs),
        handler=handlers.handle_get_dossier,
        min_phase=4,
    ),
    "investigate_stock": ToolDef(
        name="investigate_stock",
        description=(
            "指定銘柄を今すぐ調査し、ドシエ（定性調査レポート）を生成・更新する。"
            "ニュース取得→要約→保存を行い、最新の summary_md / key_facts と"
            "追加したソース件数（n_sources_added）を返す。"
            "「この銘柄を調査して」と頼まれたとき・既存ドシエが古い/無いときに呼ぶ。"
        ),
        parameters=_schema(InvestigateStockArgs),
        handler=handlers.handle_investigate_stock,
        min_phase=4,
    ),
    "fetch_news": ToolDef(
        name="fetch_news",
        description=(
            "指定銘柄の直近ニュース記事（要約＋URL）を取得する。since で発行下限日を絞れる。"
            "ドシエ更新の素材や直近の話題を確認したいときに呼ぶ（本文は返さず要約のみ）。"
        ),
        parameters=_schema(FetchNewsArgs),
        handler=handlers.handle_fetch_news,
        min_phase=4,
    ),
}


def openai_tools(available_phase: int = CURRENT_PHASE) -> list[dict[str, object]]:
    """min_phase <= available_phase の Tool を OpenAI tools 配列にして返す（spec §4.1）。

    各要素は `{"type": "function", "function": {name, description, parameters}}` 形。
    Phase ゲート: まだ実装されていない Tool を LLM に見せない。
    """
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in REGISTRY.values()
        if t.min_phase <= available_phase
    ]
