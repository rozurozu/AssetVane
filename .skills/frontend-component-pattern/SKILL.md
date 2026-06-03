---
name: frontend-component-pattern
description: frontend（Next.js App Router / React 19 / TS / Tailwind v4）でページ・コンポーネント・フック・共有プリミティブを新規作成または修正するときに必ず使う。コンポーネント粒度（page=取得/状態/合成、feature=presentation＋mutation所有、ui=純プリミティブ）、命名規則、"use client" 境界、data 取得フック（useApi）・状態表示（StatusBlock）・整形（lib/format.ts）・共有 UI プリミティブ（components/ui/）の規約を規定する。
---

# frontend コンポーネント規約

Next.js App Router（client fetch のみ・ADR-005）・React 19・TypeScript・Tailwind v4（DESIGN.md トークン）。**DB に触らず、データ取得は `lib/api.ts` 経由のブラウザ fetch のみ**（詳細は [[frontend-api-client-pattern]]）。スタイルトークンの正本は `DESIGN.md`（`surface-1`/`hairline`/`accent`/`num`/`up`/`down`/`warning`/`ink-*` 等）。

## レイヤと責務（粒度の線引き）

3 層で考える。「どこに何を書くか」で迷ったらこの表に戻る。

| レイヤ | 置き場 | export | 責務 | やらないこと |
|---|---|---|---|---|
| **page** | `app/**/page.tsx`・`layout.tsx` | **default**（Next 規約） | データ取得（GET）・状態・タブ・レイアウト合成 | 細かい表示ロジックの直書き（feature/ui に出す） |
| **feature** | `components/<feature>/` | **named** `export function X` | props を受けて描画。フォームを持つものは mutation（POST/PUT）を所有し結果を `onDone`/`onSaved` で親へ返す | 内部での GET（`useEffect` 取得）。データは親から props で受ける |
| **ui** | `components/ui/` | **named** | ドメイン非依存の純粋プリミティブ（Card/DataTable/Field 等） | `lib/api.ts` の import・ドメイン知識・状態取得 |

- **GET（取得）はページが持つ**。`useApi` を呼ぶのはページ（または後述の client コンテナ）。feature コンポーネントは取得済み data を props で受ける。
- **mutation（POST/PUT）はフォームを持つ feature が所有**してよい。送信後は結果をコールバック（`onDone`/`onSaved`）で親へ返し、親が再取得や state 更新を行う。
- **ui プリミティブはドメインを知らない**。`Stock` などの型や `lib/api.ts` を import しない。汎用 props（`title`・`children`・`columns` 等）だけ。

### 例外: 常駐フローティング（Advisor チャット）

全ページ常駐の相談チャット（ADR-024）は root layout 直下に置き、**自前で会話状態・localStorage 永続・送信を持つ**。上の「feature は GET しない」規約の明示的な例外として扱う（理由を冒頭コメントに ADR 番号付きで残す）。

## "use client" 境界

- **`"use client"` はファイル先頭（import より前）**。そこから import される下流すべてがクライアントバンドルに入るので、**境界はツリーの葉に近いほど良い**。
- `layout.tsx` と「データ非依存の静的シェル」（`<html>/<body>`、サイドバー/トップバーの骨格、見出し・装飾）は **Server Component のまま**置ける（DB に触れないので ADR-005 と無矛盾）。`"use client"` を足すのは状態・イベント・ブラウザ fetch を持つ部分だけ。
- **`next/navigation`（`usePathname` 等）と React フックは Client Component 専用**。Server の layout で pathname を読むと client 遷移で stale になるため、アクティブなナビ表示は小さな Client Component に切り出す。

### page.tsx は Client でよい（現行規約）／metadata が要るときだけ Server に割る

このプロジェクトの page は **`"use client"` を付けてデータ取得・状態を自分で持つ**のを標準とする（粒度表の page 責務）。

- トレードオフ: **Client Component は `metadata` を export できない**。そのため per-page の `<title>`/`description` は持てず、メタデータは root layout に集約する（現状の方針）。
- **per-page metadata がどうしても要るページだけ**、page.tsx を Server Component（`metadata` を export・静的シェルのみ）にし、本体を `"use client"` の子コンテナに切り出す（page → client コンテナ → presentational の 3 段）。これは ADR-005 と衝突しない（サーバー側データ取得はしない）。standard ではなく必要時の例外。

## data 取得フック `useApi`

取得の `useState`＋`useEffect`＋`then/catch`＋三分岐を毎ページ手書きしない。共有フック `lib/use-api.ts`（`hooks/` でも可・配置はプロジェクトの既存に合わせる）に一本化する。

```tsx
"use client";
import { useEffect, useState } from "react";

type ApiState<T> = { data: T | null; error: Error | null; loading: boolean };

// fetcher は AbortSignal を受け取り、lib/api.ts の関数を呼ぶ。
// deps にはプリミティブ（code 等）を渡す（fetcher を直接 deps に入れると毎レンダー再実行になる）。
export function useApi<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  deps: readonly unknown[],
): ApiState<T> {
  const [state, setState] = useState<ApiState<T>>({ data: null, error: null, loading: true });
  useEffect(() => {
    let ignore = false; // cleanup 後に届いた応答の state 反映を捨てる（race 回避）
    const ctrl = new AbortController();
    setState((s) => ({ ...s, loading: true, error: null }));
    fetcher(ctrl.signal)
      .then((data) => {
        if (!ignore) setState({ data, error: null, loading: false });
      })
      .catch((e) => {
        if (ctrl.signal.aborted) return; // 中断（AbortError）はエラー表示しない
        if (!ignore) setState({ data: null, error: e as Error, loading: false });
      });
    return () => {
      ignore = true;
      ctrl.abort();
    };
    // biome-ignore lint/correctness/useExhaustiveDependencies: deps は呼び出し側がプリミティブで明示する規約
  }, deps);
  return state;
}
```

規約:
- **`ignore` フラグ（state 反映の抑止）と `AbortController.abort()`（要求のキャンセル）は両方入れる**。役割が別で、React 19 + StrictMode の dev 二重実行と、deps 変化時の race condition の両方を吸収する。
- **deps にはプリミティブを渡す**（`useApi(() => apiGetQuotes(code, s), [code])`）。`fetcher` を deps に入れない。
- 取得は必ずこのフック経由。生 `useEffect` での手書き取得を新規に増やさない。

## 状態表示 `StatusBlock`

「読み込み中… / ⚠ 取得に失敗: … / 空」の三状態を共通コンポーネントにする（`components/ui/StatusBlock.tsx`）。

```tsx
type StatusBlockProps = {
  loading: boolean;
  error: Error | null;
  empty?: boolean;
  emptyText?: string;
  children: React.ReactNode; // 正常時の中身
};
// loading→「読み込み中…」、error→「⚠ 取得に失敗: {message}」(text-down)、
// empty→ emptyText、いずれでもなければ children を描画する。
```

`useApi` と組み合わせ、ページ側は `<StatusBlock loading={loading} error={error} empty={data?.items.length === 0}>…</StatusBlock>` の 1 行で三分岐を畳む。エラー文の配色は `text-down`、補足は `text-ink-subtle`。

## 共有 UI プリミティブ `components/ui/`

繰り返し現れる構造はコンポーネントに抽出する（Tailwind 公式も「繰り返しは `@apply` でなくコンポーネント化」を推奨）。最低限そろえる:

- **`Card`**: `section.rounded-lg.border.border-hairline.bg-surface-1` ＋ ヘッダ（`title`・任意 `meta`）＋ `p-3` 本文。各ページで local 定義しない。
- **`DataTable`**: `columns`（`{ header, align }[]`）と `rows` を受け、`thead`（`h-8 border-b border-hairline ... uppercase`）と行セル（`h-[34px] border-b border-hairline-soft`）の定型を内包。右寄せ数値は `num` クラス。行クリック遷移は `rowHref`/`onRowClick` で渡す。
- **`Field` / `Input` / `Label`**: フォーム入力の `inputCls`/`labelCls` を内包。各フォームで同じ className 文字列をコピペしない。

ルール:
- **クラス共有は React コンポーネント抽出が第一選択**。`@apply` での共通クラス量産はしない（Tailwind v4 では非推奨寄り・テーマ変数解決と性能の問題）。
- **色・寸法は DESIGN.md の `@theme` トークン**を使う。生色（`bg-[#316ff6]`）や任意値 `[...]` のマジック値を散らさない。動的値が要る時だけ `style={{ ... }}` か `bg-(--color-xxx)` で CSS 変数を注入する。
- variant が増えるプリミティブ（Button 等）は「ベースクラス定数＋variant 分岐」を 1 ファイルに閉じ込め、呼び出し側に長い className 列を出さない。

## 整形ヘルパ `lib/format.ts`

`fmtJpy`（`¥` ＋ `toLocaleString("ja-JP")`）・`pct`（0..1 → `%`・桁数指定）・`toPctStr`/`fromPctStr`（% ⇄ 0..1 の入力変換）等を `lib/format.ts` に集約する。**各ページ・各コンポーネントで同名関数を再定義しない**。比率は内部 0..1・UI でのみ ×100（ADR-008）という単位約束も、この層で一貫させる。

## 命名規則

- コンポーネント: **PascalCase**、`export function ComponentName(...)`、**ファイル名＝コンポーネント名**（`TransactionForm.tsx`）。`React.FC` は使わず、`children` は Props に `children: React.ReactNode` を明示。
- Props 型: **`type` エイリアス**（`interface` ではなく `type Props = {...}`）。コンポーネント直前に定義。プロジェクト全体で `type` に統一する。
- フック: `useXxx`（camelCase）。`lib/` か `hooks/` に置き named export。
- ページ/レイアウト: `app/**/page.tsx`・`layout.tsx` のみ **default export**（Next 規約の例外）。それ以外は named export で統一する。
- 冒頭コメントに `screens.md` の対応箇所・関連 ADR を日本語で記す（既存の流儀）。

## チェックリスト

- [ ] page=取得/状態/合成、feature=props＋mutation所有、ui=純プリミティブ の責務に収まっている
- [ ] GET 取得は `useApi` 経由（生 `useEffect` 手書き取得を増やしていない）。deps はプリミティブ
- [ ] feature コンポーネントが内部 GET していない（Advisor 常駐チャットのみ例外・理由をコメント）
- [ ] 三状態表示は `StatusBlock` を使った（手書き三分岐を増やしていない）
- [ ] Card / テーブル / 入力欄を local 再定義せず `components/ui/` のプリミティブを使った
- [ ] `fmtJpy`/`pct` 等は `lib/format.ts` から import（再定義していない）
- [ ] `"use client"` は必要な葉に最小限。静的シェルは Server のまま
- [ ] 色・寸法は DESIGN.md トークン。生色・任意値マジックを散らしていない。`@apply` で共通クラスを量産していない
- [ ] 命名: コンポーネント=PascalCase named export＋ファイル名一致、Props=`type`、page/layout のみ default
- [ ] データ取得・型は `lib/api.ts` 経由（[[frontend-api-client-pattern]]）。DB に触れていない
