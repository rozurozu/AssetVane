import { getJSON, postJSON, putJSON } from "./_client";

// 公式 EDINET（api.edinet-fsa.go.jp）接続設定（api_key を DB+WebUI で管理・ADR-087・docs/api.md
// 「EDINET（公式）設定」）。型は backend Pydantic（routers/edinet_config.py）と 1:1。第三者
// edinetdb.jp（/edinetdb/config）とは別系統＝有報「事業の内容」テキスト源（テーマタグ段階C）。
// plan 概念は無い（公式 EDINET は回数クォータ無し）。api_key は GET で常にマスク。

/** 公式 EDINET 接続の現在値（api_key はマスク済み・ADR-087）。 */
export type EdinetConfig = {
  api_key_masked: string; // "…AB12"（末尾4桁）・空鍵は ""
  has_api_key: boolean;
  configured: boolean; // api_key があり段階C 取得が動くか
};

/** 公式 EDINET 疎通テストのレスポンス（POST /diagnostics/edinet-test・ADR-087）。 */
export type EdinetTestResponse = {
  configured: boolean; // API キーが設定されているか（false なら未設定）
  ok: boolean; // Subscription-Key 認証が通り書類一覧が取れたか（configured=false のときは常に false）
  detail: string; // 人間向けメッセージ（成功＝当日書類件数／失敗＝エラー要旨・貼り間違い検知）
};

/** 公式 EDINET 接続の現在値（api_key はマスク済み）。 */
export function getEdinetConfig(signal?: AbortSignal): Promise<EdinetConfig> {
  return getJSON<EdinetConfig>("/edinet/config", signal);
}

/** 公式 EDINET 接続を更新（api_key は空送信で据え置き＝write-only）。 */
export function updateEdinetConfig(body: { api_key?: string }): Promise<EdinetConfig> {
  return putJSON<EdinetConfig>("/edinet/config", body);
}

/** 公式 EDINET に認証ピングを 1 発投げる（DB 非依存・ADR-011「複数の起動口」）。
 * configured=false は未設定、ok=false は疎通失敗。detail を呼び出し側で表示する。 */
export function sendEdinetTest(): Promise<EdinetTestResponse> {
  return postJSON<EdinetTestResponse>("/diagnostics/edinet-test", {});
}
