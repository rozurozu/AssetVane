# app レーン仕様 — REST API 契約・Next.js 画面・lib/api.ts 型（全 Phase）

> 担当: app（REST API 契約・フロント画面）。**着工可能な仕様**（コードは書かない）。
> 設計の真実は `docs/`。本書は `docs/api.md`・`docs/screens.md` を Phase ごとに**確定スキーマ**へ落とす。
> 参照: [ADR-005](../../decisions.md)（Next は DB 非接触・REST のみ・Prisma 不採用）・[ADR-024](../../decisions.md)（チャット常駐）・[ADR-025](../../decisions.md)（画面コンテキスト軽量ヒント）・[ADR-019](../../decisions.md)（保有は transactions 導出）・[ADR-008](../../decisions.md)（J-Quants V2・Free 12週遅延）。
> 接地: `docs/phase-specs/_drafts/_current-state.md`（**Dashboard は完全モック**・型契約の細部）。

---

## 0. 横断方針（全 Phase 共通）

### 0.1 命名・型・形式
- **JSON / 日付は `YYYY-MM-DD` 文字列**。金額・株数・指標は `number`（JPY 前提・通貨列なしは Phase 7 まで＝[data-model.md §1](../../data-model.md)）。
- **backend は素の `dict` を repo が返し、ルータ側で Pydantic 変換**（_current-state.md §3）。本書の「Pydantic モデル名（案）」はルータ層の入出力モデル名。
- **frontend の型は `lib/api.ts` に集約**し backend Pydantic と 1:1 対応（既存 `Stock`/`Quote` の流儀）。新規型も同ファイルに足す。
- **fetch はブラウザ（`"use client"`）**で `lib/api.ts` 経由。`API_BASE = NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"`。
- **エラーは FastAPI 標準 `{"detail": ...}`**。`lib/api.ts` の `getJSON`/`postJSON` が `detail` を拾って `throw new Error(detail)`（既存 `getJSON` と同形）。
- **null の扱い**: 取得できていない値は `null`（既存 `Quote` が `number | null` の流儀）。画面は `?? "—"` で描く。

### 0.2 既存契約（Phase 0・確定済み・変更しない）
`_current-state.md §4`・`frontend/src/lib/api.ts` の通り。**型の細部は崩さない**:
- `GET /stocks?q=` → `Stock[]`。`Stock` = `{code, company_name?, sector33_code?, sector17_code?, market_code?, is_etf?}`（`updated_at` は**返さない**）。
- `GET /stocks/{code}` → `Stock`（無ければ 404）。
- `GET /quotes/{code}?from=&to=` → `Quote[]`（date 昇順）。`Quote` = `{date, open?, high?, low?, close?, volume?, adj_close?}`（`code` は**返さない**・`volume` は **number**）。
- `POST /chat` body `{messages:[{role:"user"|"assistant", content}]}` → `{reply}`（**現状**。P3 で context・tool_runs を足す＝§P3-5）。

### 0.3 評価額の遅延注記（ADR-008・横断）
Free プランは株価 12 週間遅延。**評価額・P/L・現金比率を含むレスポンスには `is_delayed`（bool）と `as_of`（基準営業日）を必ず載せる**（_arbitration 決定2 の横断約束。`delayed` は廃止）。UI は Topbar 既存バッジ（`Free・株価12週遅延`）に加え、各カードに `meta` で「12週遅延・<as_of>基準」を出す（Dashboard モックの `meta="12週遅延・約3か月前基準"` を実値化）。
- **載せ方**: `asset-overview`・`metrics`・`optimize` は `is_delayed`/`as_of` を**フラットに**持つ（正本 Tool スキーマと同形）。`holdings` のみ `valuation_meta: { as_of, is_delayed, plan }` ラッパに包む（保有行配列と並ぶため・§P2-2）。`plan`（"free"/"light"）は遅延文脈の説明用。

### 0.4 ページネーション
- `/quotes`・`/journal` 等の長尺は当面**全件返し**（単一ユーザー・LAN・データ量限定）。`from`/`to` の範囲指定で実用上絞れる（[api.md §7](../../api.md) の宿題は「当面なし」で確定提案）。
→ **[OPEN-B]** signals 一覧の件数上限。推奨: `GET /signals` に `limit`（既定 100）を任意クエリで持たせる。← quant レーンの signals 件数想定と要一致。

### 0.5 着工順（Phase 横断の前提）
各 Phase 内の着工順は節末に記す。**Phase は roadmap 通り（P0 完了 → P1 → …）**。frontend は「①lib/api.ts に型・関数追加 → ②画面コンポーネント → ③Sidebar nav を href 化（mock-data.ts の `phase` を `href` に差し替え・非活性解除）」の順。

---

## Phase 1 — Signals（Trend Vane）

`docs/screens.md` #4・§3「今日のシグナル」。シグナルは**夜間バッチが `signals` テーブルへ事前計算**（data-model.md §4）、API は読むだけ。

### P1-1. `GET /signals`
- **method + path**: `GET /signals?date=&type=&limit=`
- **query**:
  - `date`（任意・`YYYY-MM-DD`）: 省略時は**最新算出日**（backend が `MAX(date)` を返す）。
  - `type`（任意）: `momentum` | `volume_spike` | `ai_alpha`（P5）| `lead_lag`（P7）。省略時は全 type。
  - `limit`（任意・既定 100）: スコア降順の上限（[OPEN-B]）。
- **response（TS interface）**:
```ts
// 正本 = get_signals（_arbitration 決定2）: {date, is_delayed, signals:[{code,company_name,signal_type,score,payload}]}。
// REST はこれに合わせる。date は**トップのみ**（行レベル date は持たない・B-6）。
export interface SignalsResponse {
  date: string;                 // 実際に返した算出日（最新解決後の値）
  is_delayed: boolean;          // 遅延フラグ（横断・正本）
  signals: Signal[];            // score 降順
}
export interface Signal {
  code: string;                 // 銘柄/業種コード
  company_name: string | null;  // signals JOIN stocks で補完（ルータ・B-6 確定）
  signal_type: "momentum" | "volume_spike" | "ai_alpha" | "lead_lag";
  score: number;
  payload: SignalPayload;       // 指標値・根拠（型は signal_type 依存・下記）
}
// payload(JSON) は signal_type ごとに形が違う。label/change_5d は **quant が payload に格納**（B-6 確定）。
// app は payload から読むだけ。type 固有の指標キーは quant が確定。
export interface SignalPayload {
  label?: string;               // 一覧の「シグナル」列に出す短文（例 "25MA上抜け" "RSI反転"）。quant が格納
  change_5d?: number | null;    // 5日騰落率（Dashboard モック d5 相当・0..1）。quant が格納
  [k: string]: unknown;         // momentum: ma25_cross/rsi 等、volume_spike: vol_ratio 等（quant 確定）
}
```
- **対応 backend Pydantic（案）**: `SignalOut`（`Signal`）・`SignalsResponse`。`payload` は `dict[str, Any]`（DB は TEXT/JSON）。`company_name` はルータで JOIN 補完。
- **lib/api.ts に足す型と関数**:
```ts
export type SignalType = "momentum" | "volume_spike" | "ai_alpha" | "lead_lag";
export type Signal = { /* 上記 */ };
export type SignalsResponse = { date: string; is_delayed: boolean; signals: Signal[] };
export function getSignals(opts?: { date?: string; type?: SignalType; limit?: number }): Promise<SignalsResponse> {
  const p = new URLSearchParams();
  if (opts?.date) p.set("date", opts.date);
  if (opts?.type) p.set("type", opts.type);
  if (opts?.limit != null) p.set("limit", String(opts.limit));
  const qs = p.toString();
  return getJSON<SignalsResponse>(`/signals${qs ? `?${qs}` : ""}`);
}
```

### P1-2. 画面コンポーネント
- **`frontend/src/app/signals/page.tsx`**（新規・`"use client"`）: 「今日の強い銘柄」一覧。
  - props: なし（ページ）。内部 state: `data: SignalsResponse | null` / `error` / `type フィルタ`。
  - 構成: ヘッダー（タイトル「Signals（Trend Vane）」＋ `data.date` を「<date> 算出」と表示）→ **type 切替タブ**（全 / momentum / volume_spike）→ **テーブル**（`page.tsx` の Dashboard 既存 `Table`/`Td` と同じ列構成: コード/銘柄・スコア（バー＋数値）・5日・シグナル）。
  - 行クリックで `/stocks/{code}` へ（`Link`）。
  - DESIGN.md トークン: `surface-1`/`hairline`/`hairline-soft`・スコアバーは `bg-accent`・5日は `text-up`/`text-down`・シグナルバッジは `bg-surface-2 text-ink-muted`・数値は `num`（tnum）。
  - 空/エラー/読み込み中は Stocks 一覧と同じ 3 状態文言の流儀。
- **`frontend/src/components/signals/SignalsTable.tsx`**（任意・抽出）: Dashboard と Signals ページで共用するなら `page.tsx` の `Table`/`Td`/スコアバーをここへ抽出。**推奨**: まず Signals ページにインライン、Dashboard 実配線（P2 以降）時に共通化。
- **Dashboard 実配線**: `app/page.tsx` の `signals`（mock-data）を `getSignals({ limit: 5 })` に差し替え。`mock-data.ts` の `signals` を削除。

### P1-3. Sidebar nav
- `mock-data.ts` の `{ label: "Signals", icon: "📈", phase: "P1" }` を `{ label: "Signals", icon: "📈", href: "/signals" }` に変更（非活性 → 遷移可）。

### P1 新規/変更ファイル
- 新規: `app/signals/page.tsx`。
- 変更: `lib/api.ts`（Signal 型・getSignals）・`lib/mock-data.ts`（nav の Signals を href 化・`signals` mock 削除）・`app/page.tsx`（Dashboard の signals を実配線）。
- 着工順: lib/api.ts → signals/page.tsx → nav href 化 → Dashboard 配線。

### P1 突き合わせ
- **[確定済 B-6]** `payload` の `label`（短文）・`change_5d`（0..1）は **quant が payload に格納**、app は読むだけ。type 固有の指標キーは quant が確定。
- **[確定済 B-6]** `signals` テーブルに `company_name` は持たず**ルータで stocks を JOIN**（quant の signals DDL に名前を持たせない＝正本）。`date`/`is_delayed` はレスポンスのトップのみ（行レベル date なし）。

---

## Phase 2 — Portfolio / 資産

`docs/screens.md` #5・#6・§3。[ADR-019] **holdings は transactions から導出**（直接編集しない）。**当面ポートフォリオは単一固定**（[api.md §3]）。

### P2 共通: ポートフォリオ ID
単一前提でも API は `{id}` を取る形にしておく（将来複数化に耐える）。**フロントは既定ポートフォリオ id=1 を定数で持つ**（`DEFAULT_PORTFOLIO_ID = 1`）。
→ **[OPEN-C]** 「既定ポートフォリオの解決」を `GET /portfolios`（先頭を使う）にするか、`id=1` 固定にするか。推奨: `GET /portfolios` で配列を返し先頭を既定にする（後で複数化しても画面が壊れない）。← data-arch の portfolios 初期行投入と要一致。

### P2-1. `GET /portfolios`・`POST /portfolios`（当面 GET 中心）
- `GET /portfolios` → `Portfolio[]`。
- **response**:
```ts
export interface Portfolio { portfolio_id: number; name: string; created_at: string; }
```
- backend Pydantic（案）: `PortfolioOut`。
- lib/api.ts: `getPortfolios(): Promise<Portfolio[]>`。
- POST/PUT/DELETE は単一前提で**当面未実装**（[api.md §3] の CRUD は「当面は単一固定」）。

### P2-2. `GET /holdings`・`POST /transactions`
保有は導出値。**追加・変更は `POST /transactions`（→holdings 再計算）経由**（ADR-019）。

- `GET /holdings?portfolio_id=` → `HoldingsResponse`:
```ts
export interface Holding {
  id: number;
  code: string;
  company_name: string | null;   // stocks JOIN
  shares: number;                 // 取引から導出
  avg_cost: number;               // 平均取得単価（取引から導出）
  last_close: number | null;      // daily_quotes の MAX(date) の close（評価用）
  market_value: number | null;    // shares * last_close（遅延値）
  unrealized_pnl: number | null;  // market_value - shares*avg_cost
  weight: number | null;          // 株式内の比率（0..1。UI で ×100 して %。横断ルール＝_arbitration 決定2）
}
export interface HoldingsResponse {
  portfolio_id: number;
  holdings: Holding[];
  valuation_meta: ValuationMeta;  // §0.3
}
export interface ValuationMeta { as_of: string | null; is_delayed: boolean; plan: string; }
```
- `POST /transactions` body → 記録 → holdings 再計算。**レスポンスは更新後の holdings を返す**（画面が即反映できる）:
```ts
export interface TransactionInput {
  portfolio_id: number;
  code: string;
  side: "buy" | "sell";
  shares: number;
  price: number;          // 約定単価
  fee?: number | null;    // 任意
  traded_at: string;      // 約定日 YYYY-MM-DD
}
// response: HoldingsResponse（再計算後）。新規取引 id を返したいなら { transaction_id, holdings }。
export interface TransactionResult { transaction_id: number; holdings: HoldingsResponse; }
```
- backend Pydantic（案）: `HoldingOut` / `HoldingsResponse` / `ValuationMeta` / `TransactionIn` / `TransactionResult`。
- lib/api.ts: `getHoldings(portfolioId)` / `postTransaction(input): Promise<TransactionResult>`（`postJSON` ヘルパを新設）。
- **再計算ロジックは backend**（FastAPI が事実を計算＝ADR-014/019）。フロントは送るだけ。

→ **[確定済 quant]** `weight` は**株式内比率・0..1**（_arbitration 決定2 で 0..1 確定）。`market_value`/`unrealized_pnl` の算出主体は backend（quant の導出関数）。

### P2-3. `GET/PUT /cash`
- `GET /cash` → `Cash`。`PUT /cash` body `{ balance }` → 更新後 `Cash`。
```ts
export interface Cash { balance: number; updated_at: string; }
export interface CashInput { balance: number; }
```
- backend Pydantic（案）: `CashOut` / `CashIn`。
- lib/api.ts: `getCash()` / `putCash(balance: number)`。

### P2-4. `GET/POST/PUT/DELETE /external-assets`
投信等（軽量・割合文脈・data-model.md §3）。
```ts
export interface ExternalAsset {
  id: number;
  name: string;
  category: string | null;          // 投信/コモディティ等
  value: number;                    // 評価額（手入力）
  proxy_symbol: string | null;
  monthly_contribution: number | null;
  as_of: string | null;
}
export interface ExternalAssetInput {  // POST/PUT 共通（id は path）
  name: string; category?: string | null; value: number;
  proxy_symbol?: string | null; monthly_contribution?: number | null; as_of?: string | null;
}
```
- `GET /external-assets` → `ExternalAsset[]` / `POST` → 作成行 / `PUT /external-assets/{id}` → 更新行 / `DELETE /external-assets/{id}` → `{ ok: true }`。
- backend Pydantic（案）: `ExternalAssetOut` / `ExternalAssetIn`。
- lib/api.ts: `getExternalAssets()` / `createExternalAsset(input)` / `updateExternalAsset(id, input)` / `deleteExternalAsset(id)`。

### P2-5. `GET /portfolio/{id}/metrics`
相関・シャープ・最大ドローダウン（[api.md §3]・screens.md #5）。**計算は quant（Python）**、本書は**入れ物の形だけ**確定。
正本 = `get_portfolio_metrics`（_arbitration 決定2）。REST 型はそれと同形。
```ts
export interface PortfolioMetrics {
  portfolio_id: number;
  as_of: string | null;
  is_delayed: boolean;
  annual_return: number | null;    // 年率リターン（0..1・quant）
  annual_volatility: number | null;// 年率ボラ（0..1・quant）
  sharpe: number | null;
  max_drawdown: number | null;     // 最大ドローダウン（0..1・quant 確定）
  lookback_days: number | null;    // 計算に使った遡及日数
  correlation: CorrelationMatrix;  // 相関ヒートマップ用（正本＝{codes,labels,matrix}）
  deviations: Deviation[];         // 逸脱（決定6・B-12: /asset-overview と同じ quant 単一関数が供給）
}
export interface CorrelationMatrix {
  codes: string[];                 // 行/列の順序（銘柄コード）
  labels: string[];                // 表示名（company_name）
  matrix: number[][];              // codes×codes の相関係数（-1..1）
}
// Deviation は §P2-7 と同一型（current/limit は 0..1）。
```
- backend Pydantic（案）: `PortfolioMetricsOut` / `CorrelationMatrixOut`（`Deviation` は §P2-7 と共用）。
- lib/api.ts: `getPortfolioMetrics(portfolioId)`。
→ **[確定済 quant]** correlation 形 `{codes,labels,matrix}`・遅延フラグ `is_delayed`・鮮度 `as_of`・`deviations` 形 `{kind,label,current,limit,breached}`（0..1）は _arbitration 決定2 で正本確定。`annual_return`/`annual_volatility`/`max_drawdown`/`sharpe`/`lookback_days` の算出方法・null 条件（保有 1 銘柄・履歴不足）は quant が確定。`deviations` は `/asset-overview` と**同じ quant 単一関数**が供給（出力先 2・計算 1）。

### P2-6. `POST /portfolio/{id}/optimize`
policy 制約下の最適比率（[api.md §3]・PyPortfolioOpt＝advisor.md §4）。
正本 = `optimize_portfolio`（_arbitration 決定2）。すべての比率は 0..1。
```ts
export interface OptimizeRequest {
  // 省略時は現在の policy 制約をそのまま使う（backend が policy を読む）。
  // 上書きしたい時のみ送る（任意・すべて 0..1）。
  target_cash_ratio?: number | null;
  max_position_weight?: number | null;
  sector_caps?: Record<string, number> | null;
}
export interface OptimizeResult {
  portfolio_id: number;
  as_of: string | null;
  is_delayed: boolean;
  objective: string;                 // 最適化目的（例 "max_sharpe"・quant 確定）
  cash_weight: number;               // 現金比率（0..1）
  weights: OptimizeWeight[];         // 最適比率（配列・正本フィールド名は weights）
  expected_annual_return: number | null;
  expected_annual_volatility: number | null;
  expected_sharpe: number | null;
  constraints_applied: {             // 実際に使った制約（policy 由来 or 上書き）。すべて 0..1
    target_cash_ratio: number | null;
    max_position_weight: number | null;
    sector_caps: Record<string, number> | null;
  };
  infeasible: boolean;               // 解なし（制約が厳しすぎ等）。true なら weights は空
}
export interface OptimizeWeight {
  code: string;
  company_name: string | null;       // app の UI 直結のため company_name を JOIN 付与
  current_weight: number | null;     // 現状比率（0..1。UI で ×100）
  target_weight: number;             // 最適比率（0..1）
  delta: number;                     // target - current（0..1・リバランス差分）
}
```
- backend Pydantic（案）: `OptimizeIn` / `OptimizeResultOut` / `OptimizeWeightOut`。
- lib/api.ts: `optimizePortfolio(portfolioId, body?: OptimizeRequest)`。
→ **[確定済 _arbitration]** `weights` は配列 `[{code,current_weight,target_weight,delta}]`・すべて **0..1**・`infeasible` フラグで解なしを表現（422 ではなく `infeasible:true` で返す＝画面が握りつぶさず提示できる）。`company_name` は app の UI 直結のためルータで JOIN 付与（正本の純 weights に上乗せ）。`objective`/`expected_annual_*` の値は quant。
→ **[要一致 ai-advisor]** policy 制約（`target_cash_ratio`/`max_position_weight`/`sector_caps`）のキー名と単位（0..1）は `policy` レスポンス（§P3-1）と一致（policy が最適化制約と二重活用＝ADR-013）。

### P2-7. `GET /asset-overview`
Dashboard 資産概要・配分・資産推移（screens.md §3・data-model.md `asset_snapshots`）。
正本 = `get_asset_overview`（_arbitration 決定2）。`allocation` は `[{name,value,weight}]`・`weight`/`deviations.current,limit` は 0..1。
```ts
export interface AssetOverview {
  as_of: string | null;
  is_delayed: boolean;
  plan: string;                      // "free" 等（遅延注記の文脈）
  total_value: number;
  stock_value: number;
  cash_value: number;
  external_value: number;
  pnl: number;                       // 評価損益
  allocation: AllocationSlice[];     // 配分ドーナツ用
  policy_targets: {                  // policy との対比（配分カードの目標線・0..1）
    target_cash_ratio: number | null;
    max_position_weight: number | null;
  };
  deviations: Deviation[];           // 逸脱（決定6・B-12: get_portfolio_metrics と同じ quant 単一関数が供給）
  trend: AssetSnapshotPoint[];       // 資産推移スパークライン（日次）
}
export interface AllocationSlice {
  name: "株式" | "現金" | "投信";
  value: number;                     // 評価額（JPY）
  weight: number;                    // 総資産内の比率（0..1。UI で ×100。正本フィールド名は weight）
}
export interface Deviation {
  kind: "max_position" | "cash_ratio" | "sector_cap";
  label: string;                     // "最大銘柄比率" 等
  current: number;                   // 0.182（0..1。UI で ×100 → 18.2%）
  limit: number;                     // 0.15（0..1。UI で ×100 → 15%）
  breached: boolean;                 // 警告色判定（current が limit を逸脱）
}
export interface AssetSnapshotPoint { date: string; total_value: number; }
```
- backend Pydantic（案）: `AssetOverviewOut` ほか（`Deviation` は §P2-5 metrics と共用型）。
- lib/api.ts: `getAssetOverview()`。
- **逸脱（deviations）は Python が計算**（screens.md §6 (b) の宿題。AI に計算させない＝ADR-014）。**`/asset-overview` の戻り値に含める**（決定6・B-12 確定。新 Tool を立てない）。計算は quant の**単一関数**で、`get_portfolio_metrics`（Tool）と**同値**を供給（出力先 2・計算 1）。
→ **[確定済 quant]** `deviations`（最大銘柄比率・現金比率・業種上限）・`allocation.weight` はすべて 0..1（_arbitration 決定2）。計算式は quant。
→ **[DOCS要修正]** screens.md §6 (b) の宿題「`get_asset_overview` に含めるか新 Tool か未定」を、本書の結論「`/asset-overview` の `deviations` に含める」で確定（lead が screens.md 反映）。

### P2-8. 画面コンポーネント
- **`frontend/src/app/portfolio/page.tsx`**（新規）: screens.md #5。保有テーブル（`getHoldings`）＋相関ヒートマップ（`getPortfolioMetrics`）＋最適比率（`optimizePortfolio` ボタン起動）＋資産推移（`getAssetOverview`）。
  - 相関ヒートマップ: **`frontend/src/components/portfolio/CorrelationHeatmap.tsx`**（新規）。props `{ data: CorrelationMatrix }`。SVG グリッド・セル色は `accent`↔`down` の二極グラデ（正相関=accent 寄り・負=down 寄り）・対角は除外。density-first（セル詰め・`num`）。
  - 最適比率: **`frontend/src/components/portfolio/OptimizeTable.tsx`**（新規）。props `{ result: OptimizeResult }`。`current → target` と `delta`（増=`text-up`/減=`text-down`）。
  - 評価額カードに **§0.3 の遅延注記**（`valuation_meta.as_of`）。
- **`frontend/src/app/transactions/page.tsx`**（新規）: screens.md #6。取引入力フォーム（`postTransaction`）＋現金入力（`putCash`）＋投信入力（external-assets CRUD）。
  - 取引フォーム: **`frontend/src/components/portfolio/TransactionForm.tsx`**（新規）。props `{ portfolioId, stocks: Stock[], onDone(holdings) }`。side（buy/sell トグル）・code（銘柄選択）・shares・price・traded_at・fee。送信後 `TransactionResult.holdings` で一覧更新。
  - DESIGN.md: フォーム入力は `bg-canvas border-hairline focus:border-accent`（AdvisorChat の input に倣う）・buy=`up`/sell=`down` のトグル色。
- **Dashboard 実配線**: `app/page.tsx` の `kpis`/`allocation`/逸脱表示を `getAssetOverview()` に差し替え。`mock-data.ts` の `kpis`/`allocation`/`trendPath` を削除。

### P2-9. Sidebar nav
- `Portfolio`（P2）を `href: "/portfolio"` 化。Transactions はサブ導線（Portfolio 内タブ or 別 nav 項目）。
→ **[OPEN-D]** Transactions を独立 nav にするか Portfolio 内タブにするか。推奨: Portfolio 内に「保有 / 入力」タブ（nav 項目を増やしすぎない＝screens.md §2）。

### P2 新規/変更ファイル
- 新規: `app/portfolio/page.tsx`・`app/transactions/page.tsx`・`components/portfolio/{CorrelationHeatmap,OptimizeTable,TransactionForm}.tsx`。
- 変更: `lib/api.ts`（Portfolio/Holding/Cash/ExternalAsset/PortfolioMetrics/Optimize/AssetOverview 型・関数・`postJSON`/`putJSON`/`del` ヘルパ）・`lib/mock-data.ts`（nav・mock 削除）・`app/page.tsx`（Dashboard 配線）。
- 着工順: lib/api.ts（型＋ヘルパ）→ asset-overview 配線で Dashboard 実化 → holdings/transactions → metrics/optimize → 画面。

---

## Phase 3 — AI Advisor（policy / journal / proposals / chat）

`docs/advisor.md`・`docs/screens.md` #7〜10・§3〜5。[ADR-024]（常駐）・[ADR-025]（画面コンテキスト）。

### P3-1. `GET /policy`・`PUT /policy`（構造化コア ＋ rationale 分離）
screens.md §3「rationale（自由文）と構造化コアを分けて表示」・[api.md §7]。
```ts
export interface Policy {
  core: PolicyCore;        // 構造化コア（チップ/グリッド表示・最適化制約と二重活用）
  rationale: string;       // 自由文の理念（引用調表示）
  updated_at: string;
}
export interface PolicyCore {
  risk_tolerance: "低" | "中" | "高";        // data-model: TEXT 低/中/高
  time_horizon: "短" | "中" | "長";          // 短/中/長
  target_cash_ratio: number;                // 0..1（UI で ×100）。最適化制約と二重活用
  max_position_weight: number;               // 0..1
  sector_caps: Record<string, number>;       // 業種→上限（値は 0..1）
  target_return: number | null;
  no_leverage: boolean;                       // DB は INTEGER → bool 変換
  exclusions: string[];                       // DB は JSON
}
export interface PolicyUpdate {  // PUT body（core と rationale を別々に更新可・部分更新可）
  core?: Partial<PolicyCore>;
  rationale?: string;
}
```
- backend Pydantic（案）: `PolicyOut` / `PolicyCoreOut` / `PolicyUpdateIn`。`no_leverage` の int↔bool・`sector_caps`/`exclusions` の JSON↔型変換はルータ層。
- lib/api.ts: `getPolicy()` / `putPolicy(update: PolicyUpdate)`。
- **`GET /policy` は core と rationale を分けて返す**（screens.md §3・[api.md §7] の宿題を本書で確定）。
→ **[確定済 _arbitration 決定2]** `target_cash_ratio`/`max_position_weight`/`sector_caps` の値は**すべて 0..1**（DB・API・Tool）、UI 表示時のみ ×100 → %。policy が最適化制約（§P2-6）と二重活用＝ADR-013 のため横断で 0..1 を貫く。
→ **[要一致 ai-advisor]** PUT がチャット経由更新（advisor 側）と Policy 画面の直接編集の**両方の入口**になる。両者が同じ `PUT /policy` を叩く。

### P3-2. `GET /journal`
```ts
export interface JournalEntry {
  id: number;
  date: string;
  observations: string;                 // AI 所見（自由文・Dashboard/Journal で本文表示）
  proposal: string | null;              // 当日の提案（自由文 or 参照）
  proposed_policy_change: PolicyUpdate | null;  // 方針変更案 JSON（任意）
  policy_snapshot: Policy | null;       // その時点の policy まるごと（履歴）
  llm_model: string | null;             // 監査用（Dashboard モック meta "openrouter" 相当）
  created_at: string;
  // situation_briefing（構造化事実 JSON）は一覧では返さず、詳細でのみ（重いので任意展開）
}
export interface JournalResponse { entries: JournalEntry[]; }  // date 降順
```
- query: `from`/`to`（任意・範囲）。省略時は直近 N 件。
- backend Pydantic（案）: `JournalEntryOut` / `JournalResponse`。
- lib/api.ts: `getJournal(opts?: { from?: string; to?: string })`。
→ **[要一致 ai-advisor]** `situation_briefing` を一覧で返すか（重い JSON）。本書は**一覧では省略、必要なら別途 `GET /journal/{id}`**。← ai-advisor が夜の分析で書く中身と要一致。

### P3-3. `GET /proposals`・`POST /proposals/{id}/approve`・`/reject`
screens.md §3「夜の分析AI 提案」・§6 (a)(c)。**承認は status 遷移のみで約定しない**（ADR-019・screens.md §6 (c)）。
```ts
export interface Proposal {
  id: number;
  created_date: string;
  kind: "policy_change" | "buy" | "sell" | "rebalance";
  body: ProposalBody;                  // 提案内容（kind 依存・JSON）
  rationale: string;                   // 根拠（AI の説明）
  status: "pending" | "approved" | "rejected";
  outcome: string | null;
  resolved_at: string | null;
  journal_id: number | null;
  depends_on: number | null;           // 提案間依存（policy_change → buy 等）。FK→proposals.id（決定4・確定）
}
// kind 別の body（入れ物として規定。中身の確定は ai-advisor）
export interface ProposalBody {
  // policy_change: { core?: Partial<PolicyCore>; rationale?: string }
  // buy/sell:      { code, shares?, target_weight? }
  // rebalance:     { weights: { code, target_weight }[] }
  [k: string]: unknown;
}
export interface ProposalsResponse { proposals: Proposal[]; }
export interface ResolveResult { proposal: Proposal; }  // approve/reject 後の最新状態
```
- `GET /proposals?status=` → `ProposalsResponse`（`status` 省略時は全件・`pending` 既定でもよい）。
- `POST /proposals/{id}/approve` body `{ outcome?: string }` → `ResolveResult`（status=approved）。
- `POST /proposals/{id}/reject` body `{ outcome?: string }` → `ResolveResult`（status=rejected）。
- backend Pydantic（案）: `ProposalOut` / `ProposalsResponse` / `ResolveIn` / `ResolveResult`。
- lib/api.ts: `getProposals(status?)` / `approveProposal(id, outcome?)` / `rejectProposal(id, outcome?)`。
→ **[確定済 決定4]** `proposals.depends_on INTEGER NULL`（FK→proposals.id）を追加（`0006_advisor_state` DDL・ai-advisor 管轄）。UI は依存元が approved になるまで承認ボタンを無効化。
→ **[要一致 ai-advisor]** `body` の kind 別スキーマ。本書は入れ物（`ProposalBody`）を規定。中身は ai-advisor 確定。

### P3-4. 画面コンポーネント（Policy / Journal / Proposals）
- **`frontend/src/app/policy/page.tsx`**（新規・screens.md #8）: 構造化コア（チップ/グリッド・編集可）＋ rationale（テキストエリア）。保存は `putPolicy`。「チャットで調整」導線（AdvisorChat を開く）も併置。
  - **`frontend/src/components/policy/PolicyEditor.tsx`**（新規）。props `{ policy: Policy; onSave(update: PolicyUpdate) }`。Dashboard の policy カード（`app/page.tsx` の core グリッド・rationale 引用ブロック）を編集可能版に拡張。
- **`frontend/src/app/journal/page.tsx`**（新規・screens.md #9）: 日記一覧（`getJournal`）。各エントリに observations 本文＋`policy_snapshot` の差分チップ（前日との変化）。markdown でなく自由文。
- **`frontend/src/app/proposals/page.tsx`**（新規・screens.md #10）: status タブ（pending/approved/rejected）＋承認/却下 UI。Dashboard の提案カード（`app/page.tsx` の proposals ブロック）を一覧化。
  - **`frontend/src/components/proposals/ProposalCard.tsx`**（新規）。props `{ proposal; onApprove; onReject }`。kind バッジ（POLICY=`accent-weak/accent`・BUY=`up-weak/up`・SELL=`down-weak/down`）・`depends_on` 未承認なら承認ボタン無効＋注記。
- **Dashboard 実配線**: `app/page.tsx` の `policy`/`proposals`/`journal` を `getPolicy()`/`getProposals("pending")`/`getJournal()` に差し替え。`mock-data.ts` の該当 mock 削除。提案カードの「承認/却下」を `approveProposal`/`rejectProposal` に接続。

### P3-5. `POST /chat`（context ＋ ストリーミング ＋ tool 可視化）
advisor.md §6・§6.1・screens.md §4・§5。[ADR-024][ADR-025]。**現状 `{messages}`→`{reply}` を拡張**。

- **request body**:
```ts
export interface ChatRequest {
  messages: ChatMessage[];           // 既存（毎ターン全送信・サーバはステートレス）
  context?: ChatContext;             // ADR-025 軽量ヒント（数値は含めない）
}
export interface ChatMessage { role: "user" | "assistant"; content: string; }
export interface ChatContext {
  page: string;                      // "dashboard" | "stock_detail" | "portfolio" | "signals" | ...
  focus?: FocusRef;                  // 対象が無いページは省略
}
// _arbitration 決定3（B-4）の正本＝ai-advisor 形。
// stock/signal は code、portfolio/proposal は id を使う（type で使い分け）。
export interface FocusRef {
  type: "stock" | "portfolio" | "signal" | "proposal";
  code?: string;                     // stock / signal
  id?: number;                       // portfolio / proposal
}
```
- **page の値**: ルート → page 名のマッピングを `lib/api.ts`（or AdvisorChat）に定数で持つ。`/` → `dashboard`・`/stocks/[code]` → `stock_detail`（focus `{type:"stock", code}`）・`/portfolio` → `portfolio`・`/signals` → `signals` 等。**数値・画面データは載せない**（ADR-025）。
- **response（非ストリーミング・Phase 3 既定）**:
```ts
export interface ChatResponse {
  reply: string;
  tool_runs?: ToolRun[];             // AI が呼んだ Tool（UI に「⚙ get_signals 実行」表示＝screens.md §4）
}
export interface ToolRun { name: string; args?: Record<string, unknown>; }  // 結果値は載せない（事実は reply に反映済み・ADR-025）
```
- **tool 可視化フィールドの正本 = `tool_runs: [{name, args?}]`**（_arbitration 決定3・B-5 で app 案採用）。`tool_calls_made: string[]` は廃止。**結果の数値は載せない**（ADR-025）。
- **ストリーミング**: _arbitration 決定7（L-16）で **Phase 3 は非ストリーミング**（`{reply, tool_runs}` 同梱）に確定。SSE は将来拡張として `sendChatStream` の口だけ予約（Phase 3 では実装しない）。
- backend Pydantic（案）: `ChatRequest` / `ChatMessage` / `ChatContext` / `FocusRef` / `ChatResponse` / `ToolRun`。**既存 `Message`（user/assistant）を流用**（system 不可は維持）。
- lib/api.ts: チャットを **`lib/api.ts` に集約**（現状 AdvisorChat 内の直接 fetch を移す）。`sendChat(req: ChatRequest): Promise<ChatResponse>`（非ストリーミング・Phase 3 はこれのみ）。`sendChatStream(req, onEvent)` は将来の SSE 用に口だけ予約。
→ **[DOCS要修正]** _current-state.md §6 の通り現状 body に context なし。本書で `ChatRequest.context` を追加（lead が api.md §4・§7 に反映）。

### P3-6. AdvisorChat 拡張（既存 `components/advisor/AdvisorChat.tsx` を変更）
- **画面コンテキスト送信**（ADR-025）: 現状ハードコードの「見ているページ: Dashboard」を `usePathname()` ＋ route→page マップで実値化し、`ChatRequest.context` に載せる。**root layout 配置のまま**（ADR-024・遷移で会話保持）。
- **Tool 実行の可視化**（screens.md §4）: `ChatResponse.tool_runs` を assistant バブル上部にチップ表示（「⚙ get_signals 実行」）。SSE なら逐次表示。
- **会話の永続**: → **[OPEN-G]** 会話履歴の永続先。推奨: **`localStorage`**（ADR-024「クライアント保持・実装時に決める」に沿う・サーバはステートレス維持）。DB 永続は journal スナップショット（ADR-013）と役割が別なので**持たない**。
- **ドラッグ/リサイズ/最小化**: 現状ドラッグ・最小化（開閉）はあり。**リサイズ**未実装（screens.md §4）→ 角ハンドル追加 or `react-rnd` 導入。
→ **[OPEN-H]** リサイズ実装手段（自前ハンドル vs `react-rnd` 依存追加）。推奨: 自前（既存 pointer ロジックがある・依存を増やさない）。

### P3-7. Sidebar nav
- `Advisor`/`Policy`/`Journal`/`Proposals`（P3）を href 化（`/policy`・`/journal`・`/proposals`）。**Advisor は常駐チャットが実体**（ADR-024）なので、nav の「Advisor」はチャットを開くボタン（href なし・onClick で AdvisorChat を open）にする。
→ **[OPEN-I]** nav「Advisor」の扱い。推奨: 専用ページを作らず、nav 項目はチャット起動トリガにする（screens.md #7「実体は全ページ常駐」と一致）。

### P3 新規/変更ファイル
- 新規: `app/policy/page.tsx`・`app/journal/page.tsx`・`app/proposals/page.tsx`・`components/policy/PolicyEditor.tsx`・`components/proposals/ProposalCard.tsx`。
- 変更: `lib/api.ts`（Policy/Journal/Proposal/Chat 型・関数・chat 集約）・`components/advisor/AdvisorChat.tsx`（context・tool_runs・localStorage・リサイズ）・`lib/mock-data.ts`（nav・mock 削除）・`app/page.tsx`（Dashboard 配線）。
- 着工順: policy GET/PUT → Policy 画面 → proposals → Proposals 画面＋Dashboard 配線 → journal → chat 拡張（context/tool/SSE）→ AdvisorChat 改修。

---

## Phase 4 — Watchlist / 銘柄ドシエ

`docs/screens.md` #3・#11・[ADR-020]。ドシエは**銘柄詳細内のセクション/タブ**（screens.md #3 注記）。

### P4-1. `GET/POST/DELETE /watchlist`
```ts
export interface WatchlistItem {
  id: number;
  code: string;
  company_name: string | null;       // stocks JOIN
  note: string | null;
  added_at: string;
  last_investigated_at: string | null;  // stock_dossiers JOIN（一覧の「最終調査日」）
  stale: boolean;                        // last_investigated_at が古い（しきい値超）→ 再調査促す
}
export interface WatchlistResponse { items: WatchlistItem[]; }
export interface WatchlistInput { code: string; note?: string | null; }
```
- `GET /watchlist` → `WatchlistResponse` / `POST` body `WatchlistInput` → 追加行 / `DELETE /watchlist/{id}` → `{ ok: true }`。
- **`last_investigated_at` は `stock_dossiers` JOIN**・`stale` は backend がしきい値判定（screens.md §3 警告色）。
- backend Pydantic（案）: `WatchlistItemOut` / `WatchlistResponse` / `WatchlistIn`。
- lib/api.ts: `getWatchlist()` / `addWatchlist(input)` / `removeWatchlist(id)`。
→ **[OPEN-J]** `stale` しきい値（例 14 日 or 21 日）。Dashboard モックは「23日前=stale」。推奨: 既定 21 日・backend 算出。

### P4-2. `GET /dossiers/{code}`・`POST /dossiers/{code}/investigate`
[ADR-020]・data-model.md（`stock_dossiers`/`dossier_sources`）。
```ts
export interface Dossier {
  code: string;
  summary_md: string;                 // markdown（UI でそのまま描画）
  key_facts: Record<string, unknown> | null;  // PER/成長率/直近トピック等（構造化）
  last_investigated_at: string | null;
  updated_at: string | null;
  sources: DossierSource[];           // ソース台帳（要約＋URL・本文なし）
}
export interface DossierSource {
  id: number;
  source_type: "news" | "disclosure" | "twitter" | string;
  url: string;
  title: string | null;
  summary: string | null;             // 短い要約（本文は保存しない＝ADR-020）
  published_at: string | null;
}
export interface InvestigateResult { dossier: Dossier; }  // 調査後の最新ドシエ
```
- `GET /dossiers/{code}` → `Dossier`（未調査なら 404 or `summary_md: ""`＋空 sources）。
- `POST /dossiers/{code}/investigate` → `InvestigateResult`（`investigate_stock` 起動。チャットの「この銘柄調査して」と共用パイプライン＝ADR-020）。**長時間処理**の可能性 → [OPEN-K]。
- backend Pydantic（案）: `DossierOut` / `DossierSourceOut` / `InvestigateResult`。
- lib/api.ts: `getDossier(code)` / `investigateStock(code)`。
→ **[OPEN-K]** investigate の同期/非同期。推奨: **同期（処理完了まで待って最新ドシエ返す）**で着工し、遅ければ後で「ジョブ ID → ポーリング」へ。← ai-advisor（調査パイプラインの所要時間）と要一致。
→ **[要一致 ai-advisor]** `key_facts` の構造（PER/成長率等のキー）・`summary_md` の生成。本書は入れ物（markdown ＋ `Record`）を規定。
- **markdown 描画**: → **[OPEN-L]** markdown レンダラ。推奨: `react-markdown`（軽量・依存追加）。XSS は AI 生成 markdown のみ・LAN 単一ユーザーだが `rehype-sanitize` 併用。

### P4-3. 画面コンポーネント
- **`frontend/src/app/watchlist/page.tsx`**（新規・screens.md #11）: watchlist 一覧（最終調査日・stale 警告色・「調査/再調査」ボタン＝`investigateStock`）。Dashboard モック watchlist の実配線版。
- **銘柄詳細にドシエセクション追加**（`app/stocks/[code]/page.tsx` を変更）: 既存チャートの下に **`frontend/src/components/dossier/DossierSection.tsx`**（新規）。props `{ code: string }`。`getDossier(code)` → markdown 描画＋ソース一覧（URL リンク・要約）＋「調査する」ボタン（`investigateStock`）。watchlist 追加ボタンも併置（screens.md #3 ハブ）。
  - DESIGN.md: markdown 本文は `text-ink leading-[1.55]`・見出しはサイズ＋色階層（DESIGN.md「太さの振り幅は狭く」）・ソースは `hairline-soft` 区切りリスト・`source_type` バッジ。
- **Dashboard 実配線**: watchlist カードを `getWatchlist()` に差し替え。`mock-data.ts` の `watchlist` 削除。

### P4-4. Sidebar nav
- `Watchlist`（P4）を `href: "/watchlist"` 化。

### P4 新規/変更ファイル
- 新規: `app/watchlist/page.tsx`・`components/dossier/DossierSection.tsx`。
- 変更: `lib/api.ts`（Watchlist/Dossier 型・関数）・`app/stocks/[code]/page.tsx`（ドシエセクション追加）・`lib/mock-data.ts`（nav・watchlist mock 削除）・`app/page.tsx`（Dashboard watchlist 配線）。依存追加: `react-markdown`(+`rehype-sanitize`)（[OPEN-L]）。
- 着工順: lib/api.ts → watchlist 画面＋Dashboard 配線 → 銘柄詳細ドシエセクション → investigate 接続。

---

## Phase 6 — 通知設定 / 履歴（必要なら）

`docs/api.md §6`・[ADR-007]（Discord Webhook）。通知は backend（Discord）が送る。UI は**設定確認＋手動バッチ起動**が中心で、新規画面は最小。

### P6-1. `POST /batch/run`（既存 api.md §6・手動バッチ）
正本 = data-arch（_arbitration 決定6・B-11）。**非同期 202**。
```ts
export interface BatchRunRequest { full_backfill?: boolean; }   // 既定 false（差分のみ）。true で約2年分一括
export interface BatchRunResponse { started: boolean; job_id?: string; }
```
- `POST /batch/run` body `BatchRunRequest` → **202** `BatchRunResponse`（非同期起動）。ロック競合（夜間バッチ実行中・手動バッチ重複）は **409**（`detail` で理由）。Dashboard の「バッチを今すぐ実行」（`app/page.tsx` 既存ボタン）と Settings から起動。
- lib/api.ts: `runBatch(opts?: { full_backfill?: boolean }): Promise<BatchRunResponse>`（202/409 を `getJSON`/`postJSON` の `detail` 拾いで扱う・409 は「実行中なのだ」表示）。
→ **[確定済 data-arch]** body `{full_backfill?}`・成功 `202 {started, job_id?}`・競合 `409`（_arbitration 決定6）。`tasks[]` は YAGNI で不採用（Phase 1 は全ジョブ実行のみ）。

### P6-2. 通知設定 UI（最小）
- → **[OPEN-M]** 通知設定を API 化するか `.env` 固定か。推奨: **Discord Webhook URL は `.env` 固定**（秘密情報は backend のみ＝CLAUDE.md・frontend に渡さない）。UI は**通知の ON/OFF・しきい値**程度を `GET/PUT /settings/notifications` で持つ（必要になってから）。本 Phase は Settings 画面の health 表示＋バッチ起動ボタンで十分。
- **`frontend/src/app/settings/page.tsx`**（新規・screens.md #14）: `/health` 詳細表示（既存 Topbar バッジの拡張）＋「夜間バッチ手動起動」（`runBatch`）＋（必要なら）通知 ON/OFF。
- Sidebar `Settings` を `href: "/settings"` 化。

### P6 新規/変更ファイル
- 新規: `app/settings/page.tsx`。
- 変更: `lib/api.ts`（runBatch・health 型・必要なら notifications）・`lib/mock-data.ts`（Settings nav href 化）・`app/page.tsx`（Dashboard の「バッチを今すぐ実行」を `runBatch` に接続）。

---

## 付録 A. lib/api.ts ヘルパの追加（全 Phase 共通）

既存 `getJSON` に加え、書き込み系で使うヘルパを足す（既存の `detail` 拾い流儀を踏襲）:
```ts
async function postJSON<T>(path: string, body: unknown): Promise<T> { /* POST + JSON + detail 拾い */ }
async function putJSON<T>(path: string, body: unknown): Promise<T> { /* 同上 PUT */ }
async function del<T>(path: string): Promise<T> { /* DELETE */ }
```
- これらは P2 以降の transactions/cash/external-assets/policy/proposals/watchlist で共用。
- チャットは P3 で `sendChat`/`sendChatStream` を同ファイルへ集約（現状 AdvisorChat 内の直 fetch を移設）。

## 付録 B. route → ChatContext.page マップ（P3 で確定・ADR-025）

| route | page | focus |
|---|---|---|
| `/` | `dashboard` | （なし）|
| `/stocks` | `stocks` | （なし）|
| `/stocks/[code]` | `stock_detail` | `{ type: "stock", code }` |
| `/signals` | `signals` | （なし）|
| `/portfolio` | `portfolio` | （なし）|
| `/transactions` | `transactions` | （なし）|
| `/policy` | `policy` | （なし）|
| `/journal` | `journal` | （なし）|
| `/proposals` | `proposals` | （なし）|
| `/watchlist` | `watchlist` | （なし）|
| `/settings` | `settings` | （なし）|

> **数値は載せない**（ADR-025）。focus は「指示語の対象」だけ。

---

## 付録 C. [OPEN] 一覧（R3 後・大半は _arbitration で確定済）

> R3 で `_arbitration.md` が大半を確定。下表「状態」列が **確定**のものは裁定済み（_arbitration 参照）。**未確定**のみ残 OPEN。

| ID | 論点 | 結論 / 推奨 | 状態 |
|---|---|---|---|
| OPEN-A | 評価額系の遅延注記 | 各レスポンスに `is_delayed`+`as_of` を埋め込む（共通 `valuation_meta` ラッパは holdings のみ採用・他はフラット） | 確定（決定2: is_delayed/as_of） |
| OPEN-B | `/signals` の件数上限 | `limit`（既定 100）任意クエリ | 推奨（quant と最終確認） |
| OPEN-C | 既定ポートフォリオ解決 | `GET /portfolios` 先頭を既定（L-9） | 確定 |
| OPEN-D | Transactions の nav 位置 | Portfolio 内タブ | app 裁量・確定 |
| OPEN-E | 提案間依存（policy→buy） | `proposals.depends_on INTEGER NULL` 追加・UI で承認順制御（決定4） | 確定 |
| OPEN-F | `/chat` ストリーミング | **Phase 3 は非ストリーミング**+`tool_runs` 同梱（L-16） | 確定 |
| OPEN-G | 会話履歴の永続先 | `localStorage`（U-6・サーバはステートレス維持） | 既定（ユーザー確認に回す） |
| OPEN-H | チャットのリサイズ実装 | 自前 pointer ハンドル（依存増やさない） | app 裁量・確定 |
| OPEN-I | nav「Advisor」の扱い | 専用ページなし・チャット起動トリガ | app 裁量・確定 |
| OPEN-J | watchlist `stale` 閾値 | 21 日・backend 算出（L-22） | 確定 |
| OPEN-K | investigate 同期/非同期 | 同期で着工・遅ければジョブ化（L-23） | 確定 |
| OPEN-L | ドシエ markdown レンダラ | `react-markdown`+`rehype-sanitize`（L-24） | 確定 |
| OPEN-M | 通知設定 API 化 or .env | Webhook は .env・UI は最小 | 推奨（data-arch と確認） |

> **残ユーザー確認**: OPEN-G（会話履歴=localStorage）は `_open-questions.md`（U-6）でユーザー確認に回る。値は env/設定で後差し替え可。

## 付録 D. [DOCS要修正] 一覧

- **D-1**: `docs/api.md §4`・§7 — `POST /chat` の body に `context` フィールドを明記（現 docs は「実装時に確定」止まり。本書 §P3-5 で `ChatRequest.context` を確定）。
- **D-2**: `docs/screens.md §6 (b)` — 逸脱計算の置き場所「`get_asset_overview` に含めるか新 Tool か未定」を、本書 §P2-7 の結論「`/asset-overview` の `deviations` に含める」で確定。
- **D-3**: `docs/api.md §7` — `/quotes`・`/journal` のページネーション「当面なし（範囲指定で代替）」を明記（本書 §0.4）。
- **D-4**: `docs/api.md §7` — `GET /policy` の core/rationale 分離レスポンス形を本書 §P3-1 で確定（既存宿題の解消）。

---

## 突き合わせサマリ（R3 後・他レーン依存・一覧）

R3 で `_arbitration.md` に揃えた。残る依存は中身の数値定義のみ。

- **quant（中身の数値定義）**: `Signal.payload` の type 固有キー（P1・label/change_5d は quant 格納で確定）／`Holding.market_value`・`unrealized_pnl` の算出（P2-2・weight=株式内 0..1 確定）／`PortfolioMetrics`（annual_return/volatility/sharpe/max_drawdown/lookback の値・P2-5）／`OptimizeResult` の objective/expected_* の値（P2-6・weights は配列 0..1 で確定）／`deviations` 計算式（P2-5/P2-7・単一関数で両出力）。**型・単位（0..1）・フィールド名は _arbitration 決定2 で正本確定**。
- **data-arch**: `signals` JOIN stocks（P1・確定）／`proposals.depends_on` 列（決定4・確定）／`/batch/run` `{full_backfill?}`→`202 {started,job_id?}`・`409`（P6・確定）／既定ポートフォリオ初期行 `(1,'Default')`（決定7 L-8/L-9・確定）。
- **ai-advisor**: `/chat` の `context.focus={type,code?,id?}`・`tool_runs:[{name,args?}]`・Phase 3 非ストリーミング（P3-5・決定3/L-16 確定）／`PolicyCore` 単位 0..1 の二重活用（P3-1・P2-6・確定）／`ProposalBody` の kind 別中身（P3-3・中身は ai-advisor）／`Dossier.key_facts`・investigate 同期（P4-2・L-23 確定）／`journal.situation_briefing`（P3-2）。

> **単位の統一（R3 確定）**: `target_cash_ratio`/`max_position_weight`/`sector_caps`/`weight`/`current_weight`/`target_weight`/`delta`/`cash_weight`/`deviations.current,limit`/`allocation.weight` を **DB・API・Tool ですべて 0..1**、UI 表示時のみ ×100 → %（_arbitration 決定2）。遅延フラグは `is_delayed`、鮮度は `as_of`。correlation は `{codes,labels,matrix}`、weights は配列。policy が最適化制約と二重活用（ADR-013）のため 0..1 を横断で貫く。
