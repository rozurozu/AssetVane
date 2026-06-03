# REST API 契約（Next.js ↔ FastAPI）

「Next＝見せる係 / FastAPI＝データ・計算係」の**境界＝この API 契約**（[decisions.md ADR-005](decisions.md)）。Next.js は DB を触らず、すべてここを経由する。

> **契約の正本は FastAPI が自動生成する OpenAPI（`/docs`・`/openapi.json`）とする。** 本ドキュメントは主要エンドポイントの一覧と方針を示すもので、列の細部は実装時に OpenAPI 側で確定する。パスは設計案。

---

## 0. 共通方針

- **ベース URL**: `http://<host>:8000`（FastAPI）。Next.js からは環境変数 `NEXT_PUBLIC_API_BASE_URL` で指定（[architecture.md 7](architecture.md)）。
- **形式**: JSON。日付は `YYYY-MM-DD`。
- **認証**: 単一ユーザーのため認証なし（[ADR-001](decisions.md)）。**家庭内 LAN 限定で公開しない**前提。外部公開する場合は別途要設計。
- **エラー**: FastAPI 標準の `{"detail": ...}` ＋適切な HTTP ステータス。
- **評価額の注意**: Free プランは株価が 12 週間遅延。評価額・P/L 系は遅延値である旨をレスポンスまたは UI で明示する（[data-model.md](data-model.md)）。

---

## 1. 株価・銘柄（Phase 0〜）

| メソッド | パス | 用途 |
|---|---|---|
| GET | `/stocks` | 銘柄一覧（検索・絞り込み）|
| GET | `/stocks/{code}` | 銘柄詳細 |
| GET | `/quotes/{code}?from=&to=` | 日足（チャート用）|

## 2. シグナル・スクリーニング（Phase 1〜）

| メソッド | パス | 用途 |
|---|---|---|
| GET | `/signals?date=&type=` | その日の signals（momentum / volume_spike / ai_alpha / lead_lag）|

## 3. ポートフォリオ・資産（Phase 2〜）

| メソッド | パス | 用途 |
|---|---|---|
| GET/POST/PUT/DELETE | `/portfolios`, `/portfolios/{id}` | ポートフォリオ CRUD（当面は単一固定）|
| GET/POST/PUT/DELETE | `/holdings` | 保有 CRUD |
| POST | `/transactions` | 取引（買い/売り）記録 → holdings 再計算 |
| GET/PUT | `/cash` | 現金残高 |
| GET/POST/PUT/DELETE | `/external-assets` | 投信等（割合文脈）|
| GET | `/portfolio/{id}/metrics` | 相関・シャープ・最大ドローダウン |
| POST | `/portfolio/{id}/optimize` | policy 制約下の最適比率 |
| GET | `/asset-overview` | 保有・現金・割合・資産推移（遅延注記付き）|

## 4. AI Advisor（Phase 3〜）

| メソッド | パス | 用途 |
|---|---|---|
| GET/PUT | `/policy` | 現在の投資方針の取得・更新 |
| GET | `/journal?from=&to=` | 投資日記の取得 |
| GET | `/proposals?status=` | AI 提案の取得（pending/approved/rejected）|
| POST | `/proposals/{id}/approve` / `/reject` | 提案の承認・却下 |
| POST | `/chat` | 相談チャット（軸2）。下記の **画面コンテキスト** と **Tool 実行可視化** を持つ（軽量ヒント・**数値は含めない**＝[ADR-025](decisions.md)・[advisor.md §6.1](advisor.md)・[screens.md §5](screens.md)）。ストリーミング対応は実装時に決定 |

**`/chat` の context / tool_runs（`_arbitration.md` 決定3）**

- **body の画面コンテキスト**: `context: { page, focus?: { type, code?, id? } }`。
  - `focus.type` は `"stock" | "portfolio" | "signal" | "proposal"`。`stock`/`signal` は `code`、`portfolio`/`proposal` は `code` を持たないため `id`（数値）を使う（両対応）。対象が無いページ（Dashboard 等）は `focus` 省略。
  - 数値・画面データは載せない。AI は事実が要れば該当 Tool で取り直す（[ADR-025](decisions.md)）。
- **レスポンスの Tool 実行可視化**: `tool_runs: [{ name, args? }]`。UI で「⚙ get_signals 実行」のように呼んだ Tool（と引数）を出す（[screens.md §4](screens.md)）。**Tool 結果の数値はレスポンスに載せない**（[ADR-025](decisions.md)）。`tool_calls_made: string[]` は廃止。

## 5. 銘柄ドシエ・watchlist（Phase 4〜）

| メソッド | パス | 用途 |
|---|---|---|
| GET/POST/DELETE | `/watchlist` | 監視銘柄の管理（一覧は最終調査日つき）|
| GET | `/dossiers/{code}` | 銘柄の調査レポート（markdown）と要約ソース一覧 |
| POST | `/dossiers/{code}/investigate` | その銘柄を調査（`investigate_stock` を起動。チャットの「この銘柄調査して」と共用）|

## 6. システム

| メソッド | パス | 用途 |
|---|---|---|
| GET | `/health` | 死活監視・必須環境変数の充足チェック |
| POST | `/batch/run` | 夜間バッチの手動起動（cron と共用）|

---

## 7. 未確定（実装時に OpenAPI で確定）

- 各エンドポイントのリクエスト/レスポンスの詳細スキーマ。
- `/chat` のストリーミング（SSE）有無とメッセージ形式。`context`（画面コンテキスト）の正本形は §4 に確定済み（実キーの細部は OpenAPI で確定）。

### `GET /policy` のレスポンス（構造化コア／rationale 分離・確定）

画面で見せ方が違うため（構造化コアはチップ/グリッド・`rationale` は引用調＝[screens.md §3](screens.md)）、**構造化コア（`core`）と自由文（`rationale`）を分けて返す**（`_arbitration.md`・[DOC-7]）。

```jsonc
GET /policy ->
  {
    core: {
      risk_tolerance, time_horizon, target_cash_ratio, max_position_weight,
      sector_caps, target_return, no_leverage, exclusions
    },
    rationale,   // 自由文の理念・機微（引用調で表示）
    updated_at
  }
  // 比率系（target_cash_ratio / max_position_weight 等）は 0..1。UI でのみ %。
```

`PUT /policy` も `core` と `rationale` を分けて受ける（構造化コアの更新は承認制、`rationale` は即時＝[ADR-013](decisions.md)・`_arbitration.md` U-7）。

### ページネーション

- `/quotes`・`/journal` の**ページネーションは当面なし**。期間は `from`/`to` の範囲指定で代替する（[DOC-6]）。データ量が問題になった段階で導入を検討する。
