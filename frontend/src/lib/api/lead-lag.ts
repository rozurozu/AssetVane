import { getJSON } from "./_client";

// --- Phase 7 Sector Lead-Lag（業種リードラグ・GET /lead-lag）---
// backend の Pydantic と 1:1。score 降順ランキング（翌日強含み業種）を読むだけ（AI には計算させない＝ADR-014）。
// is_delayed=true（plan=free か model_as_of が約 3 ヶ月古い）は Free 低信頼バナーの判定材料。

/** リードラグ・ランキング 1 行（業種単位）。score 降順で並ぶ。 */
export type LeadLagRow = {
  code: string;
  label: string;
  score: number;
  signal: number | null; // 生のシグナル値（縮退時は null＝routers/lead_lag.py の LeadLagRankItem）
};

/** リードラグのモデル/検証メタ（品質表示・遅延判定）。JSON キーは "lambda"（予約語だがそのまま）。 */
export type LeadLagMeta = {
  plan: string;
  is_delayed: boolean;
  model_as_of: string | null;
  ic: number | null;
  hit_rate: number | null;
  window: number | null;
  k: number | null;
  lambda: number | null;
};

/** `GET /lead-lag` レスポンス。空台帳なら ranking=[]・as_of=null（200）。 */
export type LeadLagResponse = {
  as_of: string | null;
  ranking: LeadLagRow[];
  meta: LeadLagMeta;
};

/** 業種リードラグのランキング取得（Phase 7・GET /lead-lag）。台帳が空でも 200（ranking=[]）。 */
export function getLeadLag(signal?: AbortSignal): Promise<LeadLagResponse> {
  return getJSON<LeadLagResponse>("/lead-lag", signal);
}
