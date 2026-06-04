// FastAPI（唯一のデータ所有者・ADR-005）への薄いクライアント。
// Next は UI 専用で DB に触らず、すべてこの REST 経由（docs/api.md）。
// NEXT_PUBLIC_* はブラウザに焼き込まれるため、ブラウザから到達できる名前を使う（architecture.md §7.1）。
export const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

/** API エラー。status 付きで throw する（呼び出し側で `e instanceof ApiError` で分岐できる）。
 * メッセージは FastAPI の `{"detail": "..."}` から拾う（router 境界で HTTPException 翻訳）。 */
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** レスポンスから detail を取り出して ApiError を作る（4 ヘルパ共通）。 */
async function toApiError(r: Response): Promise<ApiError> {
  const detail = await r
    .json()
    .then((j) => (j as { detail?: string }).detail ?? `HTTP ${r.status}`)
    .catch(() => `HTTP ${r.status}`);
  return new ApiError(r.status, detail);
}

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

// --- Phase 3 型定義（phase3-spec.md §9.5 / api.md §4・§7・Pydantic と 1:1）---
// 比率系（target_cash_ratio / max_position_weight / sector_caps）はすべて 0..1。
// UI でのみ ×100 して % 表示・保存時 ÷100（ADR-008 / spec §9.2）。

/** 構造化コア（policy の定量レバー・api.md §7 GET /policy）。比率は 0..1。 */
export interface PolicyCore {
  risk_tolerance: string | null; // "低"/"中"/"高"
  time_horizon: string | null; // "短"/"中"/"長"
  target_cash_ratio: number | null; // 0..1（UI で ×100）
  max_position_weight: number | null; // 0..1
  sector_caps: Record<string, number>; // {sector33_code: 0..1}
  target_return: number | null; // 0..1（任意）
  no_leverage: boolean;
  exclusions: string[]; // 除外銘柄コード等
}

/** `GET /policy` レスポンス（core / rationale を分けて返す・api.md §7）。 */
export interface Policy {
  core: PolicyCore;
  rationale: string | null; // 自由文の理念（引用調で表示）
  updated_at: string | null;
}

/** `PUT /policy` リクエスト（core 部分更新・rationale 即時更新・ADR-013 / U-7）。 */
export interface PolicyUpdate {
  core?: Partial<PolicyCore>;
  rationale?: string;
}

/** 投資日記 1 件（spec §8.2・date 降順）。source は夜の自動 / チャット要約昇格（ADR-029）。 */
export interface JournalEntry {
  id: number;
  date: string; // YYYY-MM-DD
  source: "nightly" | "chat";
  observations: string | null; // AI 所見（自由文）
  proposal: string | null; // 当日の提案（自由文 or 参照）
  proposed_policy_change: unknown | null; // JSON {field, from, to, reason}（任意）
  policy_snapshot: unknown | null; // その時点の policy まるごと（差分チップ用）
  llm_model: string | null;
  created_at: string | null;
}

/** `GET /journal` レスポンス（spec §8.2）。 */
export interface JournalResponse {
  entries: JournalEntry[];
}

/** AI 提案 1 件（spec §8.2・承認制・約定はしない＝ADR-001/019）。 */
export interface Proposal {
  id: number;
  created_date: string; // YYYY-MM-DD
  kind: "policy_change" | "buy" | "sell" | "rebalance";
  body: unknown | null; // kind 依存の JSON
  rationale: string | null; // 根拠（AI の説明）
  status: "pending" | "approved" | "rejected";
  outcome: string | null;
  resolved_at: string | null;
  journal_id: number | null; // 生成元 journal（チャット起票は null 可）
  depends_on: number | null; // 別 proposal の承認が前提（承認順制御・決定4）
}

/** `GET /proposals` レスポンス（spec §8.2）。 */
export interface ProposalsResponse {
  proposals: Proposal[];
}

/** `POST /proposals/{id}/approve|reject` レスポンス（spec §8.2）。 */
export interface ResolveResult {
  proposal: Proposal;
}

/** 画面コンテキストの主対象（ADR-025・api.md §4・type で code/id を使い分け）。 */
export interface FocusRef {
  type: "stock" | "portfolio" | "signal" | "proposal";
  code?: string; // stock / signal
  id?: number; // portfolio / proposal
}

/** 画面コンテキスト（軽量ヒント・数値は載せない＝ADR-025）。 */
export interface ChatContext {
  page: string; // "stock_detail" / "dashboard" / "signals" / "policy" / ...
  focus?: FocusRef; // 対象が無いページは省略
}

/** チャット 1 ターン（system 不可・user/assistant のみ）。 */
export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

/** `POST /chat` リクエスト（spec §6.3・毎ターン全 messages 送信＝ステートレス）。 */
export interface ChatRequest {
  messages: ChatMessage[];
  context?: ChatContext; // ADR-025（数値は載せない）
}

/** AI が呼んだ Tool（UI 可視化用・結果の数値は載せない＝ADR-025）。 */
export interface ToolRun {
  name: string;
  args?: Record<string, unknown> | null;
}

/** `POST /chat` レスポンス（非ストリーミング・spec §4.2/§6.3）。 */
export interface ChatResponse {
  reply: string;
  tool_runs: ToolRun[];
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

// 生 fetch をコンポーネントに散らさず、この 4 ヘルパに集約する（ADR-005）。
// 失敗は detail を載せた ApiError を throw（呼び出し側で status 分岐可能）。
// GET は signal を受けて fetch に渡す（AbortController でキャンセル＝useApi 連携）。
async function getJSON<T>(path: string, signal?: AbortSignal): Promise<T> {
  const r = await fetch(`${API_BASE}${path}`, {
    headers: { Accept: "application/json" },
    signal,
  });
  if (!r.ok) throw await toApiError(r);
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
  if (!r.ok) throw await toApiError(r);
  return r.json() as Promise<T>;
}

async function putJSON<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${API_BASE}${path}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw await toApiError(r);
  return r.json() as Promise<T>;
}

async function del<T>(path: string): Promise<T> {
  const r = await fetch(`${API_BASE}${path}`, {
    method: "DELETE",
    headers: { Accept: "application/json" },
  });
  if (!r.ok) throw await toApiError(r);
  return r.json() as Promise<T>;
}

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

export function getSignals(
  opts?: {
    date?: string;
    type?: SignalType;
    limit?: number;
  },
  signal?: AbortSignal,
): Promise<SignalsResponse> {
  const p = new URLSearchParams();
  if (opts?.date) p.set("date", opts.date);
  if (opts?.type) p.set("type", opts.type);
  if (opts?.limit != null) p.set("limit", String(opts.limit));
  const qs = p.toString();
  return getJSON<SignalsResponse>(`/signals${qs ? `?${qs}` : ""}`, signal);
}

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

/** 資産全体像（KPI・配分・逸脱・推移スパークライン・P2-7）。 */
export function getAssetOverview(signal?: AbortSignal): Promise<AssetOverview> {
  return getJSON<AssetOverview>("/asset-overview", signal);
}

// --- Phase 3 API 関数（phase3-spec.md §9.5・既存 fetch ヘルパと同じ流儀）---
// すべて `lib/api.ts` に集約（ADR-005）。DB に触れない。エラーは detail を throw する。

/** 現在の投資方針（core / rationale 分離・api.md §7）。 */
export function getPolicy(signal?: AbortSignal): Promise<Policy> {
  return getJSON<Policy>("/policy", signal);
}

/** 投資方針の更新（core=承認制 lever / rationale=即時・ADR-013）。比率は 0..1 で送る（UI で ÷100）。 */
export function putPolicy(update: PolicyUpdate): Promise<Policy> {
  return putJSON<Policy>("/policy", update);
}

/** 投資日記の取得（date 降順・from/to で期間指定・spec §8.2）。 */
export function getJournal(
  from?: string,
  to?: string,
  signal?: AbortSignal,
): Promise<JournalResponse> {
  const p = new URLSearchParams();
  if (from) p.set("from", from);
  if (to) p.set("to", to);
  const qs = p.toString();
  return getJSON<JournalResponse>(`/journal${qs ? `?${qs}` : ""}`, signal);
}

/** AI 提案の取得（status で pending/approved/rejected 絞り込み・spec §8.2）。 */
export function getProposals(
  status?: Proposal["status"],
  signal?: AbortSignal,
): Promise<ProposalsResponse> {
  const qs = status ? `?status=${encodeURIComponent(status)}` : "";
  return getJSON<ProposalsResponse>(`/proposals${qs}`, signal);
}

/** 提案を承認（kind=policy_change なら policy 更新＋journal snapshot・約定はしない＝ADR-001/019）。 */
export function approveProposal(id: number, outcome?: string): Promise<ResolveResult> {
  return postJSON<ResolveResult>(`/proposals/${id}/approve`, { outcome: outcome ?? null });
}

/** 提案を却下（status 遷移のみ・spec §8.2）。 */
export function rejectProposal(id: number, outcome?: string): Promise<ResolveResult> {
  return postJSON<ResolveResult>(`/proposals/${id}/reject`, { outcome: outcome ?? null });
}

/** 相談チャット（軸2・非ストリーミング・spec §6.3）。messages 全送信＋画面 context（数値なし）。 */
export function sendChat(req: ChatRequest): Promise<ChatResponse> {
  return postJSON<ChatResponse>("/chat", req);
}

/** SSE ストリーミング版の口だけ予約（spec §4.2・Phase 3 は非ストリーミング）。
 * TODO(phase3+): SSE 対応は Phase 3 後に検討（L-16）。現状は未実装。 */
export function sendChatStream(_req: ChatRequest): never {
  throw new Error("sendChatStream は未実装（Phase 3 は非ストリーミング・spec §4.2）");
}

/** `GET /health` レスポンス（疎通確認用・main.py）。詳細は使わず到達可否のみ参照する。 */
export interface HealthResponse {
  status?: string;
  [k: string]: unknown;
}

/** backend への疎通確認（Topbar の健全性バッジ。失敗は ApiError を throw）。 */
export function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return getJSON<HealthResponse>("/health", signal);
}
