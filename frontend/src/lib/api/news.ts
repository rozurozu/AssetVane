import { del, getJSON, postJSON } from "./_client";

// --- ADR-034 一般ニュース（銘柄に紐づかない別系統）---
// backend の GET /general-news（routers/general_news.py）と 1:1。本文は持たず要約＋URL のみ。

export type GeneralNewsItem = {
  url: string;
  title: string | null;
  summary: string | null;
  published_at: string | null;
  source_type: string | null;
  category: string;
};

export type GeneralNewsCategory = {
  label: string;
  items: GeneralNewsItem[];
};

export type GeneralNewsResponse = {
  categories: GeneralNewsCategory[];
};

/** 一般ニュースをカテゴリ別に取得（ADR-034）。台帳が空でも 200（categories=[]）。 */
export function getGeneralNews(signal?: AbortSignal): Promise<GeneralNewsResponse> {
  return getJSON<GeneralNewsResponse>("/general-news", signal);
}

// --- ADR-047 ニュース統合コーパス（news・銘柄/セクター/市場の 3 層）---
// backend の GET/POST/DELETE /news と 1:1。本文は持たず要約＋URL のみ（ADR-020）。
// level は "stock"/"sector"/"market" の 3 層。source='user' のものだけ DELETE 可。

/** ニュース 1 件（ADR-047）。level で 3 層に分かれる。url が "user://" 始まりは手入力で外部リンクなし。 */
export type NewsItem = {
  id: number;
  level: string; // "stock" / "sector" / "market"
  code: string | null; // level=stock のとき銘柄コード（他は null）
  sector17_code: string | null; // level=sector のとき S17 コード（他は null）
  category: string | null; // market/一般カテゴリ（任意）
  source: string | null; // "user"（手入力）/ 取得源（"user" のみ DELETE 可）
  url: string; // 手入力は "user://..."（外部リンク化しない）
  title: string | null;
  summary: string | null;
  published_at: string | null;
};

/** `GET /news` レスポンス（items ラッパ）。 */
export type NewsListResponse = {
  items: NewsItem[];
};

/** `POST /news` リクエスト（本文を要約して取り込む。要約失敗時 502）。 */
export type NewsIngestInput = {
  text: string;
  url?: string | null;
  code?: string | null;
};

/** ニュース一覧（ADR-047）。level/since/limit は指定時のみ query に付与。台帳が空でも 200。 */
export function getNews(
  params?: { level?: string; since?: string; limit?: number },
  signal?: AbortSignal,
): Promise<NewsListResponse> {
  const p = new URLSearchParams();
  if (params?.level) p.set("level", params.level);
  if (params?.since) p.set("since", params.since);
  if (params?.limit != null) p.set("limit", String(params.limit));
  const qs = p.toString();
  return getJSON<NewsListResponse>(`/news${qs ? `?${qs}` : ""}`, signal);
}

/** `GET /news/search` レスポンス（ADR-045・意味検索）。
 * items は NewsItem と同型。機能オフ/sqlite-vec 未ロード等で検索できないときは items=[] ＋
 * reason に理由が入る（200・UI を壊さない＝backend NewsSearchResponse と 1:1）。 */
export type NewsSearchResponse = {
  items: NewsItem[];
  reason?: string | null;
};

/** ニュース意味検索（ADR-045・GET /news/search・q 必須）。
 * level/since/until/limit は指定時のみ query に付与（code/sector17_code も契約上は受けるが今回 UI 未使用）。
 * 検索不能時も 200 で items=[] ＋ reason が返る（呼び出し側は reason を控えめに表示）。 */
export function searchNews(
  params: {
    q: string;
    level?: string;
    code?: string;
    sector17_code?: string;
    since?: string;
    until?: string;
    limit?: number;
  },
  signal?: AbortSignal,
): Promise<NewsSearchResponse> {
  const p = new URLSearchParams();
  p.set("q", params.q);
  if (params.level) p.set("level", params.level);
  if (params.code) p.set("code", params.code);
  if (params.sector17_code) p.set("sector17_code", params.sector17_code);
  if (params.since) p.set("since", params.since);
  if (params.until) p.set("until", params.until);
  if (params.limit != null) p.set("limit", String(params.limit));
  return getJSON<NewsSearchResponse>(`/news/search?${p.toString()}`, signal);
}

/** ニュースを手入力で取り込む（ADR-047・本文を AI 要約。失敗時 502 が detail 付きで throw）。 */
export function ingestNews(input: NewsIngestInput): Promise<NewsItem> {
  return postJSON<NewsItem>("/news", {
    text: input.text,
    url: input.url ?? null,
    code: input.code ?? null,
  });
}

/** ニュースを削除（ADR-047・source='user' 以外は backend が 404）。 */
export function deleteNews(id: number): Promise<{ ok: boolean }> {
  return del<{ ok: boolean }>(`/news/${id}`);
}
