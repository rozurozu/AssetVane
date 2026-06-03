// FastAPI（唯一のデータ所有者・ADR-005）への薄いクライアント。
// Next は UI 専用で DB に触らず、すべてこの REST 経由（docs/api.md）。
// NEXT_PUBLIC_* はブラウザに焼き込まれるため、ブラウザから到達できる名前を使う（architecture.md §7.1）。
export const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

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
