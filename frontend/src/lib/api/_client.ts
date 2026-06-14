// FastAPI（唯一のデータ所有者・ADR-005）への薄いクライアント。
// Next は UI 専用で DB に触らず、すべてこの REST 経由（docs/api.md）。
// 同一オリジン化（ADR-037）: ブラウザは相対パス `/api` を叩き、Next の rewrites（next.config.ts）が
// 裏で backend へ素通しする。ブラウザは backend のホストを知らないので CORS も URL 焼き込みも不要。
export const API_BASE = "/api";

/** API エラー。status 付きで throw する（呼び出し側で `e instanceof ApiError` で分岐できる）。
 * メッセージは FastAPI の `{"detail": "..."}` から拾う（router 境界で HTTPException 翻訳）。
 * status=0 は「ネットワーク到達不能」（CORS・接続拒否・タイムアウト等で fetch 自体が失敗し、
 * HTTP ステータスが取れなかった）を表す約束（ADR-038）。message に解決済み URL を載せる。 */
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** ネットワーク到達不能（status=0）の意味（ADR-038）。CORS・接続拒否・タイムアウトで使う。 */
const NETWORK_UNREACHABLE = 0;

/** path から解決済みのリクエスト URL を組み立てる（エラーメッセージに載せて追跡可能にする・ADR-038）。
 * ブラウザは相対 `/api`（ADR-037）を自オリジンへ解決するので、location.origin を前置する。 */
function resolveUrl(path: string): string {
  const origin = typeof location !== "undefined" ? location.origin : "";
  return `${origin}${API_BASE}${path}`;
}

/** fetch を実行し、ネットワーク到達不能（fetch が TypeError を投げる）を ApiError(status=0) に翻訳する。
 * CORS・接続拒否・DNS 失敗ではブラウザ fetch は status も URL も持たない TypeError を投げるため、
 * ここで解決済み URL を載せた ApiError に翻訳して「どこへ繋ごうとして失敗したか」を追えるようにする（ADR-038）。
 * HTTP 非 2xx（status が取れる）は呼び出し側で toApiError に通す。ここでは投げ直さず Response を返す。 */
async function fetchOrUnreachable(path: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(`${API_BASE}${path}`, init);
  } catch (e) {
    // TypeError = ネットワーク到達不能（CORS / 接続拒否 / DNS）。AbortError もここに来る。
    const url = resolveUrl(path);
    if (e instanceof DOMException && e.name === "AbortError") {
      throw new ApiError(NETWORK_UNREACHABLE, `${url} への接続を中断（タイムアウト等）`);
    }
    const reason = e instanceof Error ? e.message : String(e);
    throw new ApiError(NETWORK_UNREACHABLE, `${url} へ到達不能（${reason}）`);
  }
}

/** レスポンスから detail を取り出して ApiError を作る（4 ヘルパ共通）。 */
async function toApiError(r: Response): Promise<ApiError> {
  const detail = await r
    .json()
    .then((j) => (j as { detail?: string }).detail ?? `HTTP ${r.status}`)
    .catch(() => `HTTP ${r.status}`);
  return new ApiError(r.status, detail);
}

// 生 fetch をコンポーネントに散らさず、この 4 ヘルパに集約する（ADR-005）。
// 失敗は detail を載せた ApiError を throw（呼び出し側で status 分岐可能）。
// GET は signal を受けて fetch に渡す（AbortController でキャンセル＝useApi 連携）。
export async function getJSON<T>(path: string, signal?: AbortSignal): Promise<T> {
  const r = await fetchOrUnreachable(path, {
    headers: { Accept: "application/json" },
    signal,
  });
  if (!r.ok) throw await toApiError(r);
  return r.json() as Promise<T>;
}

// POST / PUT / DELETE ヘルパ（getJSON と同じエラー処理・ADR-005）。
// Content-Type: application/json を付与し、レスポンス body を T として返す。
export async function postJSON<T>(path: string, body: unknown): Promise<T> {
  const r = await fetchOrUnreachable(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw await toApiError(r);
  return r.json() as Promise<T>;
}

export async function putJSON<T>(path: string, body: unknown): Promise<T> {
  const r = await fetchOrUnreachable(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw await toApiError(r);
  return r.json() as Promise<T>;
}

export async function patchJSON<T>(path: string, body: unknown): Promise<T> {
  const r = await fetchOrUnreachable(path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw await toApiError(r);
  return r.json() as Promise<T>;
}

export async function del<T>(path: string): Promise<T> {
  const r = await fetchOrUnreachable(path, {
    method: "DELETE",
    headers: { Accept: "application/json" },
  });
  if (!r.ok) throw await toApiError(r);
  return r.json() as Promise<T>;
}
