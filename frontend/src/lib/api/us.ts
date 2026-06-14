import { del, getJSON, postJSON, putJSON } from "./_client";

// --- Phase 7(B-1) 米国株スクリーナー（ADR-039(B)/ADR-055・提示専用）---
// backend routers/us_stocks.py の Pydantic（UsScreenCriteria/UsScreenRow/UsStockDetail/UsQuote）と 1:1。
// 日本株（ScreenCriteria/ScreenRow）のミラーだが、市場分離（ADR-031）で別型・別関数。
// code→symbol・sector33_code→gics_sector に読み替える。数値は USD（ドル）。
// 比率系（dividend_yield/roe/各 margin/各 growth_yoy/各 pctile）は内部 0..1（UI でのみ ×100・ADR-008）。

/** 米株スクリーニング条件（UsScreenCriteria と 1:1）。全フィールド任意。市場跨ぎはしない。 */
export type UsScreenCriteria = {
  per_min?: number;
  per_max?: number;
  pbr_min?: number;
  pbr_max?: number;
  market_cap_min?: number; // USD
  market_cap_max?: number;
  dividend_yield_min?: number; // 0..1（UI で ×100）
  dividend_yield_max?: number;
  roe_min?: number; // 0..1
  roe_max?: number;
  operating_margin_min?: number; // 0..1
  operating_margin_max?: number;
  net_margin_min?: number; // 0..1
  net_margin_max?: number;
  revenue_growth_yoy_min?: number; // 0..1 基準の比率
  revenue_growth_yoy_max?: number;
  op_growth_yoy_min?: number;
  op_growth_yoy_max?: number;
  profit_growth_yoy_min?: number;
  profit_growth_yoy_max?: number;
  eps_growth_yoy_min?: number;
  eps_growth_yoy_max?: number;
  gics_sector?: string; // GICS 相当セクター（Yahoo 英語ラベルの完全一致）
  exclude_etf?: boolean;
  gics_sector_pctile_max?: number; // GICS 内で安い割合 0..1
  market_cap_rank_max?: number; // 時価総額 上位 N
  sort_by?:
    | "per"
    | "pbr"
    | "market_cap"
    | "dividend_yield"
    | "roe"
    | "operating_margin"
    | "net_margin"
    | "revenue_growth_yoy"
    | "op_growth_yoy"
    | "profit_growth_yoy"
    | "eps_growth_yoy"
    | "gics_sector_pctile"
    | "market_cap_rank"
    | "symbol";
  sort_dir?: "asc" | "desc";
  limit?: number;
  offset?: number;
};

/** 米株スクリーナー 1 行（us_valuation_snapshots × us_stocks ＋ 読み取り時ランク・UsScreenRow と 1:1）。 */
export type UsScreenRow = {
  symbol: string;
  company_name: string | null;
  gics_sector: string | null;
  industry: string | null;
  is_etf: number | null;
  as_of_date: string | null;
  close: number | null; // USD
  eps: number | null;
  bps: number | null;
  dividend_per_share: number | null;
  per: number | null;
  pbr: number | null;
  market_cap: number | null; // USD
  dividend_yield: number | null; // 0..1
  roe: number | null; // 0..1
  operating_margin: number | null; // 0..1
  net_margin: number | null; // 0..1
  revenue_growth_yoy: number | null; // 0..1 基準の比率
  op_growth_yoy: number | null;
  profit_growth_yoy: number | null;
  eps_growth_yoy: number | null;
  gics_sector_pctile: number | null; // 0..1
  market_cap_rank: number | null;
};

/** 米株マスタ 1 件（GET /us-stocks・UsStock と 1:1）。 */
export type UsStock = {
  symbol: string;
  company_name: string | null;
  gics_sector: string | null;
  industry: string | null;
  is_etf: number | null;
};

/** 米株 1 銘柄のバリュエーション事実（UsValuationSnapshot と 1:1・未焼成は null で返る）。 */
export type UsValuationSnapshot = {
  symbol: string;
  company_name: string | null;
  gics_sector: string | null;
  industry: string | null;
  is_etf: number | null;
  as_of_date: string | null;
  close: number | null; // USD
  eps: number | null;
  bps: number | null;
  dividend_per_share: number | null;
  per: number | null;
  pbr: number | null;
  market_cap: number | null; // USD
  dividend_yield: number | null; // 0..1
  roe: number | null; // 0..1
  operating_margin: number | null; // 0..1
  net_margin: number | null; // 0..1
  revenue_growth_yoy: number | null; // 0..1
  op_growth_yoy: number | null;
  profit_growth_yoy: number | null;
  eps_growth_yoy: number | null;
  gics_sector_pctile: number | null; // 0..1
  market_cap_rank: number | null;
};

/** 米株詳細（GET /us-stocks/{symbol}・UsStockDetail と 1:1）。マスタは取れたが未焼成なら valuation=null。 */
export type UsStockDetail = {
  symbol: string;
  company_name: string | null;
  gics_sector: string | null;
  industry: string | null;
  is_etf: number | null;
  valuation: UsValuationSnapshot | null;
};

/** 米株日足 1 本（GET /us-quotes/{symbol}・UsQuote と 1:1）。Quote と同型だが市場分離で別型。 */
export type UsQuote = {
  date: string; // 'YYYY-MM-DD'
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  volume: number | null;
  adj_close: number | null;
};

// --- Phase 7(B-2) 米国株保有管理（ADR-055・GET /us-holdings・GET/POST/PUT/DELETE /us-transactions）---
// backend routers/us_holdings.py の Pydantic（UsHoldingOut / UsTransactionOut）と 1:1。
// price は USD・fx_rate は USDJPY レート（省略時はサーバが約定日レートを解決）。
// 評価系（market_value_jpy 等）は FX 未取得 or close 未取得のとき null になる。
// weight は米株保有内の比率（0..1・UI で ×100）。

/** 米株保有 1 件（UsHoldingOut と 1:1・評価系は null 可）。 */
export type UsHolding = {
  id: number;
  symbol: string;
  company_name: string | null;
  gics_sector: string | null;
  shares: number;
  avg_cost: number | null; // 平均取得単価（USD）
  avg_cost_jpy: number | null; // 平均取得単価（JPY 換算）
  last_close: number | null; // 最終終値（USD）
  close_date: string | null; // 終値基準日（YYYY-MM-DD）
  fx_rate: number | null; // 直近 USDJPY レート
  market_value_jpy: number | null; // 評価額（JPY・FX×close×shares）
  cost_jpy: number | null; // 取得コスト（JPY 換算）
  unrealized_pnl_jpy: number | null; // 含み損益（JPY）
  weight: number | null; // 米株内比率（0..1・UI で ×100）
};

/** 米株取引 1 件（UsTransactionOut と 1:1）。 */
export type UsTransaction = {
  id: number;
  symbol: string;
  company_name: string | null;
  side: "buy" | "sell";
  shares: number;
  price: number; // 約定単価（USD）
  fee: number | null; // 手数料（USD・任意）
  traded_at: string; // 約定日（YYYY-MM-DD）
  fx_rate: number; // 約定時 USDJPY（UsTransactionOut は常にサーバ解決済み＝非 null）
  note: string | null;
};

/** `POST/PUT /us-transactions` リクエスト（UsTransactionIn と 1:1）。
 * fx_rate 省略時はサーバが約定日レートを解決。未取得なら 400「FX レート未取得」が throw される。 */
export type UsTransactionInput = {
  symbol: string;
  side: "buy" | "sell";
  shares: number;
  price: number; // USD
  fee?: number | null;
  traded_at: string; // YYYY-MM-DD
  fx_rate?: number | null; // USDJPY（省略可・サーバ解決）
  note?: string | null;
};

/** 米株スクリーニング（GET /us-stocks/screen・ADR-031/055）。criteria を query にして読み取り時計算結果を得る。
 * boolean は true のときだけ送る（既定 false は送らない・日本株 screenStocks と同じ流儀）。 */
export function screenUsStocks(
  criteria: UsScreenCriteria,
  signal?: AbortSignal,
): Promise<UsScreenRow[]> {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(criteria)) {
    if (v === undefined || v === null || v === "") continue;
    if (typeof v === "boolean") {
      if (v) p.set(k, "true");
    } else {
      p.set(k, String(v));
    }
  }
  const qs = p.toString();
  return getJSON<UsScreenRow[]>(`/us-stocks/screen${qs ? `?${qs}` : ""}`, signal);
}

/** 米株詳細（マスタ＋valuation snapshot）を取得（GET /us-stocks/{symbol}）。未取得は 404。 */
export function getUsStock(symbol: string, signal?: AbortSignal): Promise<UsStockDetail> {
  return getJSON<UsStockDetail>(`/us-stocks/${encodeURIComponent(symbol)}`, signal);
}

/** 米株チャート用の日足（GET /us-quotes/{symbol}）。date 昇順。 */
export function getUsQuotes(
  symbol: string,
  from?: string,
  to?: string,
  signal?: AbortSignal,
): Promise<UsQuote[]> {
  const params = new URLSearchParams();
  if (from) params.set("from", from);
  if (to) params.set("to", to);
  const qs = params.toString();
  return getJSON<UsQuote[]>(
    `/us-quotes/${encodeURIComponent(symbol)}${qs ? `?${qs}` : ""}`,
    signal,
  );
}

// --- Phase 7(B-2) 米株保有 API 関数（ADR-055・POST/DELETE は UsHolding[] を返す）---
// 投信（ADR-054）と同じ流儀。失敗は detail を載せた ApiError を throw。

/** 米株保有一覧（GET /us-holdings）。評価系は FX 未取得 or close 未取得のとき null（ADR-014）。 */
export function getUsHoldings(signal?: AbortSignal): Promise<UsHolding[]> {
  return getJSON<UsHolding[]>("/us-holdings", signal);
}

/** 米株取引履歴一覧（GET /us-transactions）。新しい順。 */
export function getUsTransactions(signal?: AbortSignal): Promise<UsTransaction[]> {
  return getJSON<UsTransaction[]>("/us-transactions", signal);
}

/** 米株取引を記録（POST /us-transactions）。更新後の全保有 UsHolding[] を返す。
 * fx_rate 省略時はサーバが約定日レートを解決（未取得なら 400「FX レート未取得」が ApiError で throw）。 */
export function postUsTransaction(input: UsTransactionInput): Promise<UsHolding[]> {
  return postJSON<UsHolding[]>("/us-transactions", input);
}

/** 米株取引を更新（PUT /us-transactions/{id}・C-14＝tasks/review-2026-06-12.md）。
 * 更新後の全保有 UsHolding[] を返す。存在しない id は 404。
 * fx_rate 省略時はサーバが約定日レートを解決（未取得なら 400「FX レート未取得」が ApiError で throw）。 */
export function putUsTransaction(id: number, input: UsTransactionInput): Promise<UsHolding[]> {
  return putJSON<UsHolding[]>(`/us-transactions/${id}`, input);
}

/** 米株取引を削除（DELETE /us-transactions/{id}）。更新後の全保有 UsHolding[] を返す。 */
export function deleteUsTransaction(id: number): Promise<UsHolding[]> {
  return del<UsHolding[]>(`/us-transactions/${id}`);
}
