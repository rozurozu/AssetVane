import { del, getJSON, patchJSON, postJSON } from "./_client";

// --- Phase 4 型定義（phase4-spec.md §5・REST 担当の申し送りが実契約）---
// 型は backend Pydantic と 1:1（フィールド名・null 許容を厳密に）。stale は backend 算出（21日・L-22）。

/** watchlist 1 件（spec §5.1・夜の巡回対象・最終調査日の起点）。
 * stale は backend が per-row interval_days で算出済み。フロントで再計算しない。
 * last_investigated_at は stock_dossiers JOIN（未調査は null）。 */
export type WatchlistItem = {
  id: number;
  code: string;
  company_name: string | null;
  note: string | null;
  added_at: string | null; // backend は str | None（未設定は null）
  last_investigated_at: string | null; // 未調査は null（一覧の「最終調査日」）
  interval_days: number; // 銘柄ごとの調査間隔（日・既定 21・常に非 null）。stale 算出の基準。
  stale: boolean; // backend 算出（per-row interval_days 超過）
};

/** `GET /watchlist` レスポンス（spec §5.1・items ラッパ）。 */
export type WatchlistResponse = {
  items: WatchlistItem[];
};

/** ドシエのソース台帳 1 件（spec §5.2・本文は持たず要約＋URL のみ＝ADR-020）。 */
export type DossierSource = {
  id: number;
  source_type: string; // "news" / "filing" 等
  url: string;
  title: string | null;
  summary: string | null;
  published_at: string | null;
};

/** ドシエ（spec §5.2・1 銘柄 1 行の living document）。
 * 未調査でも 200 で返る（summary_md=""・sources=[]・last_investigated_at=null）。
 * 未調査判定は last_investigated_at === null（REST 担当の申し送り）。 */
export type Dossier = {
  code: string;
  summary_md: string; // AI 生成 markdown（react-markdown + rehype-sanitize で描画・L-24）
  key_facts: Record<string, unknown> | null; // PER/成長率等（出所は Tool の事実・ADR-014）
  last_investigated_at: string | null; // null は未調査
  updated_at: string | null;
  sources: DossierSource[];
};

/** `POST /dossiers/{code}/investigate` レスポンス（spec §5.2・調査後の最新ドシエ）。 */
export type InvestigateResult = {
  dossier: Dossier;
};

// --- Phase 4 API 関数（phase4-spec.md §5・既存 fetch ヘルパと同じ流儀）---
// すべて `lib/api.ts` に集約（ADR-005）。DB に触れない。エラーは detail を throw する。

/** watchlist 一覧（spec §5.1）。stale は backend 算出（21 日・L-22）。 */
export function getWatchlist(signal?: AbortSignal): Promise<WatchlistResponse> {
  return getJSON<WatchlistResponse>("/watchlist", signal);
}

/** watchlist へ追加（spec §5.1）。body は {code, note?}・単体 WatchlistItem を返す。
 * 重複（UNIQUE code）でも backend は 200 で既存行を返す（409 は来ない）。 */
export function postWatchlist(code: string, note?: string): Promise<WatchlistItem> {
  return postJSON<WatchlistItem>("/watchlist", { code, note: note ?? null });
}

/** watchlist から削除（spec §5.1）。存在しない id でも 200 で {ok:true}。 */
export function deleteWatchlist(id: number): Promise<{ ok: boolean }> {
  return del<{ ok: boolean }>(`/watchlist/${id}`);
}

/** 銘柄ごとの調査間隔を更新（ADR-033・PATCH /watchlist/{code}）。
 * intervalDays >= 1（違反は backend 422）。更新後の WatchlistItem を返す（stale も再算出済み）。
 * 未登録 code は 404。 */
export function patchWatchlistInterval(code: string, intervalDays: number): Promise<WatchlistItem> {
  return patchJSON<WatchlistItem>(`/watchlist/${encodeURIComponent(code)}`, {
    interval_days: intervalDays,
  });
}

/** ドシエ取得（spec §5.2）。未調査でも 200（summary_md=""・sources=[]・last_investigated_at=null）。 */
export function getDossier(code: string, signal?: AbortSignal): Promise<Dossier> {
  return getJSON<Dossier>(`/dossiers/${encodeURIComponent(code)}`, signal);
}

/** 銘柄を調査（spec §5.2・investigate_stock(mode="chat") 共用パイプライン＝ADR-020）。
 * 同期で完了まで待つためレスポンスが遅い＝呼び元はローディング表示必須（L-23）。 */
export function investigateStock(code: string): Promise<InvestigateResult> {
  return postJSON<InvestigateResult>(`/dossiers/${encodeURIComponent(code)}/investigate`, {});
}
