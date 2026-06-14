// FastAPI（唯一のデータ所有者・ADR-005）への薄いクライアント（barrel）。
// Next は UI 専用で DB に触らず、すべてこの REST 経由（docs/api.md）。
// ドメイン別モジュールへ分割し、ここで再エクスポートする（importer は @/lib/api のまま無改変）。
// 共通 fetch 基盤は _client.ts（getJSON 等の内部ヘルパは外へ出さず、公開 2 つのみ明示 re-export）。
export { API_BASE, ApiError } from "./_client";
export * from "./stocks";
export * from "./signals";
export * from "./batch";
export * from "./portfolio";
export * from "./advisor";
export * from "./watchlist";
export * from "./news";
export * from "./lead-lag";
export * from "./funds";
export * from "./us";
