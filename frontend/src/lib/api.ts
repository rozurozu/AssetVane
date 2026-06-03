// FastAPI（唯一のデータ所有者・ADR-005）への薄いクライアント。
// Next は UI 専用で DB に触らず、すべてこの REST 経由（docs/api.md）。
// NEXT_PUBLIC_* はブラウザに焼き込まれるため、ブラウザから到達できる名前を使う（architecture.md §7.1）。
export const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

// --- Phase 2 型定義（phase2-spec.md §5・TS 型は Pydantic と 1:1） ---
// 比率・weight・current/limit はすべて 0..1（UI でのみ ×100 して %・ADR-008）。

/** ポートフォリオ（P2-1・`GET /portfolios`）。既定は配列先頭（裁定 L-9）。 */
export interface Portfolio {
  portfolio_id: number;
  name: string;
  created_at: string | null;
}

/** 遅延メタ（Free 12週遅延・ADR-008）。holdings のみ valuation_meta ラッパに包む。 */
export interface ValuationMeta {
  as_of: string | null;
  is_delayed: boolean;
  plan: string;
}

/** 保有明細（transactions からの導出値・ADR-019）。 */
export interface Holding {
  id: number;
  code: string;
  company_name: string | null;
  shares: number;
  avg_cost: number | null;
  last_close: number | null;
  market_value: number | null;
  unrealized_pnl: number | null;
  weight: number | null; // 株式内比率（0..1・UI で ×100）
}

/** `GET /holdings` レスポンス（P2-2）。 */
export interface HoldingsResponse {
  portfolio_id: number;
  holdings: Holding[];
  valuation_meta: ValuationMeta;
}

/** `POST /transactions` リクエスト（P2-2）。 */
export interface TransactionInput {
  portfolio_id: number;
  code: string;
  side: "buy" | "sell";
  shares: number;
  price: number; // 約定単価
  fee?: number | null; // 手数料（任意）
  traded_at: string; // 約定日 YYYY-MM-DD
}

/** `POST /transactions` レスポンス（P2-2）。 */
export interface TransactionResult {
  transaction_id: number;
  holdings: HoldingsResponse;
}

/** `GET /cash` レスポンス・`PUT /cash` レスポンス（P2-3）。 */
export interface Cash {
  id: number;
  balance: number;
  updated_at: string | null;
}

/** `PUT /cash` リクエスト（P2-3）。 */
export interface CashInput {
  balance: number;
}

/** 外部資産（投信・コモディティ等の手入力資産・P2-4）。 */
export interface ExternalAsset {
  id: number;
  name: string;
  category: string | null;
  value: number;
  proxy_symbol: string | null;
  monthly_contribution: number | null;
  as_of: string | null;
}

/** `POST /external-assets` / `PUT /external-assets/{id}` リクエスト（P2-4）。 */
export interface ExternalAssetInput {
  name: string;
  category?: string | null;
  value: number;
  proxy_symbol?: string | null;
  monthly_contribution?: number | null;
  as_of?: string | null;
}

/** 相関行列（P2-5）。codes[i]/labels[i] が matrix[i][j] に対応。 */
export interface CorrelationMatrix {
  codes: string[];
  labels: string[];
  matrix: number[][];
}

/** 逸脱（policy 違反・P2-5/P2-7 共用）。current/limit は 0..1。 */
export interface Deviation {
  kind: "max_position" | "cash_ratio" | "sector_cap";
  label: string;
  current: number; // 0..1
  limit: number; // 0..1
  breached: boolean;
}

/** `GET /portfolio/{id}/metrics` レスポンス（P2-5）。 */
export interface PortfolioMetrics {
  portfolio_id: number;
  as_of: string | null;
  is_delayed: boolean;
  annual_return: number | null; // 年率リターン（0..1）
  annual_volatility: number | null; // 年率ボラ（0..1）
  sharpe: number | null;
  max_drawdown: number | null; // 最大DD（0..1）
  lookback_days: number | null;
  correlation: CorrelationMatrix;
  deviations: Deviation[];
}

/** `POST /portfolio/{id}/optimize` リクエスト（P2-6）。省略時は policy をそのまま使う。 */
export interface OptimizeRequest {
  target_cash_ratio?: number | null;
  max_position_weight?: number | null;
  sector_caps?: Record<string, number> | null;
}

/** 最適化後の銘柄ウェイト（P2-6）。 */
export interface OptimizeWeight {
  code: string;
  company_name: string | null;
  current_weight: number | null; // 現状比率（0..1）
  target_weight: number; // 最適比率（0..1）
  delta: number; // target - current（0..1）
}

/** `POST /portfolio/{id}/optimize` レスポンス（P2-6）。infeasible=true なら weights は空。 */
export interface OptimizeResult {
  portfolio_id: number;
  as_of: string | null;
  is_delayed: boolean;
  objective: string;
  cash_weight: number; // 現金比率（0..1）
  weights: OptimizeWeight[];
  expected_annual_return: number | null;
  expected_annual_volatility: number | null;
  expected_sharpe: number | null;
  constraints_applied: {
    target_cash_ratio: number | null;
    max_position_weight: number | null;
    sector_caps: Record<string, number> | null;
  };
  infeasible: boolean;
}

/** 配分ドーナツ用スライス（P2-7）。weight は 0..1（UI で ×100）。 */
export interface AllocationSlice {
  name: "株式" | "現金" | "投信";
  value: number;
  weight: number; // 0..1
}

/** 資産推移スパークライン用（P2-7）。 */
export interface AssetSnapshotPoint {
  date: string;
  total_value: number;
}

/** `GET /asset-overview` レスポンス（P2-7）。 */
export interface AssetOverview {
  as_of: string | null;
  is_delayed: boolean;
  plan: string;
  total_value: number;
  stock_value: number;
  cash_value: number;
  external_value: number;
  pnl: number; // 評価損益
  allocation: AllocationSlice[];
  policy_targets: {
    target_cash_ratio: number | null;
    max_position_weight: number | null;
  };
  deviations: Deviation[];
  trend: AssetSnapshotPoint[];
}

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

async function getJSON<T>(path: string): Promise<T> {
  const r = await fetch(`${API_BASE}${path}`, { headers: { Accept: "application/json" } });
  if (!r.ok) {
    const detail = await r
      .json()
      .then((j) => (j as { detail?: string }).detail ?? `HTTP ${r.status}`)
      .catch(() => `HTTP ${r.status}`);
    throw new Error(detail);
  }
  return r.json() as Promise<T>;
}

// POST / PUT / DELETE ヘルパ（getJSON と同じエラー処理・ADR-005）。
// Content-Type: application/json を付与し、レスポンス body を T として返す。
async function postJSON<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const detail = await r
      .json()
      .then((j) => (j as { detail?: string }).detail ?? `HTTP ${r.status}`)
      .catch(() => `HTTP ${r.status}`);
    throw new Error(detail);
  }
  return r.json() as Promise<T>;
}

async function putJSON<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${API_BASE}${path}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const detail = await r
      .json()
      .then((j) => (j as { detail?: string }).detail ?? `HTTP ${r.status}`)
      .catch(() => `HTTP ${r.status}`);
    throw new Error(detail);
  }
  return r.json() as Promise<T>;
}

async function del<T>(path: string): Promise<T> {
  const r = await fetch(`${API_BASE}${path}`, {
    method: "DELETE",
    headers: { Accept: "application/json" },
  });
  if (!r.ok) {
    const detail = await r
      .json()
      .then((j) => (j as { detail?: string }).detail ?? `HTTP ${r.status}`)
      .catch(() => `HTTP ${r.status}`);
    throw new Error(detail);
  }
  return r.json() as Promise<T>;
}

export function getStocks(q?: string): Promise<Stock[]> {
  const qs = q ? `?q=${encodeURIComponent(q)}` : "";
  return getJSON<Stock[]>(`/stocks${qs}`);
}

export function getStock(code: string): Promise<Stock> {
  return getJSON<Stock>(`/stocks/${encodeURIComponent(code)}`);
}

export function getQuotes(code: string, from?: string, to?: string): Promise<Quote[]> {
  const params = new URLSearchParams();
  if (from) params.set("from", from);
  if (to) params.set("to", to);
  const qs = params.toString();
  return getJSON<Quote[]>(`/quotes/${encodeURIComponent(code)}${qs ? `?${qs}` : ""}`);
}

// シグナル（Trend Vane・Phase 1・docs/api.md §5.1・docs/phase-specs/phase1-spec.md §5.1）。
// 夜間バッチが事前計算した「事実」を読むだけ（AI には計算させない＝ADR-014）。
// score は連続値 0..1、絞り込みは読み取り側が行う（ADR-026）。型は backend Pydantic と 1:1。
export type SignalType = "momentum" | "volume_spike" | "ai_alpha" | "lead_lag";

export interface SignalPayload {
  label?: string; // 一覧の「シグナル」列の短文（quant が格納）
  change_5d?: number | null; // 5日騰落率（符号付き小数・quant が格納）
  [k: string]: unknown; // momentum/volume_spike の type 固有指標（quant 確定）
}

export interface Signal {
  code: string;
  company_name: string | null; // signals JOIN stocks（ルータ補完）
  signal_type: SignalType;
  score: number; // 0..1
  payload: SignalPayload;
}

export interface SignalsResponse {
  date: string; // 実際に返した算出日（最新解決後）
  is_delayed: boolean; // 遅延フラグ（横断・Free=true）
  signals: Signal[]; // score 降順
}

export function getSignals(opts?: {
  date?: string;
  type?: SignalType;
  limit?: number;
}): Promise<SignalsResponse> {
  const p = new URLSearchParams();
  if (opts?.date) p.set("date", opts.date);
  if (opts?.type) p.set("type", opts.type);
  if (opts?.limit != null) p.set("limit", String(opts.limit));
  const qs = p.toString();
  return getJSON<SignalsResponse>(`/signals${qs ? `?${qs}` : ""}`);
}

// --- Phase 2 API 関数（phase2-spec.md §5）---
// すべて `lib/api.ts` に集約（ADR-005）。DB に触れない。

/** ポートフォリオ一覧（P2-1）。既定ポートフォリオは先頭（裁定 L-9）。 */
export function getPortfolios(): Promise<Portfolio[]> {
  return getJSON<Portfolio[]>("/portfolios");
}

/** 保有明細（P2-2）。評価額は Free 12週遅延（valuation_meta.is_delayed）。 */
export function getHoldings(portfolioId: number): Promise<HoldingsResponse> {
  return getJSON<HoldingsResponse>(`/holdings?portfolio_id=${portfolioId}`);
}

/** 取引記録（P2-2）。サーバ側で holdings を再計算し、更新後の一覧を返す（ADR-019）。 */
export function postTransaction(input: TransactionInput): Promise<TransactionResult> {
  return postJSON<TransactionResult>("/transactions", input);
}

/** 現金残高取得（P2-3）。未登録は 404（呼び元で catch して「未設定」表示）。 */
export function getCash(): Promise<Cash> {
  return getJSON<Cash>("/cash");
}

/** 現金残高更新（P2-3）。 */
export function putCash(balance: number): Promise<Cash> {
  return putJSON<Cash>("/cash", { balance } satisfies CashInput);
}

/** 外部資産（投信・コモディティ等）一覧（P2-4）。 */
export function getExternalAssets(): Promise<ExternalAsset[]> {
  return getJSON<ExternalAsset[]>("/external-assets");
}

/** 外部資産 新規作成（P2-4）。 */
export function createExternalAsset(input: ExternalAssetInput): Promise<ExternalAsset> {
  return postJSON<ExternalAsset>("/external-assets", input);
}

/** 外部資産 更新（P2-4）。 */
export function updateExternalAsset(id: number, input: ExternalAssetInput): Promise<ExternalAsset> {
  return putJSON<ExternalAsset>(`/external-assets/${id}`, input);
}

/** 外部資産 削除（P2-4）。 */
export function deleteExternalAsset(id: number): Promise<{ ok: true }> {
  return del<{ ok: true }>(`/external-assets/${id}`);
}

/** ポートフォリオメトリクス（相関・シャープ・最大DD・逸脱・P2-5）。 */
export function getPortfolioMetrics(portfolioId: number): Promise<PortfolioMetrics> {
  return getJSON<PortfolioMetrics>(`/portfolio/${portfolioId}/metrics`);
}

/** 平均分散最適化（P2-6）。body 省略時は policy 制約をそのまま使う。 */
export function optimizePortfolio(
  portfolioId: number,
  body?: OptimizeRequest,
): Promise<OptimizeResult> {
  return postJSON<OptimizeResult>(`/portfolio/${portfolioId}/optimize`, body ?? {});
}

/** 資産全体像（KPI・配分・逸脱・推移スパークライン・P2-7）。 */
export function getAssetOverview(): Promise<AssetOverview> {
  return getJSON<AssetOverview>("/asset-overview");
}
