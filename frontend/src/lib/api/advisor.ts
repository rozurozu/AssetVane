import { getJSON, postJSON, putJSON } from "./_client";

// --- Phase 3 型定義（phase3-spec.md §9.5 / api.md §4・§7・Pydantic と 1:1）---
// 比率系（target_cash_ratio / max_position_weight / sector_caps）はすべて 0..1。
// UI でのみ ×100 して % 表示・保存時 ÷100（ADR-008 / spec §9.2）。

/** 構造化コア（policy の定量レバー・api.md §7 GET /policy）。比率は 0..1。 */
export type PolicyCore = {
  risk_tolerance: string | null; // "低"/"中"/"高"
  time_horizon: string | null; // "短"/"中"/"長"
  target_cash_ratio: number | null; // 0..1（UI で ×100）
  max_position_weight: number | null; // 0..1
  sector_caps: Record<string, number>; // {sector33_code: 0..1}
  target_return: number | null; // 0..1（任意）
  no_leverage: boolean;
  exclusions: string[]; // 除外銘柄コード等
};

/** `GET /policy` レスポンス（core / rationale を分けて返す・api.md §7）。 */
export type Policy = {
  core: PolicyCore;
  rationale: string | null; // 自由文の理念（引用調で表示）
  updated_at: string | null;
};

/** `PUT /policy` リクエスト（core 部分更新・rationale 即時更新・ADR-013 / U-7）。 */
export type PolicyUpdate = {
  core?: Partial<PolicyCore>;
  rationale?: string;
};

/** 投資日記 1 件（spec §8.2・date 降順）。source は夜の自動 / チャット要約昇格（ADR-029）。 */
export type JournalEntry = {
  id: number;
  date: string; // YYYY-MM-DD
  source: "nightly" | "chat";
  observations: string | null; // AI 所見（自由文）
  proposal: string | null; // 当日の提案（自由文 or 参照）
  proposed_policy_change: unknown | null; // JSON {field, from, to, reason}（任意）
  policy_snapshot: unknown | null; // その時点の policy まるごと（差分チップ用）
  llm_model: string | null;
  created_at: string | null;
};

/** `GET /journal` レスポンス（spec §8.2）。 */
export type JournalResponse = {
  entries: JournalEntry[];
};

/** AI 提案 1 件（spec §8.2・承認制・約定はしない＝ADR-001/019）。 */
export type Proposal = {
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
};

/** `GET /proposals` レスポンス（spec §8.2）。 */
export type ProposalsResponse = {
  proposals: Proposal[];
};

/** `POST /proposals/{id}/approve|reject` レスポンス（spec §8.2）。 */
export type ResolveResult = {
  proposal: Proposal;
};

/** 画面コンテキストの主対象（ADR-025・api.md §4・type で code/id を使い分け）。 */
export type FocusRef = {
  type: "stock" | "portfolio" | "signal" | "proposal";
  code?: string; // stock / signal
  id?: number; // portfolio / proposal
};

/** 画面コンテキスト（軽量ヒント・数値は載せない＝ADR-025）。 */
export type ChatContext = {
  page: string; // "stock_detail" / "dashboard" / "signals" / "policy" / ...
  focus?: FocusRef; // 対象が無いページは省略
};

/** チャット 1 ターン（system 不可・user/assistant のみ）。 */
export type ChatMessage = {
  role: "user" | "assistant";
  content: string;
};

/** `POST /chat` リクエスト（spec §6.3・毎ターン全 messages 送信＝ステートレス）。 */
export type ChatRequest = {
  messages: ChatMessage[];
  context?: ChatContext; // ADR-025（数値は載せない）
};

/** AI が呼んだ Tool（UI 可視化用・結果の数値は載せない＝ADR-025）。 */
export type ToolRun = {
  name: string;
  args?: Record<string, unknown> | null;
};

/** `POST /chat` レスポンス（非ストリーミング・spec §4.2/§6.3）。 */
export type ChatResponse = {
  reply: string;
  tool_runs: ToolRun[];
  journal_id?: number | null; // journal に残せたら id（ADR-029・残せなければ null）
  card_ids?: number[]; // propose_card が起票した知識ノート draft の id（ADR-065・無ければ空/省略）
};

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
