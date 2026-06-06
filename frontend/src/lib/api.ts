// FastAPI（唯一のデータ所有者・ADR-005）への薄いクライアント。
// Next は UI 専用で DB に触らず、すべてこの REST 経由（docs/api.md）。
// 同一オリジン化（ADR-037）: ブラウザは相対パス `/api` を叩き、Next の rewrites（next.config.ts）が
// 裏で backend へ素通しする。ブラウザは backend のホストを知らないので CORS も URL 焼き込みも不要。
export const API_BASE = "/api";

/** API エラー。status 付きで throw する（呼び出し側で `e instanceof ApiError` で分岐できる）。
 * メッセージは FastAPI の `{"detail": "..."}` から拾う（router 境界で HTTPException 翻訳）。
 * status=0 は「ネットワーク到達不能」（CORS・接続拒否・タイムアウト等で fetch 自体が失敗し、
 * HTTP ステータスが取れなかった）を表す約束（ADR-038）。message に解決済み URL を載せる。 */
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** ネットワーク到達不能（status=0）の意味（ADR-038）。CORS・接続拒否・タイムアウトで使う。 */
const NETWORK_UNREACHABLE = 0;

/** path から解決済みのリクエスト URL を組み立てる（エラーメッセージに載せて追跡可能にする・ADR-038）。
 * ブラウザは相対 `/api`（ADR-037）を自オリジンへ解決するので、location.origin を前置する。 */
function resolveUrl(path: string): string {
  const origin = typeof location !== "undefined" ? location.origin : "";
  return `${origin}${API_BASE}${path}`;
}

/** fetch を実行し、ネットワーク到達不能（fetch が TypeError を投げる）を ApiError(status=0) に翻訳する。
 * CORS・接続拒否・DNS 失敗ではブラウザ fetch は status も URL も持たない TypeError を投げるため、
 * ここで解決済み URL を載せた ApiError に翻訳して「どこへ繋ごうとして失敗したか」を追えるようにする（ADR-038）。
 * HTTP 非 2xx（status が取れる）は呼び出し側で toApiError に通す。ここでは投げ直さず Response を返す。 */
async function fetchOrUnreachable(path: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(`${API_BASE}${path}`, init);
  } catch (e) {
    // TypeError = ネットワーク到達不能（CORS / 接続拒否 / DNS）。AbortError もここに来る。
    const url = resolveUrl(path);
    if (e instanceof DOMException && e.name === "AbortError") {
      throw new ApiError(NETWORK_UNREACHABLE, `${url} への接続を中断（タイムアウト等）`);
    }
    const reason = e instanceof Error ? e.message : String(e);
    throw new ApiError(NETWORK_UNREACHABLE, `${url} へ到達不能（${reason}）`);
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

// --- スクリーニング（ADR-031・backend ScreenCriteria/ScreenRow と 1:1）---
// 比率は内部 0..1（配当利回り）。UI でのみ ×100 して %（ADR-008）。市場跨ぎはしない（日本株専用）。

/** スクリーニング条件。全フィールド任意。保存フィルタもこの形を持つ。 */
export type ScreenCriteria = {
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

// 生 fetch をコンポーネントに散らさず、この 4 ヘルパに集約する（ADR-005）。
// 失敗は detail を載せた ApiError を throw（呼び出し側で status 分岐可能）。
// GET は signal を受けて fetch に渡す（AbortController でキャンセル＝useApi 連携）。
async function getJSON<T>(path: string, signal?: AbortSignal): Promise<T> {
  const r = await fetchOrUnreachable(path, {
    headers: { Accept: "application/json" },
    signal,
  });
  if (!r.ok) throw await toApiError(r);
  return r.json() as Promise<T>;
}

// POST / PUT / DELETE ヘルパ（getJSON と同じエラー処理・ADR-005）。
// Content-Type: application/json を付与し、レスポンス body を T として返す。
async function postJSON<T>(path: string, body: unknown): Promise<T> {
  const r = await fetchOrUnreachable(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw await toApiError(r);
  return r.json() as Promise<T>;
}

async function putJSON<T>(path: string, body: unknown): Promise<T> {
  const r = await fetchOrUnreachable(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw await toApiError(r);
  return r.json() as Promise<T>;
}

async function patchJSON<T>(path: string, body: unknown): Promise<T> {
  const r = await fetchOrUnreachable(path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw await toApiError(r);
  return r.json() as Promise<T>;
}

async function del<T>(path: string): Promise<T> {
  const r = await fetchOrUnreachable(path, {
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
export function createFilter(input: SavedFilterInput): Promise<SavedFilter> {
  return postJSON<SavedFilter>("/screening-filters", input);
}

/** 保存フィルタを更新（PUT /screening-filters/{id}）。 */
export function updateFilter(id: number, input: SavedFilterInput): Promise<SavedFilter> {
  return putJSON<SavedFilter>(`/screening-filters/${id}`, input);
}

/** 保存フィルタを削除（DELETE /screening-filters/{id}）。 */
export function deleteFilter(id: number): Promise<{ ok: boolean }> {
  return del<{ ok: boolean }>(`/screening-filters/${id}`);
}

// シグナル（Trend Vane・Phase 1・docs/api.md §5.1・docs/phase-specs/phase1-spec.md §5.1）。
// 夜間バッチが事前計算した「事実」を読むだけ（AI には計算させない＝ADR-014）。
// score は連続値 0..1、絞り込みは読み取り側が行う（ADR-026）。型は backend Pydantic と 1:1。
export type SignalType = "momentum" | "volume_spike" | "ai_alpha" | "lead_lag";

export interface SignalPayload {
  label?: string; // 一覧の「シグナル」列の短文（quant が格納）
  change_5d?: number | null; // 5日騰落率（符号付き小数・quant が格納）
  predicted_excess_return_60d?: number | null; // ai_alpha: 予測 60 営業日 対TOPIX 超過リターン（Phase 5）
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

/** 手動バッチ起動レスポンス（POST /batch/run・batch.py）。非同期受付で 202（裁定 L-2）。 */
export interface BatchRunResponse {
  started: boolean;
  job_id: string | null;
}

/** 夜間バッチを手動起動（Phase 1・POST /batch/run・ADR-011「2つの起動口」）。
 * 既に実行中なら backend が 409 を返し ApiError（detail）が throw される。
 * full_backfill=true で BACKFILL_YEARS 分を頭から取り直す（初回/復旧）。 */
export function runBatch(fullBackfill = false): Promise<BatchRunResponse> {
  return postJSON<BatchRunResponse>("/batch/run", { full_backfill: fullBackfill });
}

/** バッチ実行状態（GET /batch/status・batch.py・ADR-036）。batch.state と 1:1。 */
export interface BatchStatusResponse {
  running: boolean;
  current_job: string | null; // 実行中ジョブの短名（idle / 開始直後は null）
  started_at: string | null; // 走行開始時刻（ISO8601・UTC）
  full_backfill: boolean; // full（初回/復旧）か差分か
  stop_requested: boolean; // 停止要求済みか（次のジョブ境界で止まる）
}

/** バッチ停止レスポンス（POST /batch/stop・ADR-036）。 */
export interface BatchStopResponse {
  stopping: boolean; // 停止要求を受理したか（実行中でなければ false）
}

/** 現在のバッチ実行状態を取得（ADR-036・WebUI がポーリングして進捗・停止可否を出す）。
 * cron・/batch/run・CLI --nightly のどの口で走っていても同じ状態を映す（ADR-011）。 */
export function getBatchStatus(signal?: AbortSignal): Promise<BatchStatusResponse> {
  return getJSON<BatchStatusResponse>("/batch/status", signal);
}

/** 走行中バッチに停止を要求（協調キャンセル・ADR-036）。今のジョブを終えてから止まる。
 * 差分・フルどちらの走行でも効く。実行中でなければ stopping=false。 */
export function stopBatch(): Promise<BatchStopResponse> {
  return postJSON<BatchStopResponse>("/batch/stop", {});
}

/** Discord 疎通テストのレスポンス（POST /diagnostics/discord-test・diagnostics.py）。 */
export interface DiscordTestResponse {
  enabled: boolean; // Webhook URL が設定されているか（false なら未設定で送らない）
  sent: boolean; // 実際に 2xx で届いたか（enabled=false のときは常に false）
}

/** Discord にテスト通知を 1 通送る（ADR-011「複数の起動口」・冪等回避＝毎回飛ぶ）。
 * enabled=false は未設定、sent=false は送信失敗。両者を呼び出し側で区別して表示する。 */
export function sendDiscordTest(): Promise<DiscordTestResponse> {
  return postJSON<DiscordTestResponse>("/diagnostics/discord-test", {});
}

/** J-Quants 疎通テストのレスポンス（POST /diagnostics/jquants-test・ADR-008/036）。 */
export interface JquantsTestResponse {
  configured: boolean; // API キーが設定されているか（false なら未設定）
  ok: boolean; // 認証が通り 1 銘柄取れたか（configured=false のときは常に false）
  detail: string; // 人間向けメッセージ（成功＝会社名／失敗＝エラー要旨）
}

/** J-Quants V2 に認証ピングを 1 発投げる（DB 非依存・ADR-011「複数の起動口」）。
 * configured=false は未設定、ok=false は疎通失敗。detail を呼び出し側で表示する。 */
export function sendJquantsTest(): Promise<JquantsTestResponse> {
  return postJSON<JquantsTestResponse>("/diagnostics/jquants-test", {});
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

// --- Phase 4 型定義（phase4-spec.md §5・REST 担当の申し送りが実契約）---
// 型は backend Pydantic と 1:1（フィールド名・null 許容を厳密に）。stale は backend 算出（21日・L-22）。

/** watchlist 1 件（spec §5.1・夜の巡回対象・最終調査日の起点）。
 * stale は backend が per-row interval_days で算出済み。フロントで再計算しない。
 * last_investigated_at は stock_dossiers JOIN（未調査は null）。 */
export interface WatchlistItem {
  id: number;
  code: string;
  company_name: string | null;
  note: string | null;
  added_at: string;
  last_investigated_at: string | null; // 未調査は null（一覧の「最終調査日」）
  interval_days: number; // 銘柄ごとの調査間隔（日・既定 21・常に非 null）。stale 算出の基準。
  stale: boolean; // backend 算出（per-row interval_days 超過）
}

/** `GET /watchlist` レスポンス（spec §5.1・items ラッパ）。 */
export interface WatchlistResponse {
  items: WatchlistItem[];
}

/** ドシエのソース台帳 1 件（spec §5.2・本文は持たず要約＋URL のみ＝ADR-020）。 */
export interface DossierSource {
  id: number;
  source_type: string; // "news" / "filing" 等
  url: string;
  title: string | null;
  summary: string | null;
  published_at: string | null;
}

/** ドシエ（spec §5.2・1 銘柄 1 行の living document）。
 * 未調査でも 200 で返る（summary_md=""・sources=[]・last_investigated_at=null）。
 * 未調査判定は last_investigated_at === null（REST 担当の申し送り）。 */
export interface Dossier {
  code: string;
  summary_md: string; // AI 生成 markdown（react-markdown + rehype-sanitize で描画・L-24）
  key_facts: Record<string, unknown> | null; // PER/成長率等（出所は Tool の事実・ADR-014）
  last_investigated_at: string | null; // null は未調査
  updated_at: string | null;
  sources: DossierSource[];
}

/** `POST /dossiers/{code}/investigate` レスポンス（spec §5.2・調査後の最新ドシエ）。 */
export interface InvestigateResult {
  dossier: Dossier;
}

/** 環境変数キーの充足状況（config.py env_status・/health の env 要素）。 */
export interface EnvStatus {
  set: boolean;
  required_from_phase: number;
}

/** `GET /health` レスポンス（疎通確認・Settings の env 詳細表示・main.py）。 */
export interface HealthResponse {
  status?: string;
  service?: string;
  version?: string;
  phase?: number;
  db?: string;
  env?: Record<string, EnvStatus>; // 各キーの set 状況（discord_webhook_url 等）
  [k: string]: unknown;
}

/** /health の疎通確認に掛けるタイムアウト（Pi 冷間起動・無応答で赤に倒すまでの上限・ADR-038）。 */
const HEALTH_TIMEOUT_MS = 5000;

/** backend への疎通確認（Topbar の健全性バッジ。失敗は ApiError を throw）。
 * 内部タイムアウト（HEALTH_TIMEOUT_MS）用の AbortController を併用し、呼び出し側の signal とも連動させる
 * （どちらが abort しても fetch を止める）。タイムアウト発火・到達不能はいずれも getJSON が
 * ApiError(status=0) に翻訳し、message に「どこへ繋ごうとして失敗したか」を載せる（ADR-038）。 */
export function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  const timeoutCtrl = new AbortController();
  const timer = setTimeout(() => {
    // 何秒で諦めたかをメッセージに残す（getJSON 側で URL と結合される）。
    timeoutCtrl.abort(new DOMException(`${HEALTH_TIMEOUT_MS}ms タイムアウト`, "AbortError"));
  }, HEALTH_TIMEOUT_MS);

  // 呼び出し側 signal が先に abort したらタイムアウト用 controller も止める（fetch を確実に中断）。
  const onCallerAbort = () => timeoutCtrl.abort();
  signal?.addEventListener("abort", onCallerAbort, { once: true });

  return getJSON<HealthResponse>("/health", timeoutCtrl.signal).finally(() => {
    clearTimeout(timer);
    signal?.removeEventListener("abort", onCallerAbort);
  });
}

// --- Phase 4 API 関数（phase4-spec.md §5・既存 fetch ヘルパと同じ流儀）---
// すべて `lib/api.ts` に集約（ADR-005）。DB に触れない。エラーは detail を throw する。

/** watchlist 一覧（spec §5.1）。stale は backend 算出（21 日・L-22）。 */
export function getWatchlist(signal?: AbortSignal): Promise<WatchlistResponse> {
  return getJSON<WatchlistResponse>("/watchlist", signal);
}

/** watchlist へ追加（spec §5.1）。body は {code, note?}・単体 WatchlistItem を返す。
 * 重複（UNIQUE code）でも backend は 200 で既存行を返す（409 は来ない）。 */
export function addWatchlist(code: string, note?: string): Promise<WatchlistItem> {
  return postJSON<WatchlistItem>("/watchlist", { code, note: note ?? null });
}

/** watchlist から削除（spec §5.1）。存在しない id でも 200 で {ok:true}。 */
export function removeWatchlist(id: number): Promise<{ ok: boolean }> {
  return del<{ ok: boolean }>(`/watchlist/${id}`);
}

/** 銘柄ごとの調査間隔を更新（ADR-033・PATCH /watchlist/{code}）。
 * intervalDays >= 1（違反は backend 422）。更新後の WatchlistItem を返す（stale も再算出済み）。
 * 未登録 code は 404。 */
export function updateWatchlistInterval(
  code: string,
  intervalDays: number,
): Promise<WatchlistItem> {
  return patchJSON<WatchlistItem>(`/watchlist/${encodeURIComponent(code)}`, {
    interval_days: intervalDays,
  });
}

/** ドシエ取得（spec §5.2）。未調査でも 200（summary_md=""・sources=[]・last_investigated_at=null）。 */
export function getDossier(code: string, signal?: AbortSignal): Promise<Dossier> {
  return getJSON<Dossier>(`/dossiers/${encodeURIComponent(code)}`, signal);
}

/** 銘柄を調査（spec §5.2・investigate_stock(mode="chat") 共用パイプライン＝ADR-020）。
 * 同期で完了まで待つためレスポンスが遅い＝呼び元はローディング表示必須（L-23）。 */
export function investigateStock(code: string): Promise<InvestigateResult> {
  return postJSON<InvestigateResult>(`/dossiers/${encodeURIComponent(code)}/investigate`, {});
}

// --- ADR-034 一般ニュース（銘柄に紐づかない別系統）---
// backend の GET /general-news（routers/general_news.py）と 1:1。本文は持たず要約＋URL のみ。

export interface GeneralNewsItem {
  url: string;
  title: string | null;
  summary: string | null;
  published_at: string | null;
  source_type: string | null;
  category: string;
}

export interface GeneralNewsCategory {
  label: string;
  items: GeneralNewsItem[];
}

export interface GeneralNewsResponse {
  categories: GeneralNewsCategory[];
}

/** 一般ニュースをカテゴリ別に取得（ADR-034）。台帳が空でも 200（categories=[]）。 */
export function getGeneralNews(signal?: AbortSignal): Promise<GeneralNewsResponse> {
  return getJSON<GeneralNewsResponse>("/general-news", signal);
}
