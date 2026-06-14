import { getJSON } from "./_client";

// シグナル（Trend Vane・Phase 1・docs/api.md §5.1・docs/phase-specs/phase1-spec.md §5.1）。
// 夜間バッチが事前計算した「事実」を読むだけ（AI には計算させない＝ADR-014）。
// score は連続値 0..1、絞り込みは読み取り側が行う（ADR-026）。型は backend Pydantic と 1:1。
export type SignalType = "momentum" | "volume_spike" | "ai_alpha" | "lead_lag";

export type SignalPayload = {
  label?: string; // 一覧の「シグナル」列の短文（quant が格納）
  change_5d?: number | null; // 5日騰落率（符号付き小数・quant が格納）
  predicted_excess_return_60d?: number | null; // ai_alpha: 予測 60 営業日 対TOPIX 超過リターン（Phase 5）
  [k: string]: unknown; // momentum/volume_spike の type 固有指標（quant 確定）
};

export type Signal = {
  code: string;
  company_name: string | null; // signals JOIN stocks（ルータ補完）
  signal_type: SignalType;
  score: number; // 0..1
  payload: SignalPayload;
};

export type SignalsResponse = {
  date: string; // 実際に返した算出日（最新解決後）
  is_delayed: boolean; // 遅延フラグ（横断・Free=true）
  signals: Signal[]; // score 降順
};

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
