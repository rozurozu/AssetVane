import { getJSON } from "./_client";

// AI Advisor 判断軌跡の観測層（ADR-092・GET /advisor/turns）。型は backend の Pydantic と 1:1。
// track_record（結果の質）と対で「AI が実際にどう判断したか（プロセスの質）」を数字で見る。

/** tool_sequence の 1 件（呼んだ Tool 名＋引数・結果値なし＝ADR-025）。 */
export type TurnToolCall = {
  name: string;
  args: Record<string, unknown>;
};

/** advisor_turns の 1 行（列＋tool_sequence 由来の read-time 導出フラグ・ADR-092）。 */
export type TurnItem = {
  id: number;
  created_at: string | null;
  source: string; // 'chat'/'nightly'/'reviewer'/'profiler'/'skeptic'
  model: string | null;
  tool_sequence: TurnToolCall[];
  n_rounds: number;
  truncated: boolean;
  called_propose_trade: boolean;
  propose_trade_disciplined: boolean | null; // null=非該当（propose_trade を呼んでいない）
  called_submit_journal: boolean;
  called_submit_notable: boolean;
};

/** 面別の判断軌跡サマリ（aggregate_turns と 1:1・ADR-092）。 */
export type TurnsSummaryRow = {
  source: string;
  n_turns: number;
  avg_rounds: number | null;
  truncated_rate: number | null; // 0..1
  n_propose_trade: number;
  disciplined_rate: number | null; // 起票ターンのうち 4 属性全備の割合（NULL 無視・ADR-084）
};

/** GET /advisor/turns のレスポンス（面別サマリ＋直近の軌跡）。 */
export type TurnsResponse = {
  summary: TurnsSummaryRow[];
  recent: TurnItem[];
};

/** AI Advisor の判断軌跡（面別サマリ＋直近）を取得（ADR-092・観測層）。
 * source を渡すと recent をその面に絞る（summary は全面のまま＝比較が目的）。 */
export function getAdvisorTurns(source?: string, signal?: AbortSignal): Promise<TurnsResponse> {
  const qs = source ? `?source=${encodeURIComponent(source)}` : "";
  return getJSON<TurnsResponse>(`/advisor/turns${qs}`, signal);
}
