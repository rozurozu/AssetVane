import { getJSON, putJSON } from "./_client";

// J-Quants 接続設定（api_key/plan を DB+WebUI で管理・ADR-061・docs/api.md「J-Quants 設定」）。
// 型は backend Pydantic（routers/jquants_config.py）と 1:1。api_key は GET では生で来ず常にマスク済み。

/** 契約プラン名（docs/jquants.md のプラン表・ドロップダウンと 1:1）。 */
export type JquantsPlan = "free" | "light" | "standard" | "premium";

/** J-Quants 接続の現在値（api_key はマスク済み・ADR-061）。 */
export type JquantsConfig = {
  api_key_masked: string; // "…AB12"（末尾4桁）・空鍵は ""
  has_api_key: boolean;
  plan: string; // free/light/standard/premium
  configured: boolean; // api_key があり取得が動くか
};

/** J-Quants 接続の現在値（api_key はマスク済み）。 */
export function getJquantsConfig(signal?: AbortSignal): Promise<JquantsConfig> {
  return getJSON<JquantsConfig>("/jquants/config", signal);
}

/** J-Quants 接続を更新（api_key は空送信で据え置き＝write-only）。 */
export function updateJquantsConfig(body: {
  api_key?: string;
  plan?: JquantsPlan;
}): Promise<JquantsConfig> {
  return putJSON<JquantsConfig>("/jquants/config", body);
}
