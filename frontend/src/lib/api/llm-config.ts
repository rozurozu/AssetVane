import { del, getJSON, postJSON, putJSON } from "./_client";

// LLM プロバイダ複数登録・面別 provider/model 設定（ADR-058・docs/api.md「LLM 設定」）。
// 型は backend Pydantic（routers/llm_config.py）と 1:1。api_key は GET では生で来ず常にマスク済み。

/** 登録済み provider（鍵あり・OpenAI 互換）。codex はここに現れない（鍵なし組み込み）。 */
export type Provider = {
  id: number;
  name: string;
  base_url: string;
  api_key_masked: string; // "…AB12"（末尾4桁）・空鍵は ""
  has_api_key: boolean;
  default_model: string;
};

/** 面（chat/nightly/dossier/tagger）の現在割当。 */
export type FaceConfig = {
  face: string;
  provider_id: number | null; // null=未設定 / 0=codex / >0=Provider.id
  provider_name: string | null; // codex は "codex"・宙づりは null
  model: string;
  configured: boolean; // resolve_face が通るか（=その面の LLM が動くか）
};

/** provider 疎通テストの結果（200＋フラグ）。 */
export type ProviderTestResult = {
  ok: boolean;
  detail: string;
};

/** provider 一覧（api_key はマスク済み）。 */
export function getProviders(signal?: AbortSignal): Promise<Provider[]> {
  return getJSON<Provider[]>("/llm/providers", signal);
}

/** provider を新規登録（name 重複は 409）。 */
export function createProvider(body: {
  name: string;
  base_url: string;
  api_key?: string;
  default_model?: string;
}): Promise<Provider> {
  return postJSON<Provider>("/llm/providers", body);
}

/** provider を部分更新（api_key は空で送ると据え置き＝write-only）。 */
export function updateProvider(
  id: number,
  body: {
    name?: string;
    base_url?: string;
    api_key?: string;
    default_model?: string;
  },
): Promise<Provider> {
  return putJSON<Provider>(`/llm/providers/${id}`, body);
}

/** provider を削除（面が使用中なら 409）。 */
export function deleteProvider(id: number): Promise<{ ok: boolean }> {
  return del<{ ok: boolean }>(`/llm/providers/${id}`);
}

/** provider の /v1/models に疎通テスト（失敗も ok=false で返る）。 */
export function testProvider(id: number): Promise<ProviderTestResult> {
  return postJSON<ProviderTestResult>(`/llm/providers/${id}/test`, {});
}

/** 4 面の現在割当（未設定面も含め必ず 4 件・configured フラグ付き）。 */
export function getFaces(signal?: AbortSignal): Promise<FaceConfig[]> {
  return getJSON<FaceConfig[]>("/llm/faces", signal);
}

/** 面の provider/model 割当を更新（provider_id=0 で codex・null で未設定）。 */
export function updateFace(
  face: string,
  body: { provider_id: number | null; model: string },
): Promise<FaceConfig> {
  return putJSON<FaceConfig>(`/llm/faces/${encodeURIComponent(face)}`, body);
}
