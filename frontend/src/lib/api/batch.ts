import { getJSON, postJSON } from "./_client";

/** 手動バッチ起動レスポンス（POST /batch/run・batch.py）。非同期受付で 202（裁定 L-2）。 */
export type BatchRunResponse = {
  started: boolean;
  job_id: string | null;
};

/** 夜間バッチを手動起動（Phase 1・POST /batch/run・ADR-011「2つの起動口」）。
 * 既に実行中なら backend が 409 を返し ApiError（detail）が throw される。
 * full_backfill=true で BACKFILL_YEARS 分を頭から取り直す（初回/復旧）。 */
export function runBatch(fullBackfill = false): Promise<BatchRunResponse> {
  return postJSON<BatchRunResponse>("/batch/run", { full_backfill: fullBackfill });
}

/** EDINET 差分タグ付けを手動起動（テーマタグ段階C・POST /edinet/run-differential・ADR-056）。
 * 夜間と同じ差分（fetch_edinet_descriptions → tag_jp_themes）を run_jobs で回す。進捗は
 * getBatchStatus で追う（夜間バッチと同じ state・ADR-011/036）。既に実行中なら 409（ApiError）。
 * 重い 15ヶ月バックフィルは app.scripts.backfill_edinet 手動のまま（コストガード）。 */
export function runEdinetDifferential(): Promise<BatchRunResponse> {
  return postJSON<BatchRunResponse>("/edinet/run-differential", {});
}

/** バッチ実行状態（GET /batch/status・batch.py・ADR-036）。batch.state と 1:1。 */
export type BatchStatusResponse = {
  running: boolean;
  current_job: string | null; // 実行中ジョブの短名（idle / 開始直後は null）
  started_at: string | null; // 走行開始時刻（ISO8601・UTC）
  full_backfill: boolean; // full（初回/復旧）か差分か
  stop_requested: boolean; // 停止要求済みか（次のジョブ境界で止まる）
};

/** バッチ停止レスポンス（POST /batch/stop・ADR-036/070）。 */
export type BatchStopResponse = {
  stopping: boolean; // 停止要求を受理したか（ADR-070 で running ゲート撤廃＝常に true）
};

/** 現在のバッチ実行状態を取得（ADR-036/070・WebUI がポーリングして進捗・停止可否を出す）。
 * running 等は同一プロセスのメモリ（best-effort）＝CLI --nightly や dev --reload の別プロセスの
 * 走行は映らない。stop_requested は停止ファイル由来でクロスプロセスに正しい（ADR-070）。 */
export function getBatchStatus(signal?: AbortSignal): Promise<BatchStatusResponse> {
  return getJSON<BatchStatusResponse>("/batch/status", signal);
}

/** 走行中バッチに停止を要求（協調キャンセル・ADR-036/070）。今の単位を終えてから止まる。
 * ADR-070 で running ゲート撤廃＝常に stopping=true（--reload/CLI の別プロセスにも停止を届かせる）。 */
export function stopBatch(): Promise<BatchStopResponse> {
  return postJSON<BatchStopResponse>("/batch/stop", {});
}

/** Discord 疎通テストのレスポンス（POST /diagnostics/discord-test・diagnostics.py）。 */
export type DiscordTestResponse = {
  enabled: boolean; // Webhook URL が設定されているか（false なら未設定で送らない）
  sent: boolean; // 実際に 2xx で届いたか（enabled=false のときは常に false）
};

/** Discord にテスト通知を 1 通送る（ADR-011「複数の起動口」・冪等回避＝毎回飛ぶ）。
 * enabled=false は未設定、sent=false は送信失敗。両者を呼び出し側で区別して表示する。 */
export function sendDiscordTest(): Promise<DiscordTestResponse> {
  return postJSON<DiscordTestResponse>("/diagnostics/discord-test", {});
}

/** J-Quants 疎通テストのレスポンス（POST /diagnostics/jquants-test・ADR-008/036）。 */
export type JquantsTestResponse = {
  configured: boolean; // API キーが設定されているか（false なら未設定）
  ok: boolean; // 認証が通り 1 銘柄取れたか（configured=false のときは常に false）
  detail: string; // 人間向けメッセージ（成功＝会社名／失敗＝エラー要旨）
};

/** J-Quants V2 に認証ピングを 1 発投げる（DB 非依存・ADR-011「複数の起動口」）。
 * configured=false は未設定、ok=false は疎通失敗。detail を呼び出し側で表示する。 */
export function sendJquantsTest(): Promise<JquantsTestResponse> {
  return postJSON<JquantsTestResponse>("/diagnostics/jquants-test", {});
}

/** 環境変数キーの充足状況（config.py env_status・/health の env 要素）。 */
export type EnvStatus = {
  set: boolean;
  required_from_phase: number;
};

/** LLM コストガード状態（ADR-028・Topbar の warn バナー判定）。/health が毎回算出する派生値。 */
export type LlmCostStatus = {
  mode: string; // "off" | "warn" | "block"
  limit_usd: number;
  month_total_usd: number;
  exceeded: boolean; // month_total_usd >= limit_usd
};

/** `GET /health` レスポンス（疎通確認・Settings の env 詳細表示・main.py）。 */
export type HealthResponse = {
  status?: string;
  service?: string;
  version?: string;
  phase?: number;
  db?: string;
  env?: Record<string, EnvStatus>; // 各キーの set 状況（discord_webhook_url 等）
  llm_cost?: LlmCostStatus; // ADR-028: warn バナーの判定材料
  [k: string]: unknown;
};

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
