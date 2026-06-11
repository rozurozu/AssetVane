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
    GetFundHoldingsArgs,
    GetGeneralNewsArgs,
    GetIndicatorsArgs,
    GetLeadLagArgs,
    GetNewsContextArgs,
    GetPortfolioMetricsArgs,
    GetSignalsArgs,
    GetStockThemesArgs,
    GetUsHoldingsArgs,
    GetUsValuationArgs,
    GetValuationArgs,
    InvestigateStockArgs,
    ListThemesArgs,
    OptimizePortfolioArgs,
    ProposeTradeArgs,
    ScreenByThemeArgs,
    ScreenStocksArgs,
    ScreenUsValuationArgs,
    ScreenValuationArgs,
    SearchNewsArgs,
    SubmitJournalArgs,
)

# 現在の投入フェーズ（段2 の dispatch が openai_tools(phase) に渡す）。
# Phase 4（Stock Dossier）＋ADR-034（一般ニュース）に加え、Phase 7（日米業種リードラグ・
# SIG-FIN-036-13）まで実装済み。これを 7 にすることで min_phase=4 の Tool（get_dossier /
# investigate_stock / fetch_news / get_general_news）と min_phase=7 の get_lead_lag が
# チャット・夜の分析AI に露出する。
CURRENT_PHASE: int = 7


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
            "指定銘柄の直近の財務（売上・営業利益・純利益・EPS・BPS）の生時系列を取得する。"
            "決算の推移そのものを見たいときに呼ぶ（派生比率は get_valuation）。"
        ),
        parameters=_schema(GetFinancialsArgs),
        handler=handlers.handle_get_financials,
        min_phase=2,
    ),
    "get_valuation": ToolDef(
        name="get_valuation",
        description=(
            "指定銘柄のバリュエーション/ファンダ事実を取得する（PER・PBR・ROE・営業/純利益率・"
            "配当利回り・売上/利益/EPS の YoY 成長率・時価総額と業種内パーセンタイル/順位）。"
            "割安・割高や収益性・成長性を語る前に必ず呼ぶ。"
            "数値は事実のみで判定は付かない＝PER 単体で結論せず、成長率・業種比較と併せて解釈する"
            "（手法カード『バリュエーション』参照）。日本株（market:JP・JPY）。"
        ),
        parameters=_schema(GetValuationArgs),
        handler=handlers.handle_get_valuation,
        min_phase=2,
    ),
    "screen_valuation": ToolDef(
        name="screen_valuation",
        description=(
            "バリュエーション/ファンダ条件で全銘柄を絞り込み、候補を指標付きで列挙する"
            "（per/pbr/roe/利益率/配当利回り/YoY 成長率のレンジ・業種・時価総額順位など）。"
            "『割安な銘柄を探して』『高 ROE で割安を探して』等の候補探しのときに呼ぶ。"
            "しきい値は手法カードの作法に基づき自分で criteria に渡す（例: 割安≈PER<15 や PBR<1 を"
            "起点に、成長率・業種で調整）。日本株のみ・ランクは市場内（market:JP）。"
        ),
        parameters=_schema(ScreenValuationArgs),
        handler=handlers.handle_screen_valuation,
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
    "get_fund_holdings": ToolDef(
        name="get_fund_holdings",
        description=(
            "保有投資信託（投信）の口数・取得単価・最新基準価額（NAV）・評価額・含み損益・"
            "投信内ウェイトを取得する。投信の保有状況・含み損益を論じる前に呼ぶ"
            "（基準価額は 10,000 口あたりの円・ADR-054）。"
        ),
        parameters=_schema(GetFundHoldingsArgs),
        handler=handlers.handle_get_fund_holdings,
        min_phase=4,
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
    "get_news_context": ToolDef(
        name="get_news_context",
        description=(
            "指定銘柄の3層ニュース文脈（①銘柄自身 ②そのセクター ③市況/マクロ）を"
            "貯めた台帳から一括取得する（要約＋URL・読み取り専用・ADR-044）。"
            "『この銘柄どう？』のように銘柄を語る前に、まずこれで 3 層の文脈をまとめて取る"
            "（市況層が必ず添うので、銘柄ニュースだけでは見えないマクロ要因を取りこぼさない）。"
            "新規にネット取得はしない＝最新を取り込みたいときは fetch_news を使う。"
        ),
        parameters=_schema(GetNewsContextArgs),
        handler=handlers.handle_get_news_context,
        min_phase=4,
    ),
    "propose_trade": ToolDef(
        name="propose_trade",
        description=(
            "ニュース起点の買い/売りアイデアを承認制の提案として起票する（提示専用・ADR-052/009）。"
            "get_news_context などで根拠（3 層の文脈）を掴んだうえで、明確な買い/売り材料が"
            "あるときだけ呼ぶ（無ければ呼ばなくてよい・毎回出さない）。"
            "渡すのは方向（action=buy/sell）・銘柄（code＝JP 5 桁または US ティッカー）・根拠"
            "（reason）だけ。株数・金額・目標価格などの数値は出さない（サイズは別途最適化に委ねる"
            "＝ADR-014）。承認しても発注はしない（ユーザーが手動約定）。"
        ),
        parameters=_schema(ProposeTradeArgs),
        handler=handlers.handle_propose_trade,
        min_phase=4,
    ),
    "fetch_news": ToolDef(
        name="fetch_news",
        description=(
            "指定銘柄のニュースを**今すぐ新規にネット取得**して要約＋URL を返す"
            "（取り込みも伴う・since で発行下限日を絞れる）。"
            "貯めた文脈で足りず最新の話題を能動的に取りに行きたいとき・ドシエ更新の素材集めに呼ぶ。"
            "既存の文脈で足りるなら get_news_context（読み取り専用）を先に。本文は返さず要約のみ。"
        ),
        parameters=_schema(FetchNewsArgs),
        handler=handlers.handle_fetch_news,
        min_phase=4,
    ),
    "get_general_news": ToolDef(
        name="get_general_news",
        description=(
            "**市況だけ**（特定銘柄に紐づかない一般ニュース＝市況・マクロ経済・世界情勢）を"
            "カテゴリ別の見出し＋要約＋URL で取得する（code 不要・ADR-034/044）。"
            "個別銘柄を絡めず全体観・マクロ文脈だけを語るときに呼ぶ。"
            "銘柄に紐づく文脈が要るなら get_news_context（市況層も含めて 3 層返す）を使う。"
        ),
        parameters=_schema(GetGeneralNewsArgs),
        handler=handlers.handle_get_general_news,
        min_phase=4,
    ),
    "search_news": ToolDef(
        name="search_news",
        description=(
            "貯めた台帳を**意味で過去横断検索**する（embedding 余弦距離・ADR-045）。"
            "直近窓を越えて『あの利上げ観測の話』『先週の決算』のような曖昧・過去のクエリに使う。"
            "level/code/sector17_code・発行日範囲（since/until）で絞れる。"
            "最新を新規取得したいときは fetch_news。"
            "3 層の構造文脈（銘柄＋セクター＋市況）が要るときは get_news_context。"
        ),
        parameters=_schema(SearchNewsArgs),
        handler=handlers.handle_search_news,
        min_phase=4,
    ),
    # --- Phase 7（日米業種リードラグ・SIG-FIN-036-13）---
    "get_lead_lag": ToolDef(
        name="get_lead_lag",
        description=(
            "日米業種リードラグ・モデル（米国当日の業種ショックから日本業種の翌営業日の"
            "相対的な強弱を予測）の最新ランキング（17 業種・0..1 スコア）と検証指標"
            "（IC・ヒット率）を取得する。明日どの日本業種が相対的に強い/弱いか、"
            "業種ローテーションを語るときに呼ぶ。"
        ),
        parameters=_schema(GetLeadLagArgs),
        handler=handlers.handle_get_lead_lag,
        min_phase=7,
    ),
    # --- Phase 7(B-1)（米国株バリュエーション・ADR-039/048/055）---
    "get_us_valuation": ToolDef(
        name="get_us_valuation",
        description=(
            "指定**米国株**（ティッカー symbol）のバリュエーション/ファンダ事実を取得する"
            "（PER・PBR・ROE・営業/純利益率・配当利回り・売上/利益/EPS の YoY 成長率・時価総額と"
            "GICS 業種内パーセンタイル/順位）。割安・割高や収益性・成長性を語る前に必ず呼ぶ。"
            "数値は事実のみで判定は付かない＝PER 単体で結論せず、成長率・業種比較と併せて解釈する"
            "（手法カード『バリュエーション』参照）。米国株のバリュエーション/ファンダ事実取得"
            "（USD・GICS／market:US・currency:USD）。日本株は get_valuation（JPY）。"
        ),
        parameters=_schema(GetUsValuationArgs),
        handler=handlers.handle_get_us_valuation,
        min_phase=7,
    ),
    "screen_us_valuation": ToolDef(
        name="screen_us_valuation",
        description=(
            "バリュエーション/ファンダ条件で**米国株**を絞り込み、候補を指標付きで列挙する"
            "（per/pbr/roe/利益率/配当利回り/YoY 成長率のレンジ・GICS 業種・時価総額順位など）。"
            "『割安な米国株を探して』『高 ROE で割安を探して』等の候補探しのときに呼ぶ。"
            "しきい値は手法カードの作法に基づき自分で criteria に渡す（例: 割安≈PER<15 や PBR<1 を"
            "起点に、成長率・業種で調整）。米国株のバリュエーション/ファンダ事実取得（USD・GICS）・"
            "ランクは市場内（market:US・currency:USD）。日本株は screen_valuation（JPY）。"
        ),
        parameters=_schema(ScreenUsValuationArgs),
        handler=handlers.handle_screen_us_valuation,
        min_phase=7,
    ),
    # --- ADR-050 段階A（テーマタグ）---
    "list_themes": ToolDef(
        name="list_themes",
        description=(
            "テーマ語彙の目録（canonical テーマ名・所属銘柄数 n_stocks・重複候補フラグ"
            " near_duplicate_of）を取得する。テーマ語彙の discovery 用＝テーマ名を当て推量せず、"
            "get_stock_themes / screen_by_theme の前に必ずここで実在の語彙を確認する"
            "（screen_by_theme はここの canonical 名との exact 一致でしか引けない）。"
            "near_duplicate_of が付くテーマは近接の既存テーマの重複候補（自動マージはされない）。"
        ),
        parameters=_schema(ListThemesArgs),
        handler=handlers.handle_list_themes,
        min_phase=7,
    ),
    "get_stock_themes": ToolDef(
        name="get_stock_themes",
        description=(
            "指定銘柄（market: JP=5桁コード / US=ティッカー）に付いたテーマタグの一覧を取得する。"
            "タグは実在の事業説明テキスト（米株 longBusinessSummary・JP は EDINET 有報"
            "「事業の内容」）に grounded に付与されたもの＝社名からの名前推測ではない"
            "（ADR-050）。銘柄のテーマ所属・物色文脈を語る前に呼ぶ。未タグは found:false で返る。"
        ),
        parameters=_schema(GetStockThemesArgs),
        handler=handlers.handle_get_stock_themes,
        min_phase=7,
    ),
    "screen_by_theme": ToolDef(
        name="screen_by_theme",
        description=(
            "テーマ株スクリーニング＝指定テーマ（list_themes の canonical 名・exact 一致）に"
            "所属する銘柄を列挙する。market（JP/US）・sector17_code（JP の TOPIX-17）・"
            "gics_sector（US の GICS 相当英語ラベル）で絞れる（業種は日米で別体系＝ADR-053）。"
            "競合比較は専用 Tool がないため get_stock_themes→screen_by_theme（同セクター絞り）の"
            "合成で行う。戻りはテーマ所属の事実のみで数値なし＝バリュエーション等の数値が"
            "要るときは get_us_valuation / get_valuation 等を併用する（ADR-014）。"
        ),
        parameters=_schema(ScreenByThemeArgs),
        handler=handlers.handle_screen_by_theme,
        min_phase=7,
    ),
    # --- Phase 7(B-2)（米株保有・FX 換算・ADR-039/057）---
    "get_us_holdings": ToolDef(
        name="get_us_holdings",
        description=(
            "保有米国株（ティッカー symbol）の株数・取得単価（USD）・最新終値（USD）・"
            "USDJPY レート・JPY 評価額・JPY 含み損益（為替損益込み）・米株内ウェイトを取得する。"
            "米株の保有状況・含み損益・日米横断の資産バランスを論じる前に呼ぶ"
            "（FX/終値未取得の銘柄は評価額 null で返る・捏造しない・ADR-014/057）。"
        ),
        parameters=_schema(GetUsHoldingsArgs),
        handler=handlers.handle_get_us_holdings,
        min_phase=7,
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
