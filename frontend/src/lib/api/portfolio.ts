import { del, getJSON, postJSON, putJSON } from "./_client";

// --- Phase 2 型定義（phase2-spec.md §5・TS 型は Pydantic と 1:1） ---
// 比率・weight・current/limit はすべて 0..1（UI でのみ ×100 して %・ADR-008）。

/** ポートフォリオ（P2-1・`GET /portfolios`）。既定は配列先頭（裁定 L-9）。 */
export type Portfolio = {
  portfolio_id: number;
  name: string;
  created_at: string | null;
};

/** 遅延メタ（Free 12週遅延・ADR-008）。holdings のみ valuation_meta ラッパに包む。 */
export type ValuationMeta = {
  as_of: string | null;
  is_delayed: boolean;
  plan: string;
};

/** 保有明細（transactions からの導出値・ADR-019）。 */
export type Holding = {
  id: number;
  code: string;
  company_name: string | null;
  shares: number;
  avg_cost: number | null;
  last_close: number | null;
  market_value: number | null;
  unrealized_pnl: number | null;
  weight: number | null; // 株式内比率（0..1・UI で ×100）
};

/** `GET /holdings` レスポンス（P2-2）。 */
export type HoldingsResponse = {
  portfolio_id: number;
  holdings: Holding[];
  valuation_meta: ValuationMeta;
};

/** `POST /transactions` リクエスト（P2-2）。 */
export type TransactionInput = {
  portfolio_id: number;
  code: string;
  side: "buy" | "sell";
  shares: number;
  price: number; // 約定単価
  fee?: number | null; // 手数料（任意）
  traded_at: string; // 約定日 YYYY-MM-DD
};

/** `POST /transactions` レスポンス（P2-2）。 */
export type TransactionResult = {
  transaction_id: number;
  holdings: HoldingsResponse;
};

/** 取引履歴 1 行（`GET /transactions` の 1 要素・P2-2）。新しい順で返る。
 * company_name は stocks JOIN で補完（行に名前を焼かない＝ADR-019）。 */
export type Transaction = {
  id: number;
  code: string;
  company_name: string | null;
  side: "buy" | "sell";
  shares: number;
  price: number; // 約定単価
  fee: number | null; // 手数料（任意）
  traded_at: string; // 約定日 YYYY-MM-DD
};

/** `GET /cash` レスポンス・`PUT /cash` レスポンス（P2-3）。 */
export type Cash = {
  id: number;
  balance: number;
  updated_at: string | null;
};

/** `PUT /cash` リクエスト（P2-3）。 */
export type CashInput = {
  balance: number;
};

/** 外部資産（投信・コモディティ等の手入力資産・P2-4）。 */
export type ExternalAsset = {
  id: number;
  name: string;
  category: string | null;
  value: number;
  proxy_symbol: string | null;
  monthly_contribution: number | null;
  as_of: string | null;
};

/** `POST /external-assets` / `PUT /external-assets/{id}` リクエスト（P2-4）。 */
export type ExternalAssetInput = {
  name: string;
  category?: string | null;
  value: number;
  proxy_symbol?: string | null;
  monthly_contribution?: number | null;
  as_of?: string | null;
};

/** 相関行列（P2-5）。codes[i]/labels[i] が matrix[i][j] に対応。 */
export type CorrelationMatrix = {
  codes: string[];
  labels: string[];
  matrix: number[][];
};

/** 逸脱（policy 違反・P2-5/P2-7 共用）。current/limit は 0..1。 */
export type Deviation = {
  kind: "max_position" | "cash_ratio" | "sector_cap";
  label: string;
  current: number; // 0..1
  limit: number; // 0..1
  breached: boolean;
};

/** `GET /portfolio/{id}/metrics` レスポンス（P2-5）。 */
export type PortfolioMetrics = {
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
};

/** `POST /portfolio/{id}/optimize` リクエスト（P2-6）。省略時は policy をそのまま使う。 */
export type OptimizeRequest = {
  target_cash_ratio?: number | null;
  max_position_weight?: number | null;
  sector_caps?: Record<string, number> | null;
};

/** 最適化後の銘柄ウェイト（P2-6）。 */
export type OptimizeWeight = {
  code: string;
  company_name: string | null;
  current_weight: number | null; // 現状比率（0..1）
  target_weight: number; // 最適比率（0..1）
  delta: number; // target - current（0..1）
};

/** `POST /portfolio/{id}/optimize` レスポンス（P2-6）。infeasible=true なら weights は空。 */
export type OptimizeResult = {
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
};

/** backtest 累積曲線の 1 点（value は 1 始まりの倍率・§4.4）。 */
export type BacktestCurvePoint = {
  date: string;
  value: number;
};

/** backtest の 1 系列（ポート/ベンチ共通形・§4.4）。 */
export type BacktestLeg = {
  cumulative_return: number; // 累積リターン（0..1 基準の比率）
  annual_return: number; // 年率リターン
  sharpe: number | null;
  max_drawdown: number; // 最大DD（負値）
  curve: BacktestCurvePoint[];
};

/** `GET /portfolio/{id}/backtest` レスポンス（現保有 buy&hold vs TOPIX・§4.4）。 */
export type BacktestResult = {
  portfolio_id: number;
  as_of: string | null;
  is_delayed: boolean;
  portfolio: BacktestLeg;
  benchmark: BacktestLeg;
  excess_return: number; // ポート年率 - ベンチ年率
};

/** 配分ドーナツ用スライス（P2-7・ADR-054・ADR-055）。weight は 0..1（UI で ×100）。
 * external_assets 由来は backend が "外部資産"、NAV 自動取得の投信は "投資信託"、
 * 米国株保有は "米国株" を返す（Phase 7(B-2)）。 */
export type AllocationSlice = {
  name: "株式" | "現金" | "外部資産" | "投資信託" | "米国株";
  value: number;
  weight: number; // 0..1
};

/** 資産推移スパークライン用（P2-7）。 */
export type AssetSnapshotPoint = {
  date: string;
  total_value: number;
};

/** `GET /asset-overview` レスポンス（P2-7・Phase 7(B-2) で us_stock_value を追加）。 */
export type AssetOverview = {
  as_of: string | null;
  is_delayed: boolean;
  plan: string;
  total_value: number;
  stock_value: number;
  cash_value: number;
  external_value: number;
  fund_value: number; // 投資信託の評価額合計（ADR-054・allocation に「投資信託」スライスが入る）
  us_stock_value: number; // 米国株の評価額合計（JPY 建て・Phase 7(B-2)・allocation に「米国株」スライスが入る）
  pnl: number; // 評価損益
  pnl_ratio: number | null; // 総資産ベースの損益率（backend 計算・ADR-014・frontend で再計算しない）
  allocation: AllocationSlice[];
  policy_targets: {
    target_cash_ratio: number | null;
    max_position_weight: number | null;
  };
  deviations: Deviation[];
  trend: AssetSnapshotPoint[];
};

// --- Phase 2 API 関数（phase2-spec.md §5）---
// すべて `lib/api.ts` に集約（ADR-005）。DB に触れない。

/** ポートフォリオ一覧（P2-1）。既定ポートフォリオは先頭（裁定 L-9）。 */
export function getPortfolios(signal?: AbortSignal): Promise<Portfolio[]> {
  return getJSON<Portfolio[]>("/portfolios", signal);
}

/** 保有明細（P2-2）。評価額は Free 12週遅延（valuation_meta.is_delayed）。 */
export function getHoldings(portfolioId: number, signal?: AbortSignal): Promise<HoldingsResponse> {
  return getJSON<HoldingsResponse>(`/holdings?portfolio_id=${portfolioId}`, signal);
}

/** 取引記録（P2-2）。サーバ側で holdings を再計算し、更新後の一覧を返す（ADR-019）。 */
export function postTransaction(input: TransactionInput): Promise<TransactionResult> {
  return postJSON<TransactionResult>("/transactions", input);
}

/** 取引履歴一覧（P2-2）。新しい順・company_name 付き（ADR-019）。 */
export function getTransactions(portfolioId: number, signal?: AbortSignal): Promise<Transaction[]> {
  return getJSON<Transaction[]>(`/transactions?portfolio_id=${portfolioId}`, signal);
}

/** 取引を更新（P2-2）。サーバ側で holdings を再計算し、更新後の一覧を返す（ADR-019）。
 * 存在しない id は 404。 */
export function putTransaction(id: number, input: TransactionInput): Promise<TransactionResult> {
  return putJSON<TransactionResult>(`/transactions/${id}`, input);
}

/** 取引を削除（P2-2）。サーバ側で holdings を再計算し、更新後の一覧を返す（ADR-019）。
 * 存在しない id は 404。 */
export function deleteTransaction(id: number): Promise<TransactionResult> {
  return del<TransactionResult>(`/transactions/${id}`);
}

/** 現金残高取得（P2-3）。未登録は 404（呼び元で ApiError.status===404 を「未設定」表示）。 */
export function getCash(signal?: AbortSignal): Promise<Cash> {
  return getJSON<Cash>("/cash", signal);
}

/** 現金残高更新（P2-3）。 */
export function putCash(balance: number): Promise<Cash> {
  return putJSON<Cash>("/cash", { balance } satisfies CashInput);
}

/** 外部資産（投信・コモディティ等）一覧（P2-4）。 */
export function getExternalAssets(signal?: AbortSignal): Promise<ExternalAsset[]> {
  return getJSON<ExternalAsset[]>("/external-assets", signal);
}

/** 外部資産 新規作成（P2-4）。 */
export function postExternalAsset(input: ExternalAssetInput): Promise<ExternalAsset> {
  return postJSON<ExternalAsset>("/external-assets", input);
}

/** 外部資産 更新（P2-4）。 */
export function putExternalAsset(id: number, input: ExternalAssetInput): Promise<ExternalAsset> {
  return putJSON<ExternalAsset>(`/external-assets/${id}`, input);
}

/** 外部資産 削除（P2-4）。 */
export function deleteExternalAsset(id: number): Promise<{ ok: true }> {
  return del<{ ok: true }>(`/external-assets/${id}`);
}

/** ポートフォリオメトリクス（相関・シャープ・最大DD・逸脱・P2-5）。 */
export function getPortfolioMetrics(
  portfolioId: number,
  signal?: AbortSignal,
): Promise<PortfolioMetrics> {
  return getJSON<PortfolioMetrics>(`/portfolio/${portfolioId}/metrics`, signal);
}

/** 平均分散最適化（P2-6）。body 省略時は policy 制約をそのまま使う。 */
export function optimizePortfolio(
  portfolioId: number,
  body?: OptimizeRequest,
): Promise<OptimizeResult> {
  return postJSON<OptimizeResult>(`/portfolio/${portfolioId}/optimize`, body ?? {});
}

/** 過去シミュレーション（現保有 buy&hold vs TOPIX・§4.4）。 */
export function getPortfolioBacktest(
  portfolioId: number,
  signal?: AbortSignal,
): Promise<BacktestResult> {
  return getJSON<BacktestResult>(`/portfolio/${portfolioId}/backtest`, signal);
}

/** 資産全体像（KPI・配分・逸脱・推移スパークライン・P2-7）。 */
export function getAssetOverview(signal?: AbortSignal): Promise<AssetOverview> {
  return getJSON<AssetOverview>("/asset-overview", signal);
}
