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
