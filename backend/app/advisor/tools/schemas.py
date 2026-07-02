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


class GetTrackRecordArgs(_ToolArgs):
    """get_track_record の引数（ADR-077）。すべて任意（省略時は全 source/kind/horizon を集計）。"""

    source: str | None = None  # 'nightly'/'chat'
    kind: str | None = None  # 'buy'/'sell'/'notable'
    horizon: int | None = None  # 20/60（営業日）
    recent_limit: int | None = None  # 直近個別 outcome の件数（既定 10）


class SearchJudgmentsArgs(_ToolArgs):
    """search_judgments の引数（ADR-078）。query 必須・他は任意。"""

    query: str  # 検索語（trigram・3 文字以上）
    code: str | None = None  # 銘柄コードで絞る（proposal/notable のみ・exact）
    origin: str | None = None  # 'journal'/'proposal'/'notable'
    limit: int | None = None  # 返す件数（既定 8）


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


class GetValuationArgs(_ToolArgs):
    """get_valuation の引数（ADR-048）。指定銘柄のバリュエーション事実を取得する。"""

    code: str


class ScreenValuationArgs(_ToolArgs):
    """screen_valuation の criteria（ADR-048・キーは内部列名）。

    バリュエーション/ファンダ指標で割安・優良銘柄を絞り込む。すべて任意（省略時は無条件）。
    しきい値は AI が手法カード（docs/methods/valuation.md）の作法を見て explicit に渡す
    （コードは破壊的ゲートを持たない＝ADR-014/026/031）。比率系は 0..1、PER/PBR は倍率。
    """

    per_min: float | None = None
    per_max: float | None = None
    pbr_min: float | None = None
    pbr_max: float | None = None
    roe_min: float | None = None
    roe_max: float | None = None
    dividend_yield_min: float | None = None  # 0..1
    operating_margin_min: float | None = None  # 0..1
    net_margin_min: float | None = None  # 0..1
    revenue_growth_yoy_min: float | None = None  # 0..1 基準の比率
    profit_growth_yoy_min: float | None = None
    market_cap_min: float | None = None  # 円
    market_cap_max: float | None = None  # 円（清原式の小型株＝500 億円未満 等の上限に使う）
    # 清原式ネットキャッシュ（ADR-079）。net_cash_ratio_min≥1 で「時価総額を手元現金が上回る」割安。
    net_cash_ratio_min: float | None = None  # net_cash / 時価総額（≥1 が清原式の目安）
    net_cash_min: float | None = None  # ネットキャッシュ絶対額の下限（>0 で実質無借金に絞る等）
    sector33_code: str | None = None
    per_sector_pctile_max: float | None = None  # 業種内で安い割合（0..1）
    market_cap_rank_max: int | None = None  # 時価総額 上位 N
    exclude_etf: bool | None = None
    sort_by: str | None = None  # per/pbr/roe/market_cap/dividend_yield/net_cash_ratio 等
    sort_dir: Literal["asc", "desc"] | None = None
    limit: int | None = None


class GetUsValuationArgs(_ToolArgs):
    """get_us_valuation の引数（Phase 7(B-1)・ADR-039/048/055）。

    GetValuationArgs（日本株）のミラー。識別子は米株流儀の `symbol`（ティッカー）に置換する
    （日本株は `code`・市場分離＝ADR-031）。指定銘柄のバリュエーション事実を取得する。
    """

    symbol: str


class ScreenUsValuationArgs(_ToolArgs):
    """screen_us_valuation の criteria（Phase 7(B-1)・ADR-039/048/055・キーは内部列名）。

    ScreenValuationArgs（日本株）のミラー。バリュエーション/ファンダ指標で米国株を絞り込む。
    日本株との差分は ① 業種が `sector33_code`→`gics_sector`（Yahoo `.info.sector`≒GICS 11 分類の
    文字列・ADR-055）② 業種内パーセンタイル上限が `per_sector_pctile_max`→`gics_sector_pctile_max`
    （GICS 内で安い割合・0..1）。それ以外（per/pbr/roe/利益率/配当利回り/YoY 成長率のレンジ・
    時価総額順位・sort・limit）は日本株と同型。すべて任意（省略時は無条件）。しきい値は AI が手法
    カードの作法を見て explicit に渡す（コードは破壊的ゲートを持たない＝ADR-014/026/031）。
    """

    per_min: float | None = None
    per_max: float | None = None
    pbr_min: float | None = None
    pbr_max: float | None = None
    roe_min: float | None = None
    roe_max: float | None = None
    dividend_yield_min: float | None = None  # 0..1
    operating_margin_min: float | None = None  # 0..1
    net_margin_min: float | None = None  # 0..1
    revenue_growth_yoy_min: float | None = None  # 0..1 基準の比率
    profit_growth_yoy_min: float | None = None
    market_cap_min: float | None = None  # USD
    market_cap_max: float | None = None  # USD（清原式の小型株上限に使う）
    # 清原式ネットキャッシュ（ADR-079・US はフル式）。net_cash_ratio_min≥1 で割安。
    net_cash_ratio_min: float | None = None  # net_cash / 時価総額（≥1 が清原式の目安）
    net_cash_min: float | None = None  # ネットキャッシュ絶対額の下限（USD）
    gics_sector: str | None = None  # Yahoo .info.sector（GICS 相当の文字列・ADR-055）
    gics_sector_pctile_max: float | None = None  # 業種内で安い割合（0..1）
    market_cap_rank_max: int | None = None  # 時価総額 上位 N
    exclude_etf: bool | None = None
    sort_by: str | None = None  # per/pbr/roe/market_cap/dividend_yield/net_cash_ratio 等
    sort_dir: Literal["asc", "desc"] | None = None
    limit: int | None = None


class GetFundHoldingsArgs(_ToolArgs):
    """get_fund_holdings の引数（ADR-054）。省略時は先頭ポートフォリオ（portfolio_id 既定 1）。"""

    portfolio_id: int | None = None


class GetUsHoldingsArgs(_ToolArgs):
    """get_us_holdings の引数（ADR-057）。引数なし（単一ユーザー・global 保有・ADR-001）。"""


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


class GetNewsContextArgs(_ToolArgs):
    """get_news_context の引数（ADR-044）。

    指定銘柄の3層ニュース文脈（銘柄＋セクター＋市況）をまとめて取る。引数は code のみ
    （セクター/市況層はコードから解決して構造的に揃える＝ADR-044）。
    """

    code: str


class SearchNewsArgs(_ToolArgs):
    """search_news の引数（ADR-045・ニュース意味検索）。

    貯めた統合コーパスを意味（embedding 余弦距離）で過去横断検索する。query は必須、ほかは任意の
    絞り込み。level は階層タグ（stock/sector/market/user）、since/until は発行日範囲 'YYYY-MM-DD'、
    limit は件数上限。任意引数の "None"/"null" 等は _ToolArgs が実 None に正規化する。
    """

    query: str
    level: str | None = None
    code: str | None = None
    sector17_code: str | None = None
    since: str | None = None
    until: str | None = None
    limit: int | None = None


class SearchCardsArgs(_ToolArgs):
    """search_cards の引数（ADR-062・追補で code 対応）。

    AI アドバイザーの知識ベース（知識カード）を検索する。query は必須（code 指定時は無視される）、
    level（market/sector/stock/general）は任意の構造フィルタ、limit は件数上限（既定 5）。
    code を渡すと**その銘柄の active ノートを exact-match で全返し**（意味検索でなく完全一致・
    ADR-062 追補）。market は JP/US（省略時 code だけで一致）。任意引数の "None"/"null" は
    _ToolArgs が実 None に正規化する。
    """

    query: str
    level: str | None = None
    code: str | None = None
    market: str | None = None
    limit: int | None = None


class GetMethodCardArgs(_ToolArgs):
    """get_method_card の引数（ADR-075・ADR-079・手法カードのオンデマンド取得）。

    シグナル/手法の正典的な解釈（何を測る・スコアの読み方・限界）をリポジトリの手法カードから返す。
    `signal_type` を渡すと本文を、省略すると登録カードの一覧（キー＋kind＋summary）を返す。
    渡すキーは signal 種＝signal_type（lead_lag / ai_alpha / stealth_accum 等を解釈する前に）、
    strategy 種＝手法スラッグ（例: 清原式ネットキャッシュの `net_cash_value`・能動 screen の前に）。
    """

    signal_type: str | None = None


class ProposeCardArgs(_ToolArgs):
    """propose_card の引数（ADR-062 追補・チャットからカードを承認制で起票）。

    AI が会話から知識カードを起票する。body 必須、ほかは任意（title/when_to_apply/level/source）。
    起票は draft で、人間が /cards で active 化する（本番助言に効く操作は人間が最終承認・ADR-009）。
    code を渡すと**特定銘柄のノート**（アノマリー等）になる＝会話で論じている銘柄の code を渡す
    （社名からの推測でなく tool 文脈由来の grounded な値を・未知 code は起票せず drop・
    market は JP/US・
    ADR-062 追補）。code 付きは level='stock' に確定する（起票側で矯正）。
    """

    body: str
    title: str | None = None
    when_to_apply: str | None = None
    level: str | None = None
    source: str | None = None
    code: str | None = None
    market: str | None = None


class AdjustCardWeightArgs(_ToolArgs):
    """adjust_card_weight の引数（ADR-062 追補・weight 変更を承認制で提案）。

    既存カードの重要度 weight（>0）を変える提案を起票する（古い/信頼度低を下げる等）。card_id は
    search_cards の結果の id。承認するまで反映しない（proposals 経由・後で直接化しやすい設計）。
    """

    card_id: int
    weight: float
    reason: str


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


class ListThemesArgs(_ToolArgs):
    """list_themes の引数（ADR-050 改訂・テーマタグ段階 A）。

    limit 省略時は全件。テーマ語彙の discovery 用なので、まず全体を眺める使い方を既定にする
    （handler が n_stocks 降順に並べるため、limit 指定時は「所属の多い順の先頭 N 件」になる）。
    """

    limit: int | None = None


class GetStockThemesArgs(_ToolArgs):
    """get_stock_themes の引数（ADR-050 改訂）。

    market='JP' のとき code は J-Quants 5 桁コード（例 72030）、market='US' のとき code は
    ティッカー（例 AAPL）。stock_themes は market+code が同一性（cross-FK なし・ADR-050）。
    """

    market: Literal["JP", "US"] = Field(
        description="市場。JP=日本株（code は 5 桁コード）/ US=米国株（code はティッカー）。"
    )
    code: str = Field(description="JP は 5 桁コード（例 72030）、US はティッカー（例 AAPL）。")


class ScreenByThemeArgs(_ToolArgs):
    """screen_by_theme の引数（ADR-050 改訂）。

    theme は list_themes が返す canonical 名と exact 一致（当て推量しない）。業種絞りは
    S17（JP の TOPIX-17）と GICS（US の Yahoo `.info.sector` 英語ラベル）が**別体系**
    （ADR-053）なので 1 引数に混載せず分離する。段階 A のタガーは US のみ稼働で JP 行が
    無いため、sector17_code は前方互換の予約引数（段階 B/C で効く）。
    """

    theme: str = Field(
        description="テーマ名（list_themes の canonical 名と exact 一致。当て推量しない）。"
    )
    market: Literal["JP", "US"] | None = Field(
        default=None, description="市場の絞り込み（省略時は JP＋US 横断）。"
    )
    sector17_code: str | None = Field(
        default=None,
        description="JP の TOPIX-17 業種コード（S17）での絞り込み。US 行には効かない。",
    )
    gics_sector: str | None = Field(
        default=None,
        description="US の GICS 相当業種（英語ラベル・例 Technology）での絞り込み。"
        "JP 行には効かない。",
    )
    limit: int = Field(default=50, ge=1, le=200)


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


class ProposeTradeArgs(_ToolArgs):
    """propose_trade の引数（ADR-052・ニュース起点の売買アイデア起票）。

    提示専用（ADR-009）。方向（action）と銘柄（code）と根拠（reason）だけを受け、株数・金額・
    目標価格などの数値は持たない（数値は AI に計算させない＝ADR-014）。code は JP 5 桁または
    US ティッカー。受理側（persist）が stocks→us_stocks で解決し、未知コードは起票しない。
    """

    action: Literal["buy", "sell"] = Field(description="売買の方向（buy=買い / sell=売り）。")
    code: str = Field(description="JP は 5 桁コード（例 72030）、US はティッカー（例 AAPL）。")
    reason: str = Field(description="ニュース起点の根拠（なぜ買い/売りかの説明・数値は含めない）。")


class GetNotableCandidatesArgs(_ToolArgs):
    """get_notable_candidates の引数（ADR-067・引数なし）。

    合流ゲートで組んだ「今日の注目候補」を返すだけなので引数は無い（get_lead_lag と同じ空モデル）。
    """


class NotablePickArg(_ToolArgs):
    """submit_notable_stocks の 1 件（ADR-067）。"""

    code: str = Field(
        description="注目銘柄の JP 5 桁コード（例 72030）。候補集合のコードから選ぶ。"
    )
    reason: str = Field(
        description="なぜ注目かの短い理由（材料の重なりや文脈・数値は Tool の事実に基づく）。"
    )


class SubmitNotableStocksArgs(_ToolArgs):
    """submit_notable_stocks の引数（ADR-067・夜の分析AI の注目選別受け）。

    候補集合（get_notable_candidates／プロンプト注入）から「総合的に注目すべき銘柄だけ」を選び、
    picks（code＋reason の配列）で提出する。本当に無ければ空配列でよい（毎回無理に出さない）。
    受理側（persist）が JP コードを解決し、未知コードは drop する（ADR-014/018）。
    """

    picks: list[NotablePickArg] = Field(
        default_factory=list,
        description="注目銘柄の配列（各 {code, reason}）。関連度が高い順に並べる。無ければ空配列。",
    )


class WatchlistCandidateArg(_ToolArgs):
    """propose_watchlist の 1 候補（ADR-080）。code=JP 5 桁・reason=任意（追加時 note へ焼く）。"""

    code: str = Field(
        description="ウォッチ候補の JP 5 桁コード（例 37120）。提示した銘柄群から選ぶ。"
    )
    reason: str = Field(
        default="",
        description="なぜウォッチ候補かの短い理由（任意・数値は含めない・追加時に note へ焼く）。",
    )


class ProposeWatchlistArgs(_ToolArgs):
    """propose_watchlist の引数（ADR-080・厳選ウォッチ候補の提示）。

    厳選ショートリストを提示するときだけ呼ぶ（雑談での言及では呼ばない）。候補（code＋reason）を
    構造化して UI に渡すだけで、**watchlist への追加はしない**（追加はユーザーが UI で行う＝ADR-009
    の承認制精神）。受理側（router）が JP コードを解決し、未知コードは drop する（ADR-014/018）。
    """

    candidates: list[WatchlistCandidateArg] = Field(
        default_factory=list,
        description="ウォッチ候補の配列（各 {code, reason}）。本命→次点の順。無ければ空配列。",
    )
