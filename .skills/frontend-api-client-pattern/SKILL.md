---
name: frontend-api-client-pattern
description: frontend から backend を呼ぶ処理（lib/api/ パッケージ）を追加・変更するとき必ず読む（ブラウザ fetch 一本化・型を Pydantic と 1:1）。
---

# frontend API クライアント規約

**Next は UI 専用。DB に触らず、backend とのやり取りは `lib/api/` 経由のブラウザ fetch に一本化する**（ADR-005）。型も `lib/api/` に集約し、backend の Pydantic モデルと 1:1 で対応させる。コンポーネント/フックは `@/lib/api`（barrel）の関数と型だけを使う（詳細な使い方は [[frontend-component-pattern]] の `useApi`）。`lib/api/` の内部構成は後述の「ファイル構成」を参照。

## 絶対にやらないこと（ADR-005）

- **Server Component での `await fetch()` データ取得**（page/layout のサーバー実行でデータを取る）。一般的な Next 標準パターンだが採用しない。理由: データ取得経路が「ブラウザ fetch」と「サーバー fetch」の 2 系統になり、CORS 設計・秘密情報の置き場・通信前提が崩れる。
- **Server Actions / Route Handlers（`app/api/route.ts`）でのデータ取得・ミューテーション**。Next に「DB/バックエンドへの第二経路」を作らない。書き込み・取得はすべて FastAPI に集約。
- **Next 側に DB アクセス（Prisma 等）を足す**。

データ非依存の静的シェルを Server Component に保つのは OK（[[frontend-component-pattern]]）。禁じるのは「Next サーバーでのデータ取得・DB アクセス」。

## 接続先と秘密情報

- 接続先は**相対パス `/api`**（同一オリジン化＝ADR-037）。ブラウザは自分のオリジンの `/api/*` だけを叩き、Next の rewrites（`next.config.ts`）が裏で backend へ素通しする。**ブラウザは backend のホストを知らない**ので CORS も API_URL 焼き込みも不要。rewrites の転送先だけ `BACKEND_ORIGIN`（既定 `http://backend:8000`・ホスト直 dev は `http://localhost:8000`）で決まるが、これは Next サーバ側の話で `lib/api/` は触らない。
- **秘密情報（J-Quants / LLM キー等）を frontend に置かない**。それらは backend の `.env` のみ。

```ts
export const API_BASE = "/api";
```

## ファイル構成（`lib/api/` パッケージ）

`lib/api/` はドメイン別モジュールに分割し、`index.ts`（barrel）で再エクスポートする。**importer は `@/lib/api` を named import するだけ**で、内部分割を意識しない。

```
lib/api/
  _client.ts   共通 fetch 基盤（API_BASE / ApiError / getJSON / postJSON /
               putJSON / patchJSON / del）。自己完結し他モジュールを import しない。
  <domain>.ts  ドメイン別（portfolio / stocks / advisor / watchlist / news /
               funds / us / batch / signals / lead-lag …）。型と関数を同居。
  index.ts     barrel
```

- **各ドメインモジュールは `_client.ts` だけを import する**。型はそのドメインのファイルに同居させ、**ドメイン間で型を直接 import しない**（cross-domain 参照を作らない＝循環依存を避ける）。共用したくなった型は配置ドメインを decide して同居させ、利用側は `@/lib/api`（barrel）から取る。
- **`index.ts` はドメインを `export *`**、`_client.ts` だけは内部ヘルパ（`getJSON` 等）を外へ漏らさないため **公開分のみ明示 re-export**（`export { API_BASE, ApiError } from "./_client";`）。`export *` でドメインを束ねるのは、型名・関数名が全モジュールで一意だから（衝突したら名前を直す）。
- backend の `db/repo/` パッケージ分割（`_common.py` ＋ ドメイン別 ＋ 明示 re-export）と同じ思想。

## fetch ラッパと ApiError

生 fetch をコンポーネントに散らさない。共通ラッパ（`_client.ts`）を 1 つ置き、各エンドポイント関数はそれを呼ぶ。

```ts
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit & { signal?: AbortSignal }): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!res.ok) {
    // FastAPI は {detail: "..."} で返す。detail を拾ってメッセージにする。
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      // JSON でない/空ボディはステータス文のまま
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}
```

規約:
- **エラーは `throw`（`ApiError`）**。戻り値に `{ ok, error }` を混ぜない。`useApi`/呼び出し側が `catch` する（UI は `error: Error | null` で受ける）。
- **`signal` を受け取り fetch に渡す**（`useApi` の `AbortController` でキャンセルできるように）。
- レスポンスのエラーメッセージは FastAPI の `{ "detail": ... }` から拾う（router 境界で `HTTPException(detail=...)` に翻訳されている＝[[backend-router-pattern]]）。

## エンドポイント関数

各エンドポイントに薄い関数を 1 つ。GET は `signal` を受け、POST/PUT は body を受ける。

```ts
export function getQuotes(code: string, signal?: AbortSignal): Promise<Quote[]> {
  return request<Quote[]>(`/quotes/${code}`, { signal });
}

export function postTransaction(input: TransactionInput): Promise<TransactionResult> {
  return request<TransactionResult>("/transactions", {
    method: "POST",
    body: JSON.stringify(input),
  });
}
```

- **関数名は 2 層ルールで決める**:
  - **リソースの CRUD** は **HTTP メソッド接頭辞**に統一する（`getXxx`/`postXxx`/`putXxx`/`patchXxx`/`deleteXxx`）。同義動詞で揺らさない（`list`/`add`/`create`/`update`/`remove` を使わず、`get`/`post`/`put`/`delete` に寄せる）。例: 取得=`getUsTransactions`、作成=`postWatchlist`、更新=`putTransaction`、部分更新=`patchWatchlistInterval`、削除=`deleteFilter`。
  - **アクションエンドポイント**（リソースの単純 CRUD ではない操作＝承認・最適化・調査・バッチ起動/停止・チャット・スクリーニング・検索・取り込み等）は **ドメイン動詞を許容**する。HTTP メソッドに潰すと意図が消えるため。例: `approveProposal`/`rejectProposal`/`optimizePortfolio`/`investigateStock`/`runBatch`/`stopBatch`/`sendChat`/`screenStocks`/`searchNews`/`ingestNews`。
- パスは backend の `docs/api.md` 契約に一致させる。

## 型は Pydantic と 1:1

- レスポンス/リクエストの型を **`lib/api/` の各ドメインモジュールに `export type` で集約**し、backend の Pydantic モデルと**フィールド名・null 許容・単位まで 1:1** に対応させる。型をコンポーネント側で個別定義しない。**`interface` は使わず `type` に統一**する（union 型と書式を揃えるため・declaration merging を持ち込まない）。
- **比率・weight・current/limit は内部 0..1**。UI でのみ ×100 して % 表示（ADR-008）。型コメントに単位を明記する（`weight: number | null; // 株式内比率 0..1（UI で ×100）`）。
- backend の `Literal`（signal_type 等）は TS の union（`"momentum" | "volume_spike"`）に対応させる。
- backend のモデルを変えたら `lib/api/` の型も同じコミットで揃える（契約のズレを残さない）。

## チェックリスト

- [ ] 取得・送信は `lib/api/` 経由のブラウザ fetch のみ（Server 取得 / Server Action / Route Handler / DB アクセスを足していない）
- [ ] 接続先は相対パス `/api`（同一オリジン化＝ADR-037）。秘密情報を frontend に置いていない
- [ ] 共通ラッパ（`_client.ts`）経由で、エラーは `ApiError` を throw（`{ok,error}` を返していない）
- [ ] GET 関数は `signal` を受けて fetch に渡す（キャンセル対応）
- [ ] エラーメッセージは FastAPI の `detail` を拾っている
- [ ] 関数名は 2 層ルール（リソース CRUD＝HTTP メソッド接頭辞 `get/post/put/patch/delete`、アクション＝ドメイン動詞）
- [ ] 新ドメインを足すなら `lib/api/<domain>.ts`＋`index.ts` に `export *`。ドメインは `_client.ts` だけを import（cross-domain 型参照を作らない）
- [ ] 型は `lib/api/` に集約し Pydantic と 1:1（フィールド名・null・単位）。比率は 0..1 でコメント明記。宣言は `export type`（`interface` 不使用）
- [ ] backend のモデル変更時に `lib/api/` の型も同コミットで更新した
