# Data Model（データモデル）

SQLite のスキーマと、J-Quants API V2 データとの対応をまとめる。
DB に触れるのは FastAPI のみ（[decisions.md ADR-005](decisions.md)）。スキーマ定義は Python 側に一元化する。

> 列定義は設計時点の案。Phase 0 で実装しながら確定させる。J-Quants V2 の実レスポンスのフィールド名は [jquants.md](jquants.md) を参照。**Phase 0 で使う `stocks`（master）・`daily_quotes`（bars/daily）の実キーは実機確認済み（2026-06）で、各テーブル節に対応表を載せた**。他テーブル（`financials` 等）は使う Phase で同様に確認する。

---

## 1. テーブル一覧

| 区分 | テーブル | 内容 | 書き込み |
|---|---|---|---|
| 生データ | `stocks` | 上場銘柄マスタ | 夜間バッチ |
| 生データ | `daily_quotes` | 日足四本値（巨大） | 夜間バッチ |
| 生データ | `financials` | 財務・決算 | 夜間バッチ |
| 生データ | `index_quotes` | 主要指数の水準（TOPIX/S&P500 等）| 夜間バッチ（Phase 2〜）|
| 自分のデータ | `portfolios` | ポートフォリオ（保有のまとまり） | 画面操作 |
| 自分のデータ | `holdings` | 保有銘柄（現在ポジション・導出値）| 画面操作 |
| 自分のデータ | `transactions` | 取引履歴（買い/売り）| 画面操作 |
| 自分のデータ | `watchlist` | 監視銘柄（**Phase 4・ドシエと同時に追加**）| 画面操作（チャット） |
| 自分のデータ | `cash` | 現金残高 | 画面操作 |
| 自分のデータ | `external_assets` | 投信等（割合文脈・軽量） | 画面操作 |
| 生データ | `funds` | 投信マスタ（非上場投信・ISIN キー・[ADR-054](decisions.md)）| 画面操作 |
| 生データ | `fund_navs` | 投信の基準価額 NAV（日次・10,000 口あたり円・[ADR-054](decisions.md)）| 夜間バッチ |
| 自分のデータ | `fund_transactions` | 投信の取引履歴（買い/売り・[ADR-054](decisions.md)）| 画面操作 |
| 自分のデータ | `fund_holdings` | 投信の現在ポジション（取引から導出・[ADR-054](decisions.md)）| 画面操作 |
| 生データ | `us_stocks` | 米株マスタ（symbol キー・GICS 業種＋財務素・提示専用・[ADR-055](decisions.md)）| 夜間バッチ |
| 生データ | `us_daily_quotes` | 米株日足四本値（全履歴・チャート用・[ADR-055](decisions.md)）| 夜間バッチ |
| 生データ | `fx_rates` | FX レート日足（JPY/USD・yfinance `JPY=X`・[ADR-057](decisions.md)）| 夜間バッチ |
| 自分のデータ | `us_transactions` | 米株の取引履歴（買い/売り・USD 約定価格＋約定時 FX・[ADR-057](decisions.md)）| 画面操作 |
| 自分のデータ | `us_holdings` | 米株の現在ポジション（取引から導出・USD/JPY 両原価・[ADR-057](decisions.md)）| 画面操作 |
| 計算結果 | `signals` | スクリーニング結果（事前計算） | 夜間バッチ |
| 計算結果 | `valuation_snapshots` | PER/PBR/時価総額/配当利回り（全銘柄1行・[ADR-031](decisions.md)）| 夜間バッチ |
| 計算結果 | `us_valuation_snapshots` | 米株 PER/PBR/ROE/利益率/各 YoY（symbol 1行・提示専用・[ADR-055](decisions.md)）| 夜間バッチ |
| 自分のデータ | `screening_filters` | 保存スクリーニング条件（[ADR-031](decisions.md)）| 画面操作 |
| AI | `policy` | 投資方針（単一・アクティブ） | チャット/承認 |
| AI | `advisor_journal` | 投資日記＋方針スナップショット | 夜の分析AI |
| AI | `proposals` | AI 提案（承認状態つき）| AI/画面操作 |
| AI | `stock_dossiers` | 個別銘柄の調査レポート（1銘柄1行・更新）| 調査パイプライン |
| AI | `news` | 統合ニュースコーパス（銘柄／セクター／市況／ユーザーを `level` 階層タグで 1 本化・要約＋URL のみ・[ADR-044](decisions.md)）| 夜間ジョブ `fetch_general_news`／`fetch_sector_news`＋調査パイプライン |
| AI | `themes` | テーマ語彙の目録（JP＋US 横断・embedding＋near_dup・[ADR-050](decisions.md)）| テーマタガー／夜間 `embed_themes` |
| AI | `stock_themes` | 銘柄×theme 台帳（market 横断・UPSERT＋last_seen prune・[ADR-050](decisions.md)）| テーマタガー／investigate オーバーレイ |
| AI | `company_descriptions` | 事業説明の実在テキスト（compact・US=longBusinessSummary／JP=EDINET 事業の内容・[ADR-050](decisions.md)/[ADR-056](decisions.md)）| 夜間バッチ |
| AI | `method_cards` | 手法カタログ/参照知識（将来予約・初期はリポジトリ管理） | （将来）|
| 記録 | `asset_snapshots` | 日次の総資産スナップショット | 夜間バッチ |
| 運用 | `fetch_meta` | 取得済みの最終営業日 | 夜間バッチ |
| 運用 | `notifications` | 送信済み通知の冪等ログ（Phase 6・必要時）| 夜間バッチ |
| 運用 | `llm_usage` | LLM コスト計上台帳（OpenRouter 実コスト・Phase 3・[ADR-028](decisions.md)）| LLM アダプタ |

> **通貨について**: `daily_quotes`/`holdings`/`cash` に通貨列は持たない（JPY 固定）。**スクリーナー（[ADR-031](decisions.md)）は市場ごとに分離**し、米株は `/us-stocks` 別ルート・別テーブル（`us_stocks`/`us_daily_quotes`/`us_valuation_snapshots`）で作る（¥と$の時価総額混在・33業種とGICS跨ぎの相対ランクが無意味になるのを避ける）。**米株テーブルも `currency` 列を持たない**——比率（PER/PBR/利回り/各 margin/YoY）と市場内ランクは通貨非依存で完結するため。Phase 7(B-2) で FX 基盤（`fx_rates`・`FxAdapter`）と米株保有管理（`us_transactions`/`us_holdings`）を追加し、**資産概要レイヤ（`asset_snapshots.us_stock_value`）でのみ JPY 合算する最小スコープ**を採用した（[ADR-057](decisions.md)・JPY 資産評価コアへの通貨波及は行わない）。

---

## 2. 生データ

### `stocks` — 上場銘柄マスタ
J-Quants V2 `/v2/equities/master` 由来。

> **V2 実キー → 内部列（実機確認 2026-06）**: `Code`→`code` / `CoName`→`company_name` / `S33`→`sector33_code` / `S17`→`sector17_code` / `Mkt`→`market_code`。`is_etf` は導出（Phase 0 はプライム株のみで 0。ETF 判別＝`Mkt` の対応は Phase 7 で）。`CoNameEn`/`S33Nm`/`S17Nm`/`MktNm`/`ScaleCat`/`Mrgn`/`ProdCat` は当面未使用。正規化は `backend/app/adapters/jquants.py` に集約。

| 列 | 型 | 説明 |
|---|---|---|
| `code` | TEXT PK | 銘柄コード |
| `company_name` | TEXT | 銘柄名 |
| `sector33_code` | TEXT | 33 業種コード |
| `sector17_code` | TEXT | S17 業種コード（J-Quants S17＝"1".."17"・ETF/REIT は "99"・[ADR-053](decisions.md)）|
| `market_code` | TEXT | 市場区分 |
| `is_etf` | INTEGER | ETF/REIT 判別フラグ |
| `updated_at` | TEXT | 取得日時 |

### `daily_quotes` — 日足四本値
J-Quants V2 `/v2/equities/bars/daily` 由来。**最大行数**。ETF も同居。

> **V2 実キー → 内部列（実機確認 2026-06）**: `Date`→`date` / `Code`→`code` / `O`→`open` / `H`→`high` / `L`→`low` / `C`→`close`（**未調整の四本値**）/ `Vo`→`volume` / `AdjC`→`adj_close`（調整後終値）。`Va`（売買代金）/ `AdjFactor` / `AdjO`-`AdjL`/`AdjVo` / `UL`/`LL`（値幅制限フラグ）は当面未使用。`O/H/L/C` の略記に注意（ネットの V1 記事は `Open/High/...`）。

| 列 | 型 | 説明 |
|---|---|---|
| `code` | TEXT | 銘柄コード（FK→stocks）|
| `date` | TEXT | 営業日 `YYYY-MM-DD` |
| `open`/`high`/`low`/`close` | REAL | 四本値 |
| `volume` | REAL | 出来高 |
| `adj_close` | REAL | 調整後終値 |

- 主キー `(code, date)`。インデックス `code`、`date`。
- ⚠️ **調整値は `adj_close` のみ保存**（`O/H/L` は未調整四本値）。指標計算は調整済み終値系列（`adj_close`）で行うため、終値ベースの指標（SMA/RSI/モメンタム等）は組めるが、**高値・安値を使う指標（ATR・ストキャスティクス等）は調整 OHLV を持たない当面は組めない**。必要になった時点で調整 OHLV（`AdjO`/`AdjH`/`AdjL`/`AdjVo`）を列追加する（将来・[jquants.md §3](jquants.md)）。

### `financials` — 財務・決算
J-Quants V2 `/v2/fins/summary` 由来。

| 列 | 型 | 説明 |
|---|---|---|
| `code` | TEXT | 銘柄コード |
| `disclosed_date` | TEXT | 開示日 |
| `fiscal_period` | TEXT | 会計期間 |
| `net_sales`/`operating_profit`/`profit` | REAL | 売上/営業利益/純利益（Sales/OP/NP）|
| `eps`/`bps` | REAL | EPS/BPS（**BPS は通期FY行のみ・四半期EPSは累計**）|
| `dividend_per_share` | REAL | 年間配当（予想 `FDivAnn` 優先・実績 `DivAnn` 代替・ADR-031）|
| `shares_outstanding`/`treasury_shares` | REAL | 期末発行済株式数 `ShOutFY` / 自己株式 `TrShFY`（時価総額の素・ADR-031）|

- 主キー `(code, disclosed_date, fiscal_period)`。実フィールドは実機確認済み（2026-06・[jquants.md §6](jquants.md)）。
- 配当/株数は **スクリーナー（ADR-031）のバリュエーション導出**用に 0007_screening で追加。`fetch_financials` は全銘柄を by-date 一括取得する。

### `index_quotes` — 主要指数の水準（Phase 2〜）
`IndexAdapter` で取得（TOPIX / S&P500 等）。Advisor のマクロ文脈用。`daily_quotes` とは**別テーブル**にする（個別銘柄とは粒度・出所が違うため）。Phase 2 着手前にこの形で確定。

| 列 | 型 | 説明 |
|---|---|---|
| `symbol` | TEXT | 指数シンボル（例 `TOPIX` / `^GSPC`）|
| `date` | TEXT | 営業日 |
| `close` | REAL | 終値（水準）|

- 主キー `(symbol, date)`。

> **米国個別株**は後期（Phase 7）に `UsEquityAdapter` で取得。`daily_quotes` に `source`/通貨列を足すか別テーブルにするかは Phase 7 着手時に決める（[roadmap.md Phase 7](roadmap.md)）。

---

## 3. 自分のデータ

### `portfolios`
| 列 | 型 | 説明 |
|---|---|---|
| `portfolio_id` | INTEGER PK | 複数持てる器 |
| `name` | TEXT | 名前 |
| `created_at` | TEXT | 作成日時 |

### `holdings` — 現在ポジション
**`transactions` から導出される現在値**（買い増し/一部売却で `shares`・`avg_cost` が変わる）。`holdings` を直接編集せず、取引を `transactions` に記録して再計算するのが原則（[ADR-019](decisions.md)）。

| 列 | 型 | 説明 |
|---|---|---|
| `id` | INTEGER PK | |
| `portfolio_id` | INTEGER | FK→portfolios |
| `code` | TEXT | FK→stocks |
| `shares` | REAL | 保有株数（取引から導出）|
| `avg_cost` | REAL | 平均取得単価（取引から導出）|

> **現在値（評価額）の参照方法**: 各銘柄の「今の株価」は `daily_quotes` の最新行（`MAX(date)`）から引く。⚠️ **Free プランは 12 週間遅延**なので、評価額・P/L・現金比率はすべて**約 3 か月前の値**になる。評価額系を表示する箇所では遅延である旨を明示する（[api.md](api.md)）。Light 以上で最新値になる。

### `transactions` — 取引履歴
買い/売りを記録し、ここから `holdings`（保有株数・平均取得単価）と `asset_snapshots.pnl` の原価を導出する。「提示のみ・手動発注」でも、約定後にユーザーが記録する。

| 列 | 型 | 説明 |
|---|---|---|
| `id` | INTEGER PK | |
| `portfolio_id` | INTEGER | FK→portfolios |
| `code` | TEXT | FK→stocks |
| `side` | TEXT | `buy` / `sell` |
| `shares` | REAL | 株数 |
| `price` | REAL | 約定単価 |
| `fee` | REAL | 手数料（任意）|
| `traded_at` | TEXT | 約定日 |

### `watchlist`
| 列 | 型 | 説明 |
|---|---|---|
| `id` | INTEGER PK | |
| `code` | TEXT | FK→stocks |
| `note` | TEXT | メモ |
| `interval_days` | INTEGER | 銘柄ごとの調査間隔（既定 21・stale 起点・夜間巡回の cadence＝[decisions.md ADR-033](decisions.md)）|
| `added_at` | TEXT | 追加日時 |

### `cash` — 現金残高
| 列 | 型 | 説明 |
|---|---|---|
| `id` | INTEGER PK | |
| `balance` | REAL | 投資用待機現金 |
| `updated_at` | TEXT | 更新日時 |

### `external_assets` — 投信等（軽量・割合文脈）
深追いしない。「全体に対する割合」を AI が把握するための軽い記録（[decisions.md ADR-010](decisions.md)）。

| 列 | 型 | 説明 |
|---|---|---|
| `id` | INTEGER PK | |
| `name` | TEXT | 例「オルカン」「楽天ゴールド」 |
| `category` | TEXT | 投信/コモディティ等 |
| `value` | REAL | 評価額（手入力・随時更新） |
| `proxy_symbol` | TEXT | 概算用 proxy（例 全世界株指数 / 金価格） |
| `monthly_contribution` | REAL | 毎月積立額（任意） |
| `as_of` | TEXT | 基準日 |

### `funds` — 投信マスタ（非上場投信・[ADR-054](decisions.md)）
NAV を日次取得し含み損益を随時計算したい非上場の公募投信（eMAXIS Slim 全世界株式・楽天ゴールド等）。識別子は **ISIN**（NAV 取得が ISIN 必須）、協会コードは表示用の任意列。`external_assets`（軽量・割合文脈）とは**別系統**（[ADR-010](decisions.md) の「深追いしない」を [ADR-054](decisions.md) で上書き）。

| 列 | 型 | 説明 |
|---|---|---|
| `isin` | TEXT PK | ISIN（NAV 取得キー）|
| `name` | TEXT NOT NULL | ファンド名 |
| `assoc_code` | TEXT | 協会コード（表示用・任意）|
| `updated_at` | TEXT | 更新日時 |

### `fund_navs` — 基準価額 NAV（日次・[ADR-054](decisions.md)）
投資信託総合検索ライブラリー（ウエルスアドバイザー運営）の CSV（ISIN 指定・遅延なし実値）由来。価格は**「10,000 口あたりの円」**。評価額 = `units/10000*nav`、含み損益 = `units/10000*(nav-avg_cost)`。

| 列 | 型 | 説明 |
|---|---|---|
| `isin` | TEXT | FK→funds |
| `date` | TEXT | 基準日 |
| `nav` | REAL | 基準価額（10,000 口あたり円）|

複合 PK `(isin, date)`・index `(isin)`・`(date)`。再取得で壊れないよう冪等 UPSERT（[ADR-002](decisions.md)）。

### `fund_transactions` — 投信の取引履歴（[ADR-054](decisions.md)）
買い/売りを記録し、ここから `fund_holdings`（口数・移動平均取得単価）を導出する（株の `transactions`→`holdings` と同型＝[ADR-019](decisions.md)）。積立分も当面は buy として手入力（自動生成は将来課題＝[ADR-054](decisions.md)）。

| 列 | 型 | 説明 |
|---|---|---|
| `id` | INTEGER PK | |
| `portfolio_id` | INTEGER | FK→portfolios |
| `isin` | TEXT | FK→funds |
| `side` | TEXT | `buy` / `sell` |
| `units` | REAL | 口数 |
| `price` | REAL | 約定基準価額（10,000 口あたり円）|
| `fee` | REAL | 手数料（任意）|
| `traded_at` | TEXT | 約定日 |

index `(portfolio_id)`・`(isin)`。

### `fund_holdings` — 投信の現在ポジション（[ADR-054](decisions.md)）
**`fund_transactions` から導出される現在値**（買い増し/一部売却で `units`・`avg_cost` が変わる）。直接編集せず取引を記録して再計算する（[ADR-019](decisions.md)）。取引 mutation 後に atomic に再計算する。

| 列 | 型 | 説明 |
|---|---|---|
| `id` | INTEGER PK | |
| `portfolio_id` | INTEGER | FK→portfolios |
| `isin` | TEXT | FK→funds |
| `units` | REAL | 保有口数（取引から導出）|
| `avg_cost` | REAL | 平均取得単価（10,000 口あたり円・取引から導出）|

UNIQUE `(portfolio_id, isin)`。

---

## 4. 計算結果

### `signals` — スクリーニング結果（事前計算）
夜間バッチが計算して焼く。朝の通知・一覧・チャットはここを読むだけで即応。

| 列 | 型 | 説明 |
|---|---|---|
| `id` | INTEGER PK | |
| `date` | TEXT | 算出日 |
| `code` | TEXT | 銘柄/業種コード |
| `signal_type` | TEXT | `momentum`/`volume_spike`/`ai_alpha`/`lead_lag` 等 |
| `score` | REAL | スコア・強度 |
| `payload` | TEXT | 補足（JSON。指標値・根拠） |

- インデックス `(date, signal_type)`。
- **UNIQUE 制約 `(date, code, signal_type)`**: 夜間バッチの再計算が冪等 UPSERT（`on_conflict_do_update`）で壊れないための土台（[ADR-002](decisions.md)）。PK は `id` のみのため、論理的な一意性はこの複合 UNIQUE で担保する（`0003_signals`・定義レーン=quant）。
- **`score` は連続値（0..1）の「材料」**（[ADR-026](decisions.md)）: signals は **AI Advisor に渡す材料**で、閾値は破壊的ゲートにせず `payload.notable` フラグ＋読み取り時カットオフに留める。夜間バッチは**低フロア以上を広めに保存**して near-miss を残し、絞り込みは AI（`screen_stocks`）と一覧 UI が行う。個別銘柄の素の指標（SMA/RSI 等）は保存せず `get_indicators(code)` で都度計算する（初期・[ADR-016](decisions.md) のコード手法を共有）。手法パラメータの管理は [ADR-027](decisions.md)。

### `valuation_snapshots` — バリュエーション・スナップショット（[ADR-031](decisions.md)）
夜間ジョブ `calc_valuation` が**全銘柄 1 行**を焼く。`/stocks/screen`（スクリーナー）が読み取り、業種内パーセンタイル・時価総額順位は読み取り時に window 関数で算出（事前フィルタしない）。値は前夜終値ベース。

| 列 | 型 | 説明 |
|---|---|---|
| `code` | TEXT PK | 銘柄コード（最新 1 行のみ保持） |
| `as_of_date` | TEXT | 採用株価の営業日 |
| `close`/`eps`/`bps`/`dividend_per_share`/`shares_net` | REAL | 採用した素データ（根拠） |
| `per`/`pbr`/`market_cap`/`dividend_yield` | REAL | 派生比率（PER=close/eps・PBR=close/bps・時価総額=close×shares_net・利回り=dps/close）|
| `fin_disclosed_date` | TEXT | 採用財務の開示日（監査） |

- 採用規律: PER/PBR は最新FY行の実績 EPS/BPS、配当/株数は最新開示行（`services/valuation.py`）。指標は計算不能なら NULL（[ADR-014](decisions.md) 捏造しない）。
- **日本株専用**。米株は別スナップショット `us_valuation_snapshots`・`/us-stocks`（通貨/GICS の境界・Phase 7(B-1) 実装済み・[ADR-055](decisions.md)）。

### `screening_filters` — 保存スクリーニング条件（[ADR-031](decisions.md)）
| 列 | 型 | 説明 |
|---|---|---|
| `id` | INTEGER PK | |
| `name` | TEXT | フィルタ名 |
| `criteria_json` | TEXT | 条件まるごと（JSON・前方互換の緩い形）|
| `created_at`/`updated_at` | TEXT | ISO8601 |

- 単一ユーザーなので `user_id` を持たない（[ADR-001](decisions.md)）。CRUD は `/screening-filters`。

### 米国株（提示専用・Phase 7(B-1)・[ADR-055](decisions.md)）

日本株コア（`stocks`/`daily_quotes`/`valuation_snapshots`）と**物理的に別テーブル**（[ADR-031](decisions.md) 市場分離・migration `0017_us_equity`）。JPY 単一前提の資産評価コア（holdings/cash/asset_snapshots/portfolio metrics/`/optimize`）には触れず提示専用に閉じる。**`currency` 列は持たない**（比率/ランクは通貨非依存で完結）。データ源は yfinance 一本（`UsEquityAdapter`・[ADR-039](decisions.md)/[ADR-055](decisions.md)）。

#### `us_stocks` — 米株マスタ（`stocks` 相当）
ユニバースは NASDAQ Trader directory 由来（普通株のみ巡回・ETF は `is_etf=1` でフラグ保持）。財務素は yfinance `.info` を低頻度ローテ巡回（[ADR-033](decisions.md) 同型）で焼く。業種/名称はここに持つ（米株は `stocks` に存在しないため JOIN で補えない）。

| 列 | 型 | 説明 |
|---|---|---|
| `symbol` | TEXT PK | ティッカー（例 `AAPL`・NASDAQ Trader/yfinance）|
| `company_name` | TEXT | 銘柄名 |
| `gics_sector` | TEXT | Yahoo `.info.sector`（GICS 相当 11 分類の英語ラベル・厳密 GICS コードは追わない）|
| `industry` | TEXT | Yahoo `.info.industry`（補助・細分類）|
| `is_etf` | INTEGER | ETF 判別フラグ（0/1）|
| `eps`/`bps`/`shares_net`/`dividend_per_share`/`net_sales`/`operating_profit`/`profit` | REAL | 財務素（読み取り時 Python 計算用）。`operating_profit` は `operatingMargins × totalRevenue` の**近似**|
| `revenue_growth_yoy`/`earnings_growth_yoy` | REAL | `.info` 提供の YoY 率の中継（売上/純利益・実値）|
| `fin_disclosed_date` | TEXT | 採用財務の基準日（`.info` 由来は現状 None）|
| `updated_at` | TEXT | 取得日時 ISO8601 |

#### `us_daily_quotes` — 米株日足四本値（`daily_quotes` 相当・全履歴）
(symbol, date) 複合 PK・UPSERT で冪等（[ADR-002](decisions.md)）。FK は張らない（生データ流儀・`daily_quotes`/`index_quotes` 同方針）。

| 列 | 型 | 説明 |
|---|---|---|
| `symbol`/`date` | TEXT | 複合 PK（営業日 `YYYY-MM-DD`）|
| `open`/`high`/`low`/`close`/`volume` | REAL | 素の OHLCV（`auto_adjust=False`）|
| `adj_close` | REAL | 調整後終値（配当・分割調整）|

#### `us_valuation_snapshots` — 米株バリュエーション（`valuation_snapshots` 相当・symbol 1行）
夜間 `calc_us_valuation` が焼く。`/us-stocks/screen` が読み取り、業種内パーセンタイル・時価総額順位は読み取り時に window 関数で算出（[ADR-014](decisions.md)/[ADR-026](decisions.md)）。

| 列 | 型 | 説明 |
|---|---|---|
| `symbol` | TEXT PK | FK→`us_stocks.symbol`（自分データ＝マスタ済みのみ焼く）|
| `as_of_date` | TEXT | 採用株価の営業日 |
| `close`/`eps`/`bps`/`dividend_per_share`/`shares_net` | REAL | 採用した素データ（根拠）|
| `per`/`pbr`/`market_cap`/`dividend_yield`/`roe`/`operating_margin`/`net_margin` | REAL | 派生比率（PER=close/eps・ROE=eps/bps・利回り・各 margin 等・0..1 基準）|
| `revenue_growth_yoy`/`op_growth_yoy`/`profit_growth_yoy`/`eps_growth_yoy` | REAL | YoY 成長率。**`op_growth_yoy`/`eps_growth_yoy` は `.info` に素が無く None**（捏造しない）|
| `fin_disclosed_date` | TEXT | 採用財務の基準日（監査）|
| `updated_at` | TEXT | この行を焼いた時刻 ISO8601 |

- **指標は計算不能なら NULL**（[ADR-014](decisions.md) 捏造しない）。市場内ランク（`gics_sector_pctile`・`market_cap_rank`）は読み取り時の window 算出で、この表には保存しない。

### FX・米株保有（Phase 7(B-2)・[ADR-057](decisions.md)）

米株保有を JPY 資産概要に合算する最小スコープ。FX 基盤（`fx_rates`・`FxAdapter`）と米株保有管理（`us_transactions`/`us_holdings`）を追加し、**資産概要レイヤ（`asset_snapshots.us_stock_value`）でのみ合算**する（JPY 資産評価コアへの通貨波及は行わない）。migration `0019_us_holdings_fx`。

#### `fx_rates` — FX レート日足（JPY/USD）
yfinance `JPY=X` 日足終値を `FxAdapter`（`adapters/fx.py`・`UsEquityAdapter` と同型のフォールバック連鎖）経由で取得。`fetch_meta['fx:USDJPY']` カーソルで差分管理。夜間ジョブ `fetch_fx_rates` が `snapshot_assets` の直前に実行される（当夜 FX を当日スナップショットに確実に反映するため）。

| 列 | 型 | 説明 |
|---|---|---|
| `date` | TEXT | 営業日（YYYY-MM-DD）|
| `pair` | TEXT | 通貨ペア（例 `'USDJPY'`）|
| `rate` | REAL | JPY/USD レート（1 USD あたりの円）|

複合 PK `(date, pair)`。UPSERT で冪等。

#### `us_transactions` — 米株の取引履歴（`transactions` の米株版）
**取引が一次データ**（[ADR-019](decisions.md)）。`us_holdings` はここから `recalc_us_holdings` で導出する。mutation 後に atomic 再計算。**[ADR-001](decisions.md) の単一ユーザー前提ゆえ `portfolio_id` を持たない**（グローバル保有）。

| 列 | 型 | 説明 |
|---|---|---|
| `id` | INTEGER PK | |
| `symbol` | TEXT | FK→`us_stocks.symbol` |
| `side` | TEXT | `'buy'` / `'sell'` |
| `shares` | REAL | 株数 |
| `price` | REAL | 約定単価（USD）|
| `fee` | REAL | 手数料（USD・任意）|
| `traded_at` | TEXT | 約定日（YYYY-MM-DD）|
| `fx_rate` | REAL | 約定時の USDJPY レート（`avg_cost_jpy` 計算の根拠）|
| `note` | TEXT | メモ（任意）|

インデックス `symbol`（保有再計算の起点）。

#### `us_holdings` — 米株の現在ポジション（`holdings` の米株版）
`us_transactions` の射影（[ADR-019](decisions.md)）。`recalc_us_holdings(conn, symbol)` が symbol 単位で再計算する。共有純関数 `recompute_positions` を `price`（USD）と `price_jpy=price×fx_rate`（JPY 換算）の 2 引数で呼び、USD/JPY 両原価を得る（[ADR-014](decisions.md)/[ADR-016](decisions.md)）。

| 列 | 型 | 説明 |
|---|---|---|
| `symbol` | TEXT PK | FK→`us_stocks.symbol`（1 銘柄 1 行）|
| `shares` | REAL | 現在の保有株数 |
| `avg_cost` | REAL | 移動平均取得単価（USD）|
| `avg_cost_jpy` | REAL | 移動平均取得単価（JPY 固定・約定時 FX で換算済み）|

評価額 `= shares × latest_close_usd × fx_rate`（現レート）、含み損益 `= (latest_close_usd × fx_rate − avg_cost_jpy) × shares`。**現レートを使うため為替損益が含み損益に乗る**（[ADR-057](decisions.md)）。純関数 `value_us_holdings(holdings_rows, latest_closes_usd, fx_rate)` が計算する。

---

## 5. AI Advisor の状態

### `policy` — 投資方針（単一・アクティブ）
[decisions.md ADR-013](decisions.md)。版管理機構は作らず、変更履歴は `advisor_journal` のスナップショットに残す。常に 1 行（または `is_active=1` の 1 行）。

| 列 | 型 | 説明 |
|---|---|---|
| `id` | INTEGER PK | |
| `risk_tolerance` | TEXT | 低/中/高 |
| `time_horizon` | TEXT | 短/中/長 |
| `target_cash_ratio` | REAL | 現金比率（最適化制約）|
| `max_position_weight` | REAL | 1 銘柄上限（最適化制約）|
| `sector_caps` | TEXT | 業種上限 JSON（最適化制約）|
| `target_return` | REAL | 目標リターン（任意）|
| `no_leverage` | INTEGER | 信用・レバ不可（ゼロカット解釈）|
| `exclusions` | TEXT | 除外リスト JSON |
| `rationale` | TEXT | 自由文の理念・機微 |
| `updated_at` | TEXT | 更新日時 |

### `advisor_journal` — 投資日記
夜の分析AI が毎晩 1 件書く。方針スナップショットを内包し、これが履歴になる。**昼チャット（軸2）からも「会話を要約して残す」昇格トリガーでエントリを書ける**（[ADR-029](decisions.md)）。

| 列 | 型 | 説明 |
|---|---|---|
| `id` | INTEGER PK | |
| `date` | TEXT | 日付 |
| `source` | TEXT | エントリの出所 `'nightly'`（夜の分析AI）/ `'chat'`（昼チャットからの要約昇格）（[ADR-029](decisions.md)）。既定 `'nightly'` |
| `situation_briefing` | TEXT | その日 AI に渡した構造化事実（JSON。`source='chat'` の昼要約では null 可）|
| `observations` | TEXT | AI の所見（自由文。昼要約ではここに会話の要約が入る）|
| `proposal` | TEXT | 当日の提案（銘柄・比率・方針変更案）|
| `proposed_policy_change` | TEXT | 方針変更の提案 JSON（承認待ち・任意）|
| `policy_snapshot` | TEXT | その時点の `policy` まるごと（JSON）＝履歴 |
| `llm_model` | TEXT | 使用モデル（監査用）|
| `created_at` | TEXT | 生成日時 |

> チャットAI（軸2）で方針を変えた場合も、その日の journal に snapshot と変更理由を残す。昼チャットの要約昇格は**ユーザー承認後のみ書く**（黙って自動保存しない＝[ADR-014](decisions.md)/[ADR-029](decisions.md)）。**投資行動の数字は `transactions` が正**で、journal 要約はそれを物語として参照するだけ（[ADR-019](decisions.md)）。生の会話スクロールバックは frontend の localStorage に置き DB 保存しない（[ADR-029](decisions.md)）。

### `proposals` — AI 提案（承認状態つき）
夜の分析AI・チャットAI が出す提案（方針変更／銘柄・比率）を独立して持ち、**承認状態を消し込めるようにする**。後から「提案を採ったか／結果どうだったか」を振り返り、提案精度の検証にも使う。

| 列 | 型 | 説明 |
|---|---|---|
| `id` | INTEGER PK | |
| `created_date` | TEXT | 提案日 |
| `kind` | TEXT | `policy_change` / `buy` / `sell` / `rebalance` |
| `body` | TEXT | 提案内容（JSON・`kind` 依存）。`policy_change`=`{field, to, from?, reason?}`／`buy`・`sell`=`{code, company_name, market}`（ニュース起点の売買アイデア・[ADR-052](decisions.md)。**株数・金額などの数値は持たない**＝[ADR-014](decisions.md)）|
| `rationale` | TEXT | 根拠（AI の説明）|
| `status` | TEXT | `pending` / `approved` / `rejected` |
| `outcome` | TEXT | 採否後の結果メモ（任意・振り返り用）|
| `resolved_at` | TEXT | 承認/却下した日時 |
| `journal_id` | INTEGER | 関連する `advisor_journal`（任意）|
| `depends_on` | INTEGER NULL | 先に承認が要る提案（FK→`proposals.id`・任意）|

- インデックス `status`（未処理の提案を拾う）。
- `depends_on` は提案間の承認順制御に使う（例: `policy_change` を承認してから `buy` を承認）。Dashboard が「依存」を表現する（[screens.md §3](screens.md)・`_arbitration.md` 決定4）。`0006_advisor_state` の DDL に含める。
- **`buy`/`sell` の起票（[ADR-052](decisions.md)）**: Advisor の `propose_trade` Tool から承認制で起票する（migration なし＝既存列で表現）。`body.code` は `stocks`→`us_stocks` で解決済み（未知コードは起票しない）。同一 `(kind, code)` の `pending` は重複起票しない（`reject`/`approve` 済みは再提案可）。`depends_on` は現状 None（自動リンクなし）。承認しても約定しない＝`status` 遷移のみ（提示専用・[ADR-009](decisions.md)）。

### `llm_usage` — LLM コスト計上台帳（[ADR-028](decisions.md)）
LLM アダプタの呼び出しラッパが per-call で積む。**当月累計 `cost_usd`** をコストガードレール（$50・3 値トグル）の判定に使う。OpenRouter は実コスト（`usage.cost`）を返すので**単価表は持たない**。Ollama（ローカル）は cost 無し → 0 計上。`0006_advisor_state` の DDL に含める。

| 列 | 型 | 説明 |
|---|---|---|
| `id` | INTEGER PK | |
| `created_at` | TEXT | ISO8601（当月集計の起点）|
| `source` | TEXT | 呼び出し文脈 `'nightly'`/`'chat'`/`'dossier'` 等 |
| `model` | TEXT | 使用モデル |
| `tokens_in` / `tokens_out` | INTEGER | トークン数（監査・任意）|
| `cost_usd` | REAL | OpenRouter `usage.cost`。Ollama は 0 |

- インデックス `created_at`（当月分の集計）。

### `stock_dossiers` — 個別銘柄の調査レポート（1銘柄1行）
`investigate_stock(code)` が生成・更新する living document（[ADR-020](decisions.md)）。watchlist 一覧で「最終調査日」を表示し、「そろそろ調査」を促す。

| 列 | 型 | 説明 |
|---|---|---|
| `code` | TEXT PK | 銘柄（FK→stocks）。1 銘柄 1 行 |
| `summary_md` | TEXT | AI 生成の調査要約（**markdown**）。UI でそのまま描画 |
| `key_facts` | TEXT | 構造化した要点 JSON（PER/成長率/直近トピック等）|
| `last_investigated_at` | TEXT | 最終調査日時（watchlist 一覧の「最終調査日」）|
| `updated_at` | TEXT | 更新日時 |

> **ソース台帳の統合**: 旧 `dossier_sources`（ドシエのソース台帳）は [ADR-044](decisions.md) で統合コーパス `news`（`level='stock'`・`code` 付き）に吸収された。`stock_dossiers` 本体（living markdown）はそのまま別資産で、`investigate_stock` は「`news` を材料に `stock_dossiers` を更新する」関係を維持する。

### `news` — 統合ニュースコーパス（銘柄／セクター／市況／ユーザー・[ADR-044](decisions.md)）
旧 `dossier_sources`（[ADR-020](decisions.md)・`code` FK 必須）と旧 `general_news`（[ADR-034](decisions.md)・`category` 列）を **1 本に発展置換**したコーパス（`0013_news_corpus`・`down_revision=0012`）。記事ごとに **`level` 階層タグ**を持たせ、住所を「保存先」ではなく「対象」で割る。**記事全文は持たず要約＋URL だけ**残す方針は堅持（[ADR-020](decisions.md)）。1 記事 1 レベル（同一 URL は先勝ち）。

| 列 | 型 | 説明 |
|---|---|---|
| `id` | INTEGER PK | |
| `level` | TEXT | 階層タグ `'stock'`／`'sector'`／`'market'`／`'user'`（**必須**）|
| `code` | TEXT | 銘柄（FK→stocks）＝`stock` 層の紐付け（他層は NULL）|
| `sector17_code` | TEXT | `sector` 層の J-Quants S17 業種コード "1".."17"（`stocks.sector17_code` と同体系・変換なしで直接一致＝[ADR-053](decisions.md)。他層は NULL）|
| `category` | TEXT | 表示ラベル（市況／マクロ／世界情勢 等）＝`market` 層（他層は NULL）|
| `source` | TEXT | `news` / `user` / `disclosure` / `twitter` 等（旧 `source_type` を改名）|
| `url` | TEXT | ソース URL（**UNIQUE**＝重複防止）|
| `title` | TEXT | 見出し（任意）|
| `summary` | TEXT | 短い要約（本文は保存しない）|
| `published_at` | TEXT | 発行日 |
| `fetched_at` | TEXT | 取り込んだ日時 |
| `extraction_status` | TEXT | 取得レベル `summarized`/`description`/`headline` |
| `embedding` | BLOB | 意味検索用ベクトル（[ADR-045](decisions.md)・段階A）。`sqlite-vec` の `vec_distance_cosine` が読む **float32 little-endian** 形式・**null 可**（未埋め込み/機能オフ）。**`summary` のみ**を埋め込む（本文は持たない＝[ADR-020](decisions.md)）|
| `embed_model` | TEXT | 埋め込みに使ったモデル名（差替検知用）。現行 `embed_model` と不一致の行は `embed_news` ジョブが再埋め込み対象にする |
| `embedded_at` | TEXT | 埋め込み時刻（ISO8601 UTC）|

- インデックス: `url`（UNIQUE）、`level`、`code`、`sector17_code`。`url` UNIQUE ＋ `on_conflict_do_nothing` で冪等。
- **意味検索（[ADR-045](decisions.md) 段階A・`0016_news_embedding`・`down_revision=0015`）**: 上記 3 列を追加（すべて nullable・未埋め込み/機能オフでも既存運用を壊さない）。検索は `vec_distance_cosine` で `embedding` BLOB を**直接スキャン**＝**vec0 仮想テーブルは使わず次元非依存**。生成は夜間ジョブ `embed_news`（null/モデル不一致行を一括）＋貼付 `ingest_user_news` の best-effort 即時埋め込み。embedding 設定（OpenAI 互換 1 本＝[ADR-012](decisions.md)）未設定時は機能オフ。**【明示 TODO】規模が育ったら vec0 仮想テーブルへ昇格**（`embedding` 列はそのまま活きる・発火条件の叩き台＝概ね 5 万行 or 検索レイテンシ >200ms）。段階C（FTS5 ハイブリッド）は将来。
- **3 層の使い分け**: `level='stock'`（銘柄自身・旧 `dossier_sources`）／`level='sector'`（その銘柄の TOPIX-17 業種）／`level='market'`（市況・マクロ・旧 `general_news`）／`level='user'`（ユーザー入力＝[ADR-046](decisions.md)・schema 上許容）。`get_news_context(code)` がこの 3 層（銘柄／セクター／市況）を**必ず構造的に揃えて**返す。
- **`user` 層（ユーザー投入・[ADR-046](decisions.md)）**: 貼付テキストを要約して `source='user'` で投入する（`ingest_user_news`）。**タグ v1 はユーザー明示**＝銘柄コードありで `level='stock'`＋`code`、無しで `level='market'`＋`category="ユーザー投入"`（`GET /general-news` にも出る）。`level='user'` 値は schema 上許容だが**本実装は未使用**。**URL 未入力時は合成キー `user://`＋`sha256(text)` 先頭 16 桁**を `url`（NOT NULL UNIQUE）に詰め、`on_conflict_do_nothing` で冪等化する。削除は `source='user'` のみ可（`delete_user_news`・自動取得分は 404）。**migration なし**（既存 `news` 列で表現）。
- **取り込みジョブ**: ① `fetch_general_news`（`run_advisor` 直前）が市況ニュースを `level='market'` に UPSERT。② 新ジョブ `fetch_sector_news`（`fetch_general_news` の直後・`run_advisor` の前）が TOPIX-17 全業種を毎晩取得し `level='sector'`／`sector17_code` に UPSERT。**タグ付けは `stocks` と同じ J-Quants S17（"1".."17"）でそろえる**（`build_news_context` の等値 JOIN が直接一致するため＝[ADR-053](decisions.md)）。③ 調査パイプライン（`investigate_stock`／`fetch_news`）が銘柄ニュースを `level='stock'`／`code` に取り込む。いずれも**要約前 dedup**（既存 URL は本文取得・要約をスキップ）で冪等・省コスト。
- **定数モジュール**: カテゴリ／セクターの検索クエリ・件数上限・lookback は `app/adapters/general_news_config.py`（`SECTOR_NEWS_QUERIES`／`SECTOR_NEWS_MAX_PER_SECTOR=3`／`SECTOR_NEWS_LOOKBACK_DAYS=3` 等・env 化しない＝[ADR-034](decisions.md)）。
- **消費先**: `GET /general-news`（Dashboard widget）・`GET /news`（`/news` 画面の一覧＝[ADR-047](decisions.md)）・Tool `get_general_news`（市況のみ）・新 Tool `get_news_context`（3 層構造）・`investigate_stock`／`GET /dossiers/{code}`（銘柄層）。既存 API のレスポンス形は不変（`source` を `source_type` にマップ）。
- 適時開示（TDnet）は有料アドオンのため後付け。`source='disclosure'` でこのコーパスに入れる（構造が複雑になれば専用テーブルに分離）。

### テーマタグ — `themes`／`stock_themes`／`company_descriptions`（[ADR-050](decisions.md) 改訂・[ADR-056](decisions.md)）
業種コードをまたぐ **テーマ**（"AI需要"・"防衛"・"円安メリット" 等）で銘柄を束ねる。**全ユニバース（JP＋US）を実在テキストに grounded で事前タグ付け**する（名前推測禁止・[ADR-050](decisions.md)）。テーマは**定性タグで数値でない**（[ADR-014](decisions.md)）。**段階 A（米株）は実装済み（migration `0018_themes`・2026-06-10）**＝US は `fetch_us_fundamentals` 相乗りで `longBusinessSummary` を取り込み、夜間 `tag_us_themes`／`embed_themes` がタグ付け・語彙 reconcile する。**段階 B（JP 調査済み）も実装済み（2026-06-11）**＝`investigate_stock` がドシエ要約を `company_descriptions(JP, source='dossier')` に焼き、夜間 `tag_jp_themes` がタグ付け（説明未変化は LLM を呼ばず last_seen_at だけ bump）。**段階 C（EDINET 全ユニバース）も実装済み（2026-06-11）**＝`EdinetAdapter` が有報「事業の内容」を提出日クロールで取得・要約して `company_descriptions(JP, source='edinet')` に焼き（dossier 行は上書きしない）、既存 `tag_jp_themes` が `source` 不問で拾う。migration 不要（既存列を流用・[ADR-056](decisions.md)）。

**`themes`** — テーマ語彙の目録（JP＋US 横断のグローバル語彙・"AI需要" は市場を跨いで 1 語）。

| 列 | 型 | 説明 |
|---|---|---|
| `name` | TEXT PK | canonical なテーマ名 |
| `embedding` | BLOB | 語彙 reconcile 用ベクトル（[ADR-045](decisions.md) の `vec_distance_cosine` 流用・float32 LE・null 可）|
| `embed_model` | TEXT | 埋め込みモデル名（差替検知）|
| `first_seen_at` | TEXT | 初出日時 |
| `near_duplicate_of` | TEXT | 近接した既存テーマ名（重複候補フラグ・**自動マージはせず候補提示のみ**・null 可）|

- 語彙は**単調増加で消さない**（reconcile の資産）。種テーマ 30〜50 個を `app/reference/` に置き初回投入（[ADR-053](decisions.md) 参照知識層）。embedding／near_dup は夜間 `embed_themes`（`embed_news` 同型）が付ける。`embedding_enabled()` オフ時は embedding=NULL で degrade。

**`stock_themes`** — 銘柄×theme 台帳（JP＋US 横断）。

| 列 | 型 | 説明 |
|---|---|---|
| `id` | INTEGER PK | |
| `market` | TEXT | `'JP'`／`'US'` |
| `code` | TEXT | JP 5桁コード or US symbol（**cross-FK は張らない**＝`signals` と同じ生データ流儀・US は別テーブル）|
| `theme_name` | TEXT | `themes.name`（canonical 名のみ）|
| `first_assigned_at` | TEXT | 初付与日時 |
| `last_seen_at` | TEXT | 最終再確認日時（time-window prune の基準）|

- **UNIQUE `(market, code, theme_name)`**。**`source` 列は持たない**。書き込みは **UPSERT＋`last_seen_at` bump（削除しない）**、古いタグは**時間窓 prune**（一定期間どの再タグにも再確認されなかった行だけ枯らす）。読み取りは theme_name で union。なお JP は `company_descriptions` を1銘柄1テキスト（`UNIQUE(market,code)`）で共有し **dossier 優先**にしたため、実運用の書き手は market ごとに1系統（US=`tag_us_themes`／JP=`tag_jp_themes`）で、prune は「②説明テキストが変わって確認されなくなったタグの時間窓減衰」として効く（2書き手共存の reframe＝[ADR-050](decisions.md) 実装メモ・段階B）。
- インデックス: `(market, code)`（銘柄のテーマ一覧）・`theme_name`（テーマ株スクリーニング）。

**`company_descriptions`** — 事業説明の実在テキスト（市場横断・grounded タガーの信号源）。

| 列 | 型 | 説明 |
|---|---|---|
| `id` | INTEGER PK | |
| `market` | TEXT | `'JP'`／`'US'` |
| `code` | TEXT | JP 5桁 or US symbol（cross-FK なし）|
| `source` | TEXT | `'dossier'`（JP 調査済み＝investigate_stock のドシエ要約・段階B）／`'edinet'`（JP 未調査＝有報「事業の内容」・段階C）／`'yfinance'`（US `longBusinessSummary`）。**`UNIQUE(market,code)` で1銘柄1テキスト**＝JP は調査済みが dossier 優先（段階C は dossier 行があれば edinet で上書きしない・[ADR-050](decisions.md) 実装メモ）|
| `description_text` | TEXT | **compact プロフィール**（JP 調査済みは `summary_md` そのまま／JP 未調査は EDINET 事業の内容を要約・US は longBusinessSummary を素のまま・本文は持たず＝[ADR-020](decisions.md)）|
| `disclosed_date` | TEXT | テキストの基準日（EDINET 有報の提出/開示日・dossier/yfinance は null）|
| `doc_id` | TEXT | EDINET 書類管理番号（provenance・dossier/US は null）|
| `fetched_at` | TEXT | テキスト最終変化時刻（同一テキスト再 UPSERT では据え置き＝差分タガーが「説明変化した銘柄」を拾う判定材料）|

- `source`/`doc_id`/`disclosed_date` は**テキストの provenance**（タグの provenance ではない＝`stock_themes` とは役割が別）。`fetched_at` は **UPSERT 時に `description_text` が実際に変化したときだけ更新**される＝「テキスト最終変化時刻」の意味（`repo.upsert_company_description`）。差分タガーは銘柄ごとのタグ時刻カーソル（`fetch_meta` の source キー `us_themes:<symbol>`・ISO datetime）と `fetched_at` を比較し、「未タグ → 説明変化 → 古い順ローテ」の優先順で夜あたり天井（`theme_tagging_nightly_max`）まで再タグする（[ADR-033](decisions.md) 流用）。
- UNIQUE `(market, code)`（1 銘柄 1 行・最新を UPSERT）。

---

### `method_cards` — 手法カタログ／参照知識（将来予約・初期は不要）

「①コード実装された手法への索引（②カタログ）」と「③計算を持たない参照知識」を保持する知識ベース（[advisor.md §5](advisor.md)）。**計算そのものは持たない**——計算は必ずコード（Tool / `signals`）側にある（[ADR-016](decisions.md)）。

**初期はこのテーブルを作らない**。②カタログはコードのレジストリ（全手法をプロンプトに列挙）、③参照知識はリポジトリの markdown で十分。手法・知識が増えて RAG（`sqlite-vec` の意味検索）が必要になった段階でこの形に移す。ここでは将来のスキーマを予約として残す。

| 列 | 型 | 説明 |
|---|---|---|
| `id` | INTEGER PK | |
| `title` | TEXT | 例「日米業種リードラグ (SIG-FIN-036)」|
| `source` | TEXT | URL・引用・PDF パス |
| `summary` | TEXT | 手法の要約 |
| `when_to_apply` | TEXT | 適用条件（注入判定・検索キー）|
| `key_points` | TEXT | 重要パラメータ（例 λ=0.9, K=3）|
| `linked_signal_type` | TEXT | 実装済みなら対応シグナル（例 `lead_lag`、未実装は null）|
| `embedding` | BLOB | sqlite-vec 用ベクトル（初期は null）|
| `updated_at` | TEXT | 更新日時 |

---

## 6. 記録・運用

### `asset_snapshots` — 日次総資産スナップショット
AI が「今週 −3%」等の時系列を語れるように、資産推移グラフ用にも。

| 列 | 型 | 説明 |
|---|---|---|
| `date` | TEXT PK | 日付 |
| `total_value` | REAL | 総資産（JPY・`stock_value`+`cash_value`+`external_value`+`fund_value`+`us_stock_value` の合計）|
| `stock_value` | REAL | 株式評価額（JPY・日本株保有）|
| `cash_value` | REAL | 現金（JPY）|
| `external_value` | REAL | 投信等（`external_assets` 由来の軽量記録・JPY）|
| `fund_value` | REAL | 投信評価額（`fund_holdings`×NAV から導出・JPY・[ADR-054](decisions.md)）|
| `us_stock_value` | REAL | 米株評価額（`us_holdings`×最新 close USD×当夜 FX で JPY 換算・[ADR-057](decisions.md)）|
| `pnl` | REAL | 評価損益（JPY・全資産合算）|

### `fetch_meta` — 差分取得管理
| 列 | 型 | 説明 |
|---|---|---|
| `source` | TEXT PK | データ種別 |
| `last_fetched_date` | TEXT | 取得済み最終営業日 |
| `updated_at` | TEXT | 最終更新日時（運用観測・冪等 UPSERT の証跡）|

初回は約 2 年分（Free）を一括バックフィルし、以降は差分のみ取得（冪等な夜間バッチ）。`updated_at` は「いつ取得が走ったか」を運用で観測するための列で、再取得で UPSERT されても証跡が残る（`0002_fetch_meta`・定義レーン=data-arch）。

### `notifications` — 送信済み通知の冪等ログ（Phase 6・必要時）
無人の夜間バッチが Discord に送る通知を、重複送信なく一度だけ届けるための冪等ログ（[ADR-007](decisions.md)・[ADR-018](decisions.md)）。同じイベント（同 `notify_key`）を同じチャネルへ二重送信しない。

| 列 | 型 | 説明 |
|---|---|---|
| `notify_key` | TEXT | 通知の論理キー（例 `digest:2026-06-03`・冪等の単位。本 Phase は 1 日 1 通の digest に束ねる）|
| `channel` | TEXT | 送信先チャネル（例 `discord`）|
| `sent_at` | TEXT | 送信日時（ISO8601 UTC）|

- **複合 PK `(notify_key, channel)`** で送信を冪等化する（`0010_notifications`・定義レーン=data-arch）。採番は spec 起草時 `0009_notifications` を想定していたが、先行で `0009_news_extraction_and_watchlist_interval` が 0009 を占有したため `0010_notifications`（down_revision=0009_news...）として発行した。

---

> **DB の書き手の系統と衝突回避の実際（[ADR-002](decisions.md) 補注・`_arbitration.md` 決定5）**: DB に触れる OS プロセスは FastAPI 1 つだけ（[ADR-005](decisions.md)）。夜間バッチは APScheduler で FastAPI プロセス内に同居するため、バッチ書き込みと API 書き込みは**同一プロセス内で直列化**され、クロスプロセスの書×書競合は原理的に起きない。書き手の系統は実際には (a) 夜間バッチ、(b) 昼の手入力（`transactions`/`cash`/`external_assets`）、(c) チャット/承認（`policy`/`proposals`/`stock_dossiers`）の 3 系統だが、いずれも同居プロセス内なので衝突しない。`flock` は別 OS プロセスで起動されうる手動バッチ（`POST /batch/run` の裏ジョブ・`python -m app.scripts.backfill`）が同居スケジューラと同時に走るのを防ぐ防御。加えて SQLite `busy_timeout`（例 5000ms）を設定し、稀な競合はリトライで吸収する。運用規律として夜間バッチ実行帯はユーザーが手入力しない（単一ユーザーゆえ自然に守れる）。
