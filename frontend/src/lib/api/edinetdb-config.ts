import { getJSON, postJSON, putJSON } from "./_client";

// EDINET DB（edinetdb.jp）接続設定（api_key/plan を DB+WebUI で管理・ADR-064・docs/api.md
// 「EDINET DB 設定」）。型は backend Pydantic（routers/edinetdb_config.py）と 1:1。公式 EDINET
// （env の edinet_api_key）とは別系統。#2 売掛/在庫の質の財務取得に使う。api_key は GET で常にマスク。

/** 契約プラン名（services/edinetdb_config._PLAN_LIMITS と 1:1・ドロップダウンと一致）。 */
export type EdinetdbPlan = "free" | "pro";

/** EDINET DB 接続の現在値（api_key はマスク済み・ADR-064）。 */
export type EdinetdbConfig = {
  api_key_masked: string; // "…AB12"（末尾4桁）・空鍵は ""
  has_api_key: boolean;
  plan: string; // free/pro
  configured: boolean; // api_key があり #2 取得が動くか
};

/** EDINET DB 疎通テストのレスポンス（POST /diagnostics/edinetdb-test・ADR-064）。 */
export type EdinetdbTestResponse = {
  configured: boolean; // API キーが設定されているか（false なら未設定）
  ok: boolean; // 認証が通り会社一覧が取れたか（configured=false のときは常に false）
  detail: string; // 人間向けメッセージ（成功＝収載社数＋月残予算／失敗＝エラー要旨）
};

/** EDINET DB 接続の現在値（api_key はマスク済み）。 */
export function getEdinetdbConfig(signal?: AbortSignal): Promise<EdinetdbConfig> {
  return getJSON<EdinetdbConfig>("/edinetdb/config", signal);
}

/** EDINET DB 接続を更新（api_key は空送信で据え置き＝write-only）。 */
export function updateEdinetdbConfig(body: {
  api_key?: string;
  plan?: EdinetdbPlan;
}): Promise<EdinetdbConfig> {
  return putJSON<EdinetdbConfig>("/edinetdb/config", body);
}

/** edinetdb.jp に認証ピングを 1 発投げる（DB 非依存・ADR-011「複数の起動口」）。
 * configured=false は未設定、ok=false は疎通失敗。detail を呼び出し側で表示する。 */
export function sendEdinetdbTest(): Promise<EdinetdbTestResponse> {
  return postJSON<EdinetdbTestResponse>("/diagnostics/edinetdb-test", {});
}
