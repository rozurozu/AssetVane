---
name: frontend-component-pattern
description: frontend（Next.js App Router / React 19 / TS / Tailwind v4）のページ・コンポーネント・フック・共有 UI を新規作成または修正するとき読む。粒度（page=取得/状態/合成、feature=描画＋mutation所有、ui=純プリミティブ）・fetch 規約（useApi）・状態表示（StatusBlock）・整形（lib/format.ts）・DESIGN.md トークンを規定する。
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

## data 取得フック `useApi`（`lib/use-api.ts`）

取得の `useState`＋`useEffect`＋`then/catch`＋三分岐を毎ページ手書きしない。共有フック **`src/lib/use-api.ts`** に一本化する（`"use client"`・read-only 専用）。シグネチャと要点だけ抜粋（全文は当該ファイル）:

```tsx
// src/lib/use-api.ts
"use client";
export type ApiState<T> = { data: T | null; error: string | null; loading: boolean };
//                                          ^^^^^^^^^^^^^^^^^ error は文字列（ApiError.message を入れる）

export function useApi<T>(
  fetcher: (signal: AbortSignal) => Promise<T>, // lib/api.ts の関数を signal 付きで呼ぶ
  deps: unknown[],                              // プリミティブを渡す。fetcher は入れない
): ApiState<T> {
  // useEffect 内: ignore フラグ＋AbortController で
  //   ・StrictMode の dev 二重実行
  //   ・deps 変化時の race（古い応答の反映）
  // を吸収する。catch は (ignore || signal.aborted) なら握り、それ以外を error 文字列に。
}
```

規約:
- **error は `string | null`**（`Error` ではない）。catch で `e instanceof Error ? e.message : String(e)` に正規化済み。StatusBlock もこの string を受ける。
- **`ignore` フラグ（state 反映の抑止）と `AbortController.abort()`（要求のキャンセル）は両方入れる**。役割が別で、二重実行と race の両方を吸収する。
- **deps にはプリミティブを渡す**（`useApi((s) => getQuotes(code, s), [code])`）。`fetcher` を deps に入れない（毎レンダー再実行になる）。

### useApi の現実的な境界（read-only 専用フック）

`useApi` は **GET（読み取り）専用**。当てはめ方をパターンで持つ:

- **(a) 単一/依存リソース**: `const { data, error, loading } = useApi((s) => getX(id, s), [id]);`。`id` 変化で自動再取得。
- **(b) 複数リソース**: 独立なら **複数 `useApi` を並べる**。まとめたいときは **1 つの `useApi` 内で `Promise.all`**（`useApi((s) => Promise.all([getA(s), getB(s)]), [])`）。
- **(c) 外部から mutation されるデータは useApi 化しない**。取引登録後に再計算される holdings、`onSaved` で差し替わる policy、承認で状態が変わる proposals のように **画面操作で書き換わる data は `useState` のまま**持ち、mutation 成功時に setState（または再 fetch）で更新する。`useApi` は deps 駆動の再取得しか持たないため、こうした「操作起点の更新」には合わない。初回ロードだけ `useApi` で取り、以降 useState に移す折衷も可。

## 状態表示 `StatusBlock`（`components/ui/StatusBlock.tsx`）

「読み込み中… / ⚠ 取得に失敗: … / 空」の三状態を共通コンポーネントにする。シグネチャ抜粋:

```tsx
// src/components/ui/StatusBlock.tsx
type Props = {
  loading: boolean;
  error: string | null;        // useApi の error 文字列をそのまま渡す
  empty?: boolean;             // 取得済みだが 0 件か
  className?: string;          // 各状態メッセージのラッパに付与（"p-4"・カード内余白など）
  errorHint?: ReactNode;       // エラー時の補足（"backend が起動しているか確認" 等）
  emptyText?: ReactNode;       // 空表示の文言（既定 "データがないのだ。"）
  loadingText?: ReactNode;     // 読込中の文言（既定 "読み込み中…"）
  children: ReactNode;         // 正常時の描画
};
// 描画順: error → loading → empty → children。
// error は「⚠ 取得に失敗: {error}」を text-down、errorHint は text-ink-subtle。
// loading/empty は text-ink-subtle。
```

`useApi` と組み合わせ、ページ側は `<StatusBlock loading={loading} error={error} empty={data?.items.length === 0}>…</StatusBlock>` の 1 行で三分岐を畳む。

**当てはまらないときの逃げ道**: 枠や文言がページ独自（カード枠で囲む・"⚠ ○○の取得に失敗" のように対象名を入れたい）なら、まず `className`/`errorHint`/`emptyText`/`loadingText` で寄せる。それでも嵌らなければ **手書きの三分岐を許容**する（StatusBlock を無理に通さない）。共通形に収まるものだけ StatusBlock に乗せる。

## 共有 UI プリミティブ `components/ui/`

繰り返し現れる構造はコンポーネントに抽出する（Tailwind 公式も「繰り返しは `@apply` でなくコンポーネント化」を推奨）。純表示プリミティブなので **Server Component のまま**置ける（DB に触れない・ADR-005 と無矛盾）。配置は `src/components/ui/`。現存するものとシグネチャ:

### `Card`（`components/ui/Card.tsx`）

```tsx
type Props = { title: ReactNode; meta?: string; link?: ReactNode; children: ReactNode };
// <section.rounded-lg.border.border-hairline.bg-surface-1>
//   ヘッダ: 罫線下 + px-3 py-2、title(font-semibold text-[14px])・meta(右の鮮度注記)・link("すべて" 等)
//   本文: p-3
```

### `DataTable` ＋ `Td`（`components/ui/DataTable.tsx`）

`thead` のマッピング（`h-8`・罫線・uppercase ヘッダ）と行セルの定型を一本化する。行（`<tr>`）と `Td` は呼び出し側が組む。

```tsx
export type Column = { label: string; right?: boolean }; // right=true で右寄せ（数値列）
function DataTable({ columns, children }: { columns: Column[]; children: ReactNode })
//   thead を columns から生成し、children を <tbody> に流す
function Td({ children, right, className = "" }: { children: ReactNode; right?: boolean; className?: string })
//   h-[34px] border-hairline-soft の定型セル。数値色・font-semibold 等は className で足す
```

使用例: `<DataTable columns={[{label:"銘柄"},{label:"評価額",right:true}]}><tr><Td>…</Td><Td right className="num">…</Td></tr></DataTable>`。右寄せ数値には `num` クラスを併用する。

### フォーム入力は `inputCls`/`labelCls` ＋任意の `Field`（`components/ui/Field.tsx`）

**フォーム入力は「`Input`/`Label` 汎用コンポーネント」を作らず、`inputCls`/`labelCls` のクラストークンに一本化する。** 理由: 入力は input・select・textarea・checkbox が混在し、これらを 1 つの汎用コンポーネントで包むと props が肥大して表現力が落ちる。素の要素に className を付ける方が素直。

```tsx
export const inputCls = "w-full rounded-md border border-hairline bg-canvas px-2.5 py-1.5 text-[13px] text-ink outline-none focus:border-accent";
export const labelCls = "block text-[11px] text-ink-muted mb-0.5";

// label＋入力の縦並びラッパ（任意利用。htmlFor で id を結ぶ）
function Field({ htmlFor, label, children }: { htmlFor?: string; label: ReactNode; children: ReactNode })
```

使い方: `<Field htmlFor="qty" label="数量"><input id="qty" className={inputCls} /></Field>`。`<select className={inputCls}>` / `<textarea className={inputCls}>` も同じトークンで揃える。checkbox など縦並びに合わないものは `Field` を使わず `labelCls`/個別 className で直書きしてよい。各フォームで同じ className 文字列をコピペしない。

ルール:
- **クラス共有は React コンポーネント抽出（または公開クラス定数）が第一選択**。`@apply` での共通クラス量産はしない（Tailwind v4 では非推奨寄り・テーマ変数解決と性能の問題）。
- **色・寸法は DESIGN.md の `@theme` トークン**を使う（`surface-1`/`hairline`/`hairline-soft`/`accent`/`num`/`ink-*`/`down` 等）。生色（`bg-[#316ff6]`）や任意値 `[...]` のマジック値を散らさない。動的値が要る時だけ `style={{ ... }}` か `bg-(--color-xxx)` で CSS 変数を注入する。
- variant が増えるプリミティブ（Button 等）は「ベースクラス定数＋variant 分岐」を 1 ファイルに閉じ込め、呼び出し側に長い className 列を出さない。

## 整形ヘルパ `lib/format.ts`（`src/lib/format.ts`）

表示整形を集約する（計算はしない・表示整形のみ）。**各ページ・各コンポーネントで同名関数を再定義しない**。比率・weight は内部 0..1・UI でのみ ×100（ADR-008）という単位約束も、この層で一貫させる。現存する関数:

```tsx
// src/lib/format.ts
fmtJpy(v: number | null | undefined): string   // 円整形。null は "—"。1234567 → "¥1,234,567"
pct(v, digits = 1): string                      // 0..1 → "X%"。null は "—"。digits は小数桁（整数表示は 0）
deltaPct(v: number, digits = 1): string         // 0..1 → 符号付き "+X%"/"-X%"（delta 表示用）
toPctStr(v): string                             // 0..1 → 編集用の数値文字列（"%" なし）。null は ""。0.255 → "25.5"
fromPctStr(s: string): number | null            // "%" 値の文字列 → 0..1。空・非数は null（PolicyEditor の入力変換）
```

## 命名規則

- コンポーネント: **PascalCase**、`export function ComponentName(...)`、**ファイル名＝コンポーネント名**（`TransactionForm.tsx`）。`React.FC` は使わず、`children` は Props に `children: React.ReactNode` を明示。
- Props 型: **`type` エイリアス**（`interface` ではなく `type Props = {...}`）。コンポーネント直前に定義。プロジェクト全体で `type` に統一する。
- フック: `useXxx`（camelCase）。`lib/` か `hooks/` に置き named export。
- ページ/レイアウト: `app/**/page.tsx`・`layout.tsx` のみ **default export**（Next 規約の例外）。それ以外は named export で統一する。
- 冒頭コメントに `screens.md` の対応箇所・関連 ADR を日本語で記す（既存の流儀）。

## チェックリスト

- [ ] page=取得/状態/合成、feature=props＋mutation所有、ui=純プリミティブ の責務に収まっている
- [ ] GET 取得は `useApi` 経由（生 `useEffect` 手書き取得を増やしていない）。deps はプリミティブ。外部 mutation で書き換わる data は `useState` のまま（useApi 化していない）
- [ ] feature コンポーネントが内部 GET していない（Advisor 常駐チャットのみ例外・理由をコメント）
- [ ] 三状態表示は `StatusBlock` を使った（手書き三分岐を増やしていない）
- [ ] Card / DataTable+Td を local 再定義せず `components/ui/` のプリミティブを使った
- [ ] フォーム入力は `inputCls`/`labelCls`（＋任意 `Field`）で揃えた（className 文字列をコピペしていない・Input/Label 汎用化していない）
- [ ] `fmtJpy`/`pct` 等は `lib/format.ts` から import（再定義していない）
- [ ] `"use client"` は必要な葉に最小限。静的シェルは Server のまま
- [ ] 色・寸法は DESIGN.md トークン。生色・任意値マジックを散らしていない。`@apply` で共通クラスを量産していない
- [ ] 命名: コンポーネント=PascalCase named export＋ファイル名一致、Props=`type`、page/layout のみ default
- [ ] データ取得・型は `lib/api.ts` 経由（[[frontend-api-client-pattern]]）。DB に触れていない
