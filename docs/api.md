# REST API 契約（Next.js ↔ FastAPI）

「Next＝見せる係 / FastAPI＝データ・計算係」の**境界＝この API 契約**（[decisions.md ADR-005](decisions.md)）。Next.js は DB を触らず、すべてここを経由する。

> **契約の正本は FastAPI が自動生成する OpenAPI（`/docs`・`/openapi.json`）とする。** 本ドキュメントは主要エンドポイントの一覧と方針を示すもので、列の細部は実装時に OpenAPI 側で確定する。パスは設計案。

---

## 0. 共通方針

- **ベース URL**: backend は `http://<host>:8000`（FastAPI）。ブラウザからは frontend の相対パス **`/api`** を叩き、Next の rewrites が裏で backend へ転送する（同一オリジン化＝[ADR-037](decisions.md)。例: ブラウザの `/api/stocks` → backend `/stocks`）。CORS は不要（[architecture.md 7](architecture.md)）。
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
| GET | `/lead-lag` | 日米業種リードラグ（米国業種ショック → 翌営業日の日本業種スコア）の業種ランキング＋検証メタ。提示専用（[ADR-009](decisions.md)/[ADR-039](decisions.md)）。下記スキーマ |
| GET | `/stocks/screen` | バリュエーション条件で日本株全銘柄を絞り込むスクリーナー（読み取り時計算・[ADR-031](decisions.md)）|

## 3. ポートフォリオ・資産（Phase 2〜）

| メソッド | パス | 用途 |
|---|---|---|
| GET/POST/PUT/DELETE | `/portfolios`, `/portfolios/{id}` | ポートフォリオ CRUD（当面は単一固定）|
| GET/POST/PUT/DELETE | `/holdings` | 保有 CRUD |
| GET | `/transactions?portfolio_id=` | 取引履歴（新しい順・会社名付き）|
| POST | `/transactions` | 取引（買い/売り）記録 → holdings 再計算 |
| PUT | `/transactions/{id}` | 取引の編集 → holdings 再計算 |
| DELETE | `/transactions/{id}` | 取引の削除 → holdings 再計算（存在しない id は 404）|
| GET/PUT | `/cash` | 現金残高 |
| GET/POST/PUT/DELETE | `/external-assets` | 投信等（割合文脈）|
| GET | `/portfolio/{id}/metrics` | 相関・シャープ・最大ドローダウン |
| POST | `/portfolio/{id}/optimize` | policy 制約下の最適比率 |
| GET | `/portfolio/{id}/backtest` | 過去シミュレーション（現保有 buy&hold vs TOPIX） |
| GET | `/asset-overview` | 保有・現金・割合・資産推移（遅延注記付き・**`fund_value`・`us_stock_value` を含む**＝[ADR-054](decisions.md)/[ADR-057](decisions.md)）|

### 投資信託（非上場投信・[ADR-054](decisions.md)）

NAV を日次取得し含み損益を随時計算する投信専用系統。株（`/holdings`・`/transactions`）の取引ベース導出（[ADR-019](decisions.md)）をミラーする。識別子は **ISIN**、価格・口数・取得単価は**「10,000 口あたりの円」**。`external_assets`（割合文脈・軽量）とは別系統。

| メソッド | パス | 用途 |
|---|---|---|
| GET | `/funds` | 投信マスタ一覧 → `[{isin, name, assoc_code, updated_at}]` |
| POST | `/funds` | 投信を登録（body `{isin, name, assoc_code?}`）→ 登録した 1 件 |
| DELETE | `/funds/{isin}` | 投信を削除 → `{ok}` |
| GET | `/fund-transactions?portfolio_id=` | 投信の取引履歴 → `[{id, portfolio_id, isin, side, units, price, fee, traded_at}]` |
| POST/PUT/DELETE | `/fund-transactions`, `/fund-transactions/{id}` | 投信取引の記録・編集・削除 → mutation 後、評価額付き保有 `FundHolding[]` を返す（holdings 再計算と同型・atomic＝[ADR-019](decisions.md)）|
| GET | `/fund-holdings?portfolio_id=` | 投信の保有（評価額・含み損益・配分つき）→ `FundHolding[]` |
| GET | `/funds/{isin}/nav-series?limit=` | NAV 推移（チャート用）→ `[{date, nav}]` |

```ts
interface FundHolding {
  isin: string; name: string;
  units: number; avg_cost: number;        // avg_cost は 10,000 口あたり円
  last_nav: number; nav_date: string;     // YYYY-MM-DD
  market_value: number;                   // units/10000*last_nav
  unrealized_pnl: number;                 // units/10000*(last_nav-avg_cost)
  weight: number;                         // 投信内の配分（0..1）
}
```

`POST`/`PUT`/`DELETE /fund-transactions` のレスポンスは、取引 mutation と同一トランザクション内で再計算した `FundHolding[]` を返す（取引が一次データ、保有はその導出＝[ADR-019](decisions.md)）。存在しない id は 404。NAV は投信総合検索ライブラリーの CSV（ISIN 指定・遅延なし実値）をアダプタ越しに取得する（[ADR-010](decisions.md)）。**`/optimize` への投信組み込みは見送り**（[ADR-054](decisions.md) の将来課題）。

### `GET /portfolio/{id}/backtest`

現保有ウェイトの buy&hold を指数（TOPIX=`^TPX`）と比較する（phase2-spec.md §4.4）。計算は quant 純関数 `backtest_portfolio`。保有 0・履歴不足・ベンチ未取得は空 leg（`as_of=null` / `curve=[]`）を 200 で返す。

```ts
interface BacktestCurvePoint { date: string; value: number } // value は 1 始まりの倍率
interface BacktestLeg {
  cumulative_return: number; annual_return: number;
  sharpe: number | null; max_drawdown: number;
  curve: BacktestCurvePoint[];
}
interface BacktestResult {
  portfolio_id: number; as_of: string | null; is_delayed: boolean;
  portfolio: BacktestLeg; benchmark: BacktestLeg; excess_return: number;
}
```

### 取引履歴の一覧・編集・削除（`GET`/`PUT`/`DELETE` `/transactions`）

取引の訂正手段。**transactions が一次データ、holdings はそこからの導出**（[ADR-019](decisions.md)）なので、取引を直すと holdings は `recalc_holdings` で自動再計算され、`PUT`/`DELETE` は再計算後の holdings を同梱して返す。`POST /transactions` と同一トランザクション内で atomic。

```ts
interface TransactionOut { // GET の 1 行。company_name は stocks JOIN で補完
  id: number; code: string; company_name: string | null;
  side: "buy" | "sell"; shares: number; price: number;
  fee: number | null; traded_at: string; // YYYY-MM-DD
}
// TransactionInput（PUT の body）は POST /transactions と同一
// TransactionResult（PUT/DELETE のレスポンス）は POST と同一 = { transaction_id, holdings }
```

- `GET /transactions?portfolio_id=` → `TransactionOut[]`（**新しい順**）。
- `PUT /transactions/{id}` body=`TransactionInput` → `TransactionResult`（`holdings` は再計算後）。存在しない id は 404。
- `DELETE /transactions/{id}` → `TransactionResult`（`transaction_id`=削除した id・`holdings` は再計算後）。存在しない id は 404。

## 4. AI Advisor（Phase 3〜）

| メソッド | パス | 用途 |
|---|---|---|
| GET/PUT | `/policy` | 現在の投資方針の取得・更新 |
| GET | `/journal?from=&to=` | 投資日記の取得 |
| GET | `/journal/{id}` | 投資日記 1 件の詳細（`situation_briefing` 込み・監査用）|
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
| PATCH | `/watchlist/{code}` | 銘柄ごとの調査間隔 `interval_days` を更新（[ADR-033](decisions.md)）|
| GET | `/dossiers/{code}` | 銘柄の調査レポート（markdown）と要約ソース一覧（未調査でも 200＋空ドシエ＝`summary_md: ""`＋`last_investigated_at: null`・確定・phase4-spec §5.2）|
| POST | `/dossiers/{code}/investigate` | その銘柄を調査（`investigate_stock` を起動。チャットの「この銘柄調査して」と共用）|
| GET | `/general-news` | 銘柄に紐づかない直近の一般ニュース（市況・マクロ・世界情勢）をカテゴリ別に返す（[ADR-034](decisions.md)）|
| GET | `/news` | 統合ニュースを `level`/`since`/`limit` で一覧（新着順・[ADR-047](decisions.md)）|
| GET | `/news/search` | 統合コーパスを**意味で過去横断検索**（[ADR-045](decisions.md) 段階A）。クエリ `q`（必須）／`level`・`code`・`sector17_code`・`since`・`until`（任意の絞り込み）／`limit`（既定 20）。レスポンスは `{items: NewsItem[], reason?}`。embedding 未設定・`sqlite-vec` 未ロード等で検索できないときは **200＋`items: []`＋`reason`**（UI を壊さない＝[ADR-018](decisions.md)）|
| POST | `/news` | 貼付テキストを要約して統合コーパスに投入（同期・要約失敗時は 502・[ADR-046](decisions.md)）|
| DELETE | `/news/{id}` | ユーザー投入（`source='user'`）のニュースを削除（自動取得分は 404・[ADR-046](decisions.md)）|

## 6. システム

| メソッド | パス | 用途 |
|---|---|---|
| GET | `/health` | 死活監視・必須環境変数の充足チェック |
| POST | `/batch/run` | 夜間バッチの手動起動（cron と共用。body `{full_backfill?}`・202／実行中は 409）|
| GET | `/batch/status` | バッチ実行状態（`running`/`current_job`/`started_at`/`full_backfill`/`stop_requested`・[ADR-036](decisions.md)）|
| POST | `/batch/stop` | 走行中バッチに停止を要求（協調キャンセル＝今のジョブ完了後に止まる・`{stopping}`・[ADR-036](decisions.md)）|
| POST | `/edinet/run-differential` | EDINET 差分タグ付け（取得＋cap タグ付け）の手動起動（テーマタグ段階C・`run_jobs`・202／実行中は 409・進捗は `/batch/status`・[ADR-056](decisions.md)）|
| POST | `/diagnostics/discord-test` | Discord 疎通テスト（冪等回避＝毎回飛ぶ・`{enabled,sent}`・[ADR-011](decisions.md)）|
| POST | `/diagnostics/jquants-test` | J-Quants V2 認証ピング（接続値は DB 解決・`{configured,ok,detail}`・[ADR-036](decisions.md)/[ADR-061](decisions.md)）|

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

### `GET /lead-lag` のレスポンス（業種リードラグ・確定）

`signals`（`signal_type='lead_lag'`）の最新日を読み、業種ランキング＋検証メタを返す（[ADR-039](decisions.md)・[methods/lead-lag.md](methods/lead-lag.md)）。数値はすべて夜間バッチが算出済みの事実で、API は読むだけ（[ADR-014](decisions.md)）。

```jsonc
GET /lead-lag ->
  {
    as_of,                 // この応答の生成基準日（最新の共通営業日）
    ranking: [             // score 降順の日本業種ランキング
      {
        code,              // JP 業種 ETF コード（例 "16170"）
        label,             // 業種和名
        score,             // 横断 0..1 正規化スコア（提示用）
        signal             // 正規化前の raw シグナル値
      }
    ],
    meta: {
      plan,                // J-Quants プラン（"free" | "light" | ...）
      is_delayed,          // 株価が遅延しているか（Free=true → frontend で低信頼バナー）
      model_as_of,         // シグナル算出に使ったモデル基準日（Free だと約3ヶ月前）
      ic,                  // Spearman IC（検証・履歴で算出）
      hit_rate,            // 方向的中率（3分位ロングショート q=0.3）
      window,              // 推定窓 L（=60）
      k,                   // 採用主成分数 K（=3）
      lambda               // 正則化強度 λ（=0.9）
    }
  }
```

Free プラン時も `ranking` は返す（ハード無効化しない）。`meta.is_delayed=true` / `meta.model_as_of` を見て frontend が「シグナル日付が約3ヶ月古く実用外」の低信頼バナーを出す（[ADR-039](decisions.md)）。

### ページネーション

- `/quotes`・`/journal` の**ページネーションは当面なし**。期間は `from`/`to` の範囲指定で代替する（[DOC-6]）。データ量が問題になった段階で導入を検討する。

---

## 8. 米国株スクリーナー（Phase 7(B-1)・提示専用・[ADR-055](decisions.md)）

日本株（§1・§7 のスクリーナー）と**別ルート・別テーブル**（`us_stocks`/`us_daily_quotes`/`us_valuation_snapshots`・市場分離＝[ADR-031](decisions.md)）。すべて読み取り専用で、取得は夜間バッチ（`sync_us_universe`/`fetch_us_quotes`/`fetch_us_fundamentals`/`calc_us_valuation`）が担う。数値は USD（ドル）。**`currency` 列・FX 換算は持たない**（提示専用・(B-2) 送り）。派生比率・市場内ランクは読み取り時に Python 計算（[ADR-014](decisions.md)）。

| メソッド | パス | 用途 |
|---|---|---|
| GET | `/us-stocks?q=` | 米株マスタ一覧（symbol/銘柄名の部分一致）|
| GET | `/us-stocks/screen?...` | バリュエーションで絞り込み（下記スキーマ）|
| GET | `/us-stocks/{symbol}` | 米株詳細（マスタ＋valuation snapshot）|
| GET | `/us-quotes/{symbol}?from=&to=` | 米株日足（チャート用・date 昇順）|

- `/us-stocks/{symbol}`: **マスタ未取得は 404**、マスタはあるが valuation 未焼成なら **200＋`valuation: null`**（日本株 `/stocks/{code}` 同型・「あるものを返す」流儀）。
- ルータは `/us-stocks/{symbol}` より `/us-stocks/screen` を先に宣言する（"screen" が `{symbol}` に食われないため）。

### `GET /us-stocks/screen` のクエリと行（確定）

クエリは `UsScreenCriteria`（router の Pydantic を正本）。比率系は 0..1（×100 は UI 側）。`gics_sector` は文字列完全一致。各 `*_growth_yoy` は素データ都合で NULL になり得るが min/max 比較で自然に除外される。

```jsonc
GET /us-stocks/screen?
  per_min= per_max= pbr_min= pbr_max=
  market_cap_min= market_cap_max=                  // USD
  dividend_yield_min= dividend_yield_max=          // 0..1
  roe_min= roe_max=
  operating_margin_min/max= net_margin_min/max=    // 0..1
  revenue_growth_yoy_min/max= op_growth_yoy_min/max=
  profit_growth_yoy_min/max= eps_growth_yoy_min/max=
  gics_sector=                                      // GICS 相当セクター（完全一致）
  exclude_etf=                                      // bool
  gics_sector_pctile_max=                           // GICS 内で安い割合（0..1）
  market_cap_rank_max=                              // 時価総額 上位 N
  sort_by=                                          // per|pbr|market_cap|dividend_yield|roe|
                                                    // operating_margin|net_margin|*_growth_yoy|
                                                    // gics_sector_pctile|market_cap_rank|symbol
  sort_dir=asc|desc  limit=200  offset=0
->
  [ {
      symbol, company_name, gics_sector, industry, is_etf,
      as_of_date, close, eps, bps, dividend_per_share,
      per, pbr, market_cap, dividend_yield, roe,
      operating_margin, net_margin,
      revenue_growth_yoy, op_growth_yoy, profit_growth_yoy, eps_growth_yoy,
      gics_sector_pctile,  // GICS 業種内パーセンタイル（読み取り時 window 算出）
      market_cap_rank      // 時価総額 市場内順位（同上）
  } ]
  // op_growth_yoy / eps_growth_yoy は素が無く null（捏造しない）。
```

### `GET /us-stocks/{symbol}` のレスポンス（確定）

```jsonc
GET /us-stocks/{symbol} ->
  {
    symbol, company_name, gics_sector, industry, is_etf,
    valuation: {          // 未焼成なら null
      symbol, company_name, gics_sector, industry, is_etf, as_of_date,
      close, eps, bps, dividend_per_share,
      per, pbr, market_cap, dividend_yield, roe,
      operating_margin, net_margin,
      revenue_growth_yoy, op_growth_yoy, profit_growth_yoy, eps_growth_yoy,
      gics_sector_pctile, market_cap_rank
    } | null
  }
```

`GET /us-quotes/{symbol}` は `[{ date, open, high, low, close, volume, adj_close }]`（date 昇順）。

> **AI Tool**: `get_us_valuation`／`screen_us_valuation`（`min_phase=7`・返り値に `market:"US"`/`currency:"USD"` 明示・verdict なし＝[ADR-048](decisions.md) 契約をミラー）。日本株は `get_valuation`/`screen_valuation`（JPY・[advisor.md](advisor.md)）。

---

## 9. 米株保有・FX（Phase 7(B-2)・[ADR-057](decisions.md)）

FX 基盤と米株保有管理。**日本株保有（`/holdings`/`/transactions`）とは別ルート**（単一ユーザー＝`portfolio_id` なし・[ADR-001](decisions.md)）。取引が一次データで保有は導出（[ADR-019](decisions.md)）。数値は USD（原価・約定価格）と JPY（評価額・`avg_cost_jpy`）の両建て。

| メソッド | パス | 用途 |
|---|---|---|
| GET | `/us-holdings` | 米株の現在保有一覧（USD/JPY 両評価・含み損益つき）|
| GET | `/us-transactions` | 米株の取引履歴（新しい順）|
| POST | `/us-transactions` | 米株取引の記録 → `us_holdings` 再計算。`fx_rate` 解決順: body 明示 → 約定日 FX（`fx_rates` から取得）→ どちらもなければ 400 |
| PUT | `/us-transactions/{id}` | 米株取引の編集 → `us_holdings` 再計算 → 再計算後の保有を返す（存在しない id は 404）。`fx_rate` 解決順は POST と同じ。symbol を変更した編集は旧 symbol 側の保有も再導出する |
| DELETE | `/us-transactions/{id}` | 米株取引の削除 → 再計算後の保有を返す（存在しない id は 404）|

```ts
interface UsHolding {
  id: number;
  symbol: string; company_name: string | null;
  gics_sector: string | null;
  shares: number;
  avg_cost: number | null;           // 移動平均取得単価（USD）
  avg_cost_jpy: number | null;       // 移動平均取得単価（JPY 固定・約定時 FX）
  last_close: number | null;         // 最新終値（USD）
  close_date: string | null;         // その終値の営業日
  fx_rate: number | null;            // 評価時 USDJPY（直近の fx_rates 取得値）
  market_value_jpy: number | null;   // shares × last_close × fx_rate
  cost_jpy: number | null;           // shares × avg_cost_jpy
  unrealized_pnl_jpy: number | null; // (last_close × fx_rate - avg_cost_jpy) × shares
  weight: number | null;             // 米株内合計に対する比率（0..1）
}

interface UsTransactionOut {
  id: number; symbol: string; company_name: string | null;
  side: "buy" | "sell"; shares: number;
  price: number;             // 約定単価（USD）
  fee: number | null; traded_at: string; fx_rate: number; note: string | null;
}
```

`GET /asset-overview` レスポンスに `us_stock_value`（JPY）が追加される（`fund_value` と並ぶ独立スライス）。`allocation` に「米国株」スライスが出る。

> **AI Tool**: `get_us_holdings`（`min_phase=7`）＝米株保有を JPY 評価で返す（日米横断バランス相談用・[ADR-057](decisions.md)）。

## 10. LLM 設定（プロバイダ複数登録・面別 provider/model・[ADR-058](decisions.md)）

`/settings` から LLM の provider を複数登録し、面（chat/nightly/dossier/tagger/triage）ごとに provider と model を割り当てる（`triage`＝知識カード審査・[ADR-062](decisions.md)）。`api_key` は GET では必ずマスク（末尾4桁）で返し、更新は **write-only**（空送信は据え置き）。codex は鍵なし組み込みで provider 一覧には出ず、面の割当で `provider_id=0` として選ぶ。

| メソッド | パス | 説明 |
|---|---|---|
| GET | `/llm/providers` | provider 一覧（`api_key_masked`・`has_api_key`） |
| POST | `/llm/providers` | 登録（`{name, base_url, api_key?, default_model?}`・name 重複は 409） |
| PUT | `/llm/providers/{id}` | 部分更新（`api_key` 空送信は据え置き＝write-only） |
| DELETE | `/llm/providers/{id}` | 削除（面が使用中なら 409） |
| POST | `/llm/providers/{id}/test` | `/v1/models` 疎通テスト（`{ok, detail}`・失敗も 200） |
| GET | `/llm/faces` | 全面の割当（`{face, provider_id, provider_name, model, reasoning_effort, configured}`・全面を必ず返す＝chat/nightly/dossier/tagger/triage） |
| PUT | `/llm/faces/{face}` | 割当更新（`{provider_id, model, reasoning_effort}`・`provider_id=0` で codex・`null` で未設定・未知 provider は 422） |
| POST | `/llm/codex/test` | codex 使用可否を実ターン 1 発で確認（`{ok, detail}`・失敗も 200・ADR-059） |
| GET | `/llm/embedding` | embedding 接続の現在値（`{base_url, api_key_masked, has_api_key, model, dim, configured}`・ADR-059） |
| PUT | `/llm/embedding` | embedding 更新（`{base_url?, api_key?, model?, dim?}`・api_key 空送信は据え置き） |
| POST | `/llm/embedding/test` | embedding に 1 件投げて疎通（`{ok, detail}`・未設定/失敗も 200） |

- **`reasoning_effort`**（ADR-059）: 空=既定 / `minimal` / `low` / `medium` / `high` / `xhigh`。openai 面は `chat.completions` の reasoning_effort、codex 面は thread config に渡る。非対応 model に設定すると provider が 400→チャットは 502（自動縮退しない）。

- **`ProviderOut`**: `{id, name, base_url, api_key_masked, has_api_key, default_model}`。生の `api_key` は返さない。
- **`FaceConfig`**: `provider_id`＝`null`(未設定)/`0`(codex)/`>0`(provider)。`configured`＝`resolve_face` が通るか（=その面の LLM が動くか）。
- 未設定面は [ADR-018](decisions.md)：チャットは `POST /chat` が 503、夜間/ドシエは通知付き skip、タグ付けは沈黙 skip。

## 11. J-Quants 設定（api_key・契約プラン・[ADR-061](decisions.md)）

`/settings` の「J-Quants 設定」カードから J-Quants V2 の `api_key` と契約プランを編集する（env から DB＝`jquants_config` へ移管）。`api_key` は GET では必ずマスク（末尾4桁）で返し、更新は **write-only**（空送信は据え置き）。疎通テストは `POST /diagnostics/jquants-test` を流用する。

| メソッド | パス | 説明 |
|---|---|---|
| GET | `/jquants/config` | 接続の現在値（`{api_key_masked, has_api_key, plan, configured}`） |
| PUT | `/jquants/config` | 更新（`{api_key?, plan?}`・`api_key` 空送信は据え置き・`plan`＝`free`/`light`/`standard`/`premium`） |

- **`plan`** はスロットル間隔（取得速度）を決める（`adapters/jquants.py` の `_PLAN_INTERVALS`・[ADR-008](decisions.md)）。`GET /lead-lag` の `meta.plan`・`meta.is_delayed` もこの値から導く（`free` は遅延扱い）。
- 未設定（`configured=false`）では取得バッチは `JQuantsError` で落ち runner が Discord 通知する（[ADR-018](decisions.md)）。初回は `/settings` で登録するまで動かない。

## 12. 知識カード（[ADR-062](decisions.md)）

`/cards` 管理画面から AI アドバイザーの知識カードを追加・編集・削除し、AI 審査（triage）で `status` を振り分け、人間が active 化する（active 化＝本番助言に効く＝人間が最終承認・[ADR-009](decisions.md)）。注入対象は `status='active'`。規律は CORE・一般教科書知識は LLM に任せ、ここには「非自明な知識（市場文脈・外部メモ・手法の解釈）」だけ置く。

| メソッド | パス | 説明 |
|---|---|---|
| GET | `/cards?status=` | カード一覧（`status` で絞り込み可・新しい順）。`CardOut[]` |
| GET | `/cards/{id}` | 1 件取得（404 あり）。`CardOut` |
| POST | `/cards/assist` | 本文だけ → AI が title/when_to_apply/level を生成＋審査（保存しない・[ADR-062](decisions.md) 追補）。`CardAssistIn={body, title?}` → `CardAssistOut={title, when_to_apply, level, verdict?, reason, quant_note, linked_signal_type}`（`verdict=null`＝面未設定/応答不正） |
| POST | `/cards` | 作成（`CardCreateIn`・**title 任意**＝空なら本文先頭で代替）→ `CardOut`（201・status=`draft`・保存後 best-effort で本文ベース合成テキストを即時埋め込み） |
| PUT | `/cards/{id}` | 部分更新（`CardUpdateIn`・`weight` 等。埋め込み元〔title/when_to_apply/body〕変更で再埋め込み）→ `CardOut` |
| DELETE | `/cards/{id}` | 削除（204） |
| POST | `/cards/{id}/triage` | AI 審査して `status` を振り分ける → `{triage: TriageOut|null, card: CardOut}` |
| POST | `/cards/{id}/activate` | 人間承認で active 化 → `CardOut` |

- **`CardOut`** = `{id, title, body, when_to_apply, status, level, sector17_code, theme, linked_signal_type, quant_note, always_inject(bool), weight(float・既定1.0), source, embedded_at, created_at, updated_at}`（`embedding` BLOB は返さない）。`weight` は retrieval ランク/注入順を `distance/weight` で重み付け（古い/信頼度低を下げて生かす・[ADR-062](decisions.md) 追補）。
- **`status`** = `draft`/`active`/`needs_quant`（計算未実装で実装待ち）/`to_core`（規律→CORE 誘導）/`rejected`（LLM 一般知識でカード不要）。
- **triage**: verdict が `needs_quant`/`to_core`/`rejected` はその status を反映、`active` は draft 据え置き（人間承認待ち・`linked_signal_type` だけ反映）。面未設定/応答不正は `triage=null`・status 据え置き（[ADR-018](decisions.md)）。
- **意味検索（RAG）retrieval 実装済み**（[ADR-045](decisions.md) 同型・純 retrieval）。注入は **`always_inject` のカードだけ常時**＋**チャットは最新発話で意味検索 top-K 追加**、夜AI は always_inject のみ。`level` は検索の事前フィルタタグ（注入ポリシーは決めない）。AI は **`search_cards` Tool**（min_phase=4・query/level/limit・[ADR-062](decisions.md)）で知識カードを能動的に意味検索できる。機能オフ（embedding 未設定）は全 active を注入する fallback で graceful。埋め込み元は title+when_to_apply+body の合成テキスト（when_to_apply 任意）。
