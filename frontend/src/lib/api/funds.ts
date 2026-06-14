import { del, getJSON, postJSON, putJSON } from "./_client";

// --- ADR-054 投資信託（funds / fund-transactions / fund-holdings / nav-series）---
// backend Pydantic と 1:1。nav・avg_cost・price は「10,000 口あたりの円」、units は口数（小数）。
// 評価額・含み損益は backend が計算済み（market_value/unrealized_pnl）。frontend で再計算しない（ADR-014）。

/** 投信マスタ 1 件（ISIN を主キーに名称・協会コードを持つ）。 */
export type Fund = {
  isin: string;
  name: string;
  assoc_code: string | null; // 協会コード（NAV 取得キー・任意）
  updated_at: string | null;
};

/** `POST /funds` リクエスト（ISIN＋名称＋協会コードで登録）。
 * assoc_code は NAV 取得（投信総合検索ライブラリー associFundCd）に必須。未指定だと backend が 422。 */
export type FundInput = {
  isin: string;
  name: string;
  assoc_code: string; // NAV 取得に必須（空不可）
};

/** 投信取引 1 件（株の Transaction に対応）。price は 10,000 口あたりの基準価額（円）。 */
export type FundTransaction = {
  id: number;
  portfolio_id: number;
  isin: string;
  side: "buy" | "sell";
  units: number; // 口数（小数）
  price: number; // 約定基準価額（10,000 口あたりの円）
  fee: number | null; // 手数料（任意）
  traded_at: string; // 約定日 YYYY-MM-DD
};

/** `POST /fund-transactions`・`PUT /fund-transactions/{id}` リクエスト。 */
export type FundTransactionInput = {
  portfolio_id: number;
  isin: string;
  side: "buy" | "sell";
  units: number;
  price: number;
  fee?: number | null;
  traded_at: string;
};

/** 投信保有 1 件（取引から導出。評価額・含み損益は backend 計算＝ADR-014）。 */
export type FundHolding = {
  isin: string;
  name: string | null;
  units: number; // 口数（小数）
  avg_cost: number | null; // 平均取得（10,000 口あたりの円）
  last_nav: number | null; // 現在 NAV（10,000 口あたりの円）
  nav_date: string | null; // NAV 基準日
  market_value: number | null; // 評価額（backend 計算）
  unrealized_pnl: number | null; // 含み損益（backend 計算）
  weight: number | null; // 投信内比率（0..1・UI で ×100）
};

/** NAV 推移の 1 点（スパークライン用・date 昇順）。 */
export type FundNavPoint = {
  date: string;
  nav: number | null; // 10,000 口あたりの円
};

/** 投信マスタ一覧（ADR-054・GET /funds）。 */
export function getFunds(signal?: AbortSignal): Promise<Fund[]> {
  return getJSON<Fund[]>("/funds", signal);
}

/** 投信マスタを新規登録（POST /funds）。assoc_code は NAV 取得に必須（未指定だと backend 422）。 */
export function postFund(input: FundInput): Promise<Fund> {
  return postJSON<Fund>("/funds", {
    isin: input.isin,
    name: input.name,
    assoc_code: input.assoc_code,
  });
}

/** 投信マスタを削除（DELETE /funds/{isin}）。 */
export function deleteFund(isin: string): Promise<{ ok: boolean }> {
  return del<{ ok: boolean }>(`/funds/${encodeURIComponent(isin)}`);
}

/** 投信取引履歴一覧（GET /fund-transactions・新しい順）。 */
export function getFundTransactions(
  portfolioId: number,
  signal?: AbortSignal,
): Promise<FundTransaction[]> {
  return getJSON<FundTransaction[]>(`/fund-transactions?portfolio_id=${portfolioId}`, signal);
}

/** 投信取引を記録（POST /fund-transactions）。取引後の最新保有 FundHolding[] を返す。 */
export function postFundTransaction(input: FundTransactionInput): Promise<FundHolding[]> {
  return postJSON<FundHolding[]>("/fund-transactions", input);
}

/** 投信取引を更新（PUT /fund-transactions/{id}）。取引後の最新保有 FundHolding[] を返す。 */
export function putFundTransaction(
  id: number,
  input: FundTransactionInput,
): Promise<FundHolding[]> {
  return putJSON<FundHolding[]>(`/fund-transactions/${id}`, input);
}

/** 投信取引を削除（DELETE /fund-transactions/{id}）。取引後の最新保有 FundHolding[] を返す。 */
export function deleteFundTransaction(id: number): Promise<FundHolding[]> {
  return del<FundHolding[]>(`/fund-transactions/${id}`);
}

/** 投信保有一覧（GET /fund-holdings）。評価額・含み損益は backend 計算（ADR-014）。 */
export function getFundHoldings(portfolioId: number, signal?: AbortSignal): Promise<FundHolding[]> {
  return getJSON<FundHolding[]>(`/fund-holdings?portfolio_id=${portfolioId}`, signal);
}

/** NAV 推移（GET /funds/{isin}/nav-series）。limit で点数を絞る（スパークライン用）。 */
export function getFundNavSeries(
  isin: string,
  limit?: number,
  signal?: AbortSignal,
): Promise<FundNavPoint[]> {
  const qs = limit != null ? `?limit=${limit}` : "";
  return getJSON<FundNavPoint[]>(`/funds/${encodeURIComponent(isin)}/nav-series${qs}`, signal);
}
