# ドリフト監査レポート（スキル基準）

作成: 2026-06-03 / 基準: `.skills/` の 10 スキル / 方針: 修正はせず一覧化（リファクタは小バッチ承認制で別フェーズ）

> **更新 2026-06-04（返済済み）**: 本レポートが挙げた frontend 負債は**ほぼ全返済**。共有レイヤ（`lib/format.ts`・`lib/use-api.ts`・`components/ui/Card`・`DataTable`・`Field`・`StatusBlock`）は抽出済みで、各ページも移行済み。残っていた局所ドリフト（transactions の `inputCls` 重複・外部資産の手書きテーブル → `DataTable`/`Td`、journal の `pct` 同名再定義 → `pctChip` にリネーム）も解消した（`biome check`／`tsc --noEmit` 緑）。
> **更新 2026-06-05（B-1 返済済み）**: 唯一残っていたバックエンドの B-1（`DELETE /external-assets/{asset_id}` の `response_model` 無し）も解消済み。現状 `backend/app/routers/assets.py:169` は `response_model=OkOut` を付与済みで、これで**バックエンドのドリフトはゼロ**になった。
> F-5 の「手書き fetch」は dashboard（多リソース・失敗握り＝spec §9.6）・mutation 起点ページ（rule (c) で `useState` が正）・Topbar（health チェック）が**正当な例外**として残るのみでドリフトではない。
> 以降の F-1〜F-5 / 推奨バッチA〜F の記述は**着手前のスナップショット**として保存（履歴）。

ベースラインは全緑（backend: `ruff check` clean・`pytest` 全通過 / frontend: `biome check` clean・`tsc --noEmit` 0 エラー）。
`next build` はグローバル hook で禁止のため frontend 検証は `biome` ＋ `tsc` で行う。

---

## サマリ

- **backend**: スキルと整合。**ドリフトなし**（旧 B-1 は 2026-06-05 返済済み）。二階の書き込み規約（W1/W2）は `backend-repo-pattern` で「意図的」と明文化済みなのでドリフト扱いしない。
- **frontend**: 共有レイヤ（`frontend/src/components/ui/`・`frontend/src/lib/format.ts`・`useApi`・`StatusBlock`）が未抽出で、同じ構造のコピペが各所に散在。`frontend-component-pattern` が新たに規約化した対象。挙動バグではなく保守性・一貫性の負債（深刻度 Medium）。

---

## frontend ドリフト（`frontend-component-pattern` 基準・すべて Medium）

現状、`frontend/src/components/ui/` ・ `frontend/src/lib/format.ts` ・ `useApi` ・ `StatusBlock` はいずれも**存在しない**。

| ID | 内容 | 該当箇所 | 対応スキル |
|---|---|---|---|
| F-1 | `Card` を各ページで local 定義（コメントに「同形」と自認） | `frontend/src/app/page.tsx:550`・`frontend/src/app/portfolio/page.tsx:41`・`frontend/src/app/transactions/page.tsx:27` | `frontend/src/components/ui/Card` へ抽出 |
| F-2 | 整形関数の重複定義（`fmtJpy`/`pct`/`toPctStr`/`fromPctStr`/`pctOrDash`） | `frontend/src/app/page.tsx:26,66,67`・`frontend/src/app/journal/page.tsx:18`・`frontend/src/app/portfolio/page.tsx:30,35`・`frontend/src/components/portfolio/OptimizeTable.tsx:12`・`frontend/src/components/policy/PolicyEditor.tsx:19,24` | `frontend/src/lib/format.ts` へ集約 |
| F-3 | フォーム入力クラス `inputCls`/`labelCls` の local 重複 | `frontend/src/app/transactions/page.tsx`・`frontend/src/components/portfolio/TransactionForm.tsx`・`frontend/src/components/policy/PolicyEditor.tsx` | `frontend/src/components/ui/Field`・`Input`・`Label` へ |
| F-4 | インラインのテーブル markup（`<thead>`＋セル定型）を各所で手書き | `frontend/src/app/page.tsx`・`frontend/src/app/stocks/page.tsx`・`frontend/src/app/signals/page.tsx`・`frontend/src/app/transactions/page.tsx`・`frontend/src/app/portfolio/page.tsx`・`frontend/src/components/portfolio/OptimizeTable.tsx` | `frontend/src/components/ui/DataTable` へ |
| F-5 | 取得の手書き三分岐（`useState`＋`useEffect`＋`then/catch`＋loading/error/empty） | 取得を行う全 9 ページ（`frontend/src/app/journal`・`frontend/src/app/signals`・`frontend/src/app/page.tsx`・`frontend/src/app/stocks`・`frontend/src/app/stocks/[code]`・`frontend/src/app/transactions`・`frontend/src/app/policy`・`frontend/src/app/portfolio`・`frontend/src/app/proposals`） | `useApi` ＋ `StatusBlock` へ |

補足:
- F-5 の移行時、`useApi` の `fetcher` は `frontend/src/lib/api.ts` の関数を呼び、`deps` にはプリミティブ（`code` 等）を渡す（フックを deps に入れない）。各 GET 関数に `signal?: AbortSignal` を足す必要がある（[[frontend-api-client-pattern]]）。
- 常駐の `AdvisorChat` は規約上の例外（自前で状態・取得を持つ）。F-5 の対象外。

## backend ドリフト

| ID | 内容 | 該当箇所 | 深刻度 | 状態 |
|---|---|---|---|---|
| B-1 | `DELETE /external-assets/{asset_id}` に `response_model` が無い（他 24 ルートは付与済み） | `backend/app/routers/assets.py:169` | Low | ✅ 返済済み（2026-06-05・`response_model=OkOut` 付与） |

その他は整合（`from __future__ import annotations` 全モジュール完備 / routers に pandas・numpy・数値計算なし / `async def` は LLM を await する `POST /chat` のみ）。**現状バックエンドの未対応ドリフトはゼロ。**

---

## 推奨リファクタ順（小バッチ・各バッチで `biome check` ＋ `tsc --noEmit` 緑を確認）

リスク小・機械的なものから。1 バッチ = 1 関心、レビュー可能な粒度に保つ。

1. **Batch A — `frontend/src/lib/format.ts` 抽出**（F-2）。最も機械的・低リスク。6 ファイルの重複を置換。
2. **Batch B — `frontend/src/components/ui/Card`**（F-1）。3 ファイルの local Card を置換。
3. **Batch C — `frontend/src/components/ui/Field`/`Input`/`Label`**（F-3）。3 フォームの入力クラスを置換。
4. **Batch D — `useApi` ＋ `StatusBlock` 導入**（F-5）。フック・コンポーネントを追加し、ページを 1 枚ずつ移行（同時に `frontend/src/lib/api.ts` の GET へ `signal` 追加）。
5. **Batch E — `frontend/src/components/ui/DataTable`**（F-4）。churn 最大。テーブルを 1 つずつ移行、最後に。
6. **Batch F（任意）— B-1**。`response_model` 付与 or 204 化。

各バッチは「挙動を変えない純粋な抽出」を原則とし、差分前後で画面表示が変わらないことを確認する。
