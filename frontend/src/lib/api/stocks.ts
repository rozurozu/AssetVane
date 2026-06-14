import { del, getJSON, postJSON, putJSON } from "./_client";

export type Stock = {
  code: string;
  company_name: string | null;
  sector33_code: string | null;
  sector17_code: string | null;
  market_code: string | null;
  is_etf: number | null;
};

export type Quote = {
  date: string; // 'YYYY-MM-DD'
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  volume: number | null;
  adj_close: number | null;
};

// --- スクリーニング（ADR-031・backend ScreenCriteria/ScreenRow と 1:1）---
// 比率は内部 0..1（配当利回り）。UI でのみ ×100 して %（ADR-008）。市場跨ぎはしない（日本株専用）。

/** スクリーニング条件。全フィールド任意。保存フィルタもこの形を持つ。 */
export type ScreenCriteria = {
  q?: string; // 銘柄名・コードの部分一致
  per_min?: number;
  per_max?: number;
  pbr_min?: number;
  pbr_max?: number;
  market_cap_min?: number; // 円
  market_cap_max?: number;
  dividend_yield_min?: number; // 0..1（UI で ×100）
  dividend_yield_max?: number;
  sector33_code?: string;
  market_code?: string;
  exclude_etf?: boolean;
  per_sector_pctile_max?: number; // 業種内で安い割合 0..1
  market_cap_rank_max?: number; // 時価総額 上位 N
  sort_by?: "per" | "pbr" | "market_cap" | "dividend_yield" | "per_sector_pctile" | "code";
  sort_dir?: "asc" | "desc";
  limit?: number;
  offset?: number;
};

/** スクリーナー 1 行（valuation_snapshots × stocks ＋ 読み取り時ランク）。 */
export type ScreenRow = {
  code: string;
  company_name: string | null;
  sector33_code: string | null;
  market_code: string | null;
  is_etf: number | null;
  as_of_date: string | null;
  close: number | null;
  eps: number | null;
  bps: number | null;
  dividend_per_share: number | null;
  per: number | null;
  pbr: number | null;
  market_cap: number | null; // 円
  dividend_yield: number | null; // 0..1
  per_sector_pctile: number | null; // 0..1
  market_cap_rank: number | null;
};

/** 保存スクリーニング条件（CRUD /screening-filters）。criteria は前方互換の緩い形。 */
export type SavedFilter = {
  id: number;
  name: string;
  criteria: ScreenCriteria;
  created_at: string | null;
  updated_at: string | null;
};

/** 保存フィルタの作成/更新リクエスト。 */
export type SavedFilterInput = {
  name: string;
  criteria: ScreenCriteria;
};

export function getStocks(q?: string, signal?: AbortSignal): Promise<Stock[]> {
  const qs = q ? `?q=${encodeURIComponent(q)}` : "";
  return getJSON<Stock[]>(`/stocks${qs}`, signal);
}

export function getStock(code: string, signal?: AbortSignal): Promise<Stock> {
  return getJSON<Stock>(`/stocks/${encodeURIComponent(code)}`, signal);
}

export function getQuotes(
  code: string,
  from?: string,
  to?: string,
  signal?: AbortSignal,
): Promise<Quote[]> {
  const params = new URLSearchParams();
  if (from) params.set("from", from);
  if (to) params.set("to", to);
  const qs = params.toString();
  return getJSON<Quote[]>(`/quotes/${encodeURIComponent(code)}${qs ? `?${qs}` : ""}`, signal);
}

/** スクリーニング（GET /stocks/screen・ADR-031）。criteria を query にして読み取り時計算結果を得る。 */
export function screenStocks(criteria: ScreenCriteria, signal?: AbortSignal): Promise<ScreenRow[]> {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(criteria)) {
    if (v === undefined || v === null || v === "") continue;
    if (typeof v === "boolean") {
      if (v) p.set(k, "true"); // false は送らない（既定 false）
    } else {
      p.set(k, String(v));
    }
  }
  const qs = p.toString();
  return getJSON<ScreenRow[]>(`/stocks/screen${qs ? `?${qs}` : ""}`, signal);
}

/** 保存フィルタ一覧（GET /screening-filters）。 */
export function getFilters(signal?: AbortSignal): Promise<SavedFilter[]> {
  return getJSON<SavedFilter[]>("/screening-filters", signal);
}

/** 保存フィルタを作成（POST /screening-filters）。 */
export function postFilter(input: SavedFilterInput): Promise<SavedFilter> {
  return postJSON<SavedFilter>("/screening-filters", input);
}

/** 保存フィルタを更新（PUT /screening-filters/{id}）。 */
export function putFilter(id: number, input: SavedFilterInput): Promise<SavedFilter> {
  return putJSON<SavedFilter>(`/screening-filters/${id}`, input);
}

/** 保存フィルタを削除（DELETE /screening-filters/{id}）。 */
export function deleteFilter(id: number): Promise<{ ok: boolean }> {
  return del<{ ok: boolean }>(`/screening-filters/${id}`);
}
