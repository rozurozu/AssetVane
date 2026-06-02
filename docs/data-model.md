# Data Model（データモデル）

SQLite のスキーマと、J-Quants API V2 データとの対応をまとめる。
DB に触れるのは FastAPI のみ（[decisions.md ADR-005](decisions.md)）。スキーマ定義は Python 側に一元化する。

> 列定義は設計時点の案。Phase 0 で実装しながら確定させる。J-Quants V2 の実レスポンスのフィールド名は [jquants.md](jquants.md) を参照し、実装時に実機で確認する。

---

## 1. テーブル一覧

| 区分 | テーブル | 内容 | 書き込むタイミング |
|---|---|---|---|
| 生データ | `stocks` | 上場銘柄マスタ | 夜間バッチ |
| 生データ | `daily_quotes` | 日足四本値（巨大） | 夜間バッチ |
| 生データ | `financials` | 財務・決算 | 夜間バッチ |
| 自分のデータ | `portfolios` | ポートフォリオ（保有のまとまり） | 画面操作 |
| 自分のデータ | `holdings` | 保有銘柄 | 画面操作 |
| 自分のデータ | `watchlist` | 監視銘柄 | 画面操作 |
| 計算結果 | `signals` | スクリーニング結果（事前計算） | 夜間バッチ |

---

## 2. 生データ（J-Quants / 外部ソース由来）

### `stocks` — 上場銘柄マスタ

J-Quants V2 `/v2/equities/master` 由来。

| 列 | 型 | 説明 |
|---|---|---|
| `code` | TEXT PK | 銘柄コード（例 `86970`） |
| `company_name` | TEXT | 銘柄名 |
| `sector33_code` | TEXT | 33 業種コード |
| `sector17_code` | TEXT | 17 業種コード |
| `market_code` | TEXT | 市場区分 |
| `is_etf` | INTEGER | ETF/REIT 等の判別フラグ（業種コードや区分から導出） |
| `updated_at` | TEXT | 取得日時 |

### `daily_quotes` — 日足四本値

J-Quants V2 `/v2/equities/bars/daily` 由来。**最大行数のテーブル**。ETF も同じテーブルに入る（J-Quants は東証上場全銘柄を配信）。

| 列 | 型 | 説明 |
|---|---|---|
| `code` | TEXT | 銘柄コード（FK → stocks.code） |
| `date` | TEXT | 営業日（`YYYY-MM-DD`） |
| `open` | REAL | 始値 |
| `high` | REAL | 高値 |
| `low` | REAL | 安値 |
| `close` | REAL | 終値 |
| `volume` | REAL | 出来高 |
| `adj_close` | REAL | 調整後終値（分割・分配調整） |

- 主キー: `(code, date)` の複合主キー。
- インデックス: `code`、`date` にそれぞれ作成（銘柄横断・日付横断クエリ用）。

### `financials` — 財務・決算

J-Quants V2 `/v2/fins/summary` 由来（財務要約。全プランで取得可）。

| 列 | 型 | 説明 |
|---|---|---|
| `code` | TEXT | 銘柄コード |
| `disclosed_date` | TEXT | 開示日 |
| `fiscal_period` | TEXT | 会計期間（例 `FY2025Q3`） |
| `net_sales` | REAL | 売上高 |
| `operating_profit` | REAL | 営業利益 |
| `profit` | REAL | 純利益 |
| `eps` | REAL | EPS |
| `bps` | REAL | BPS |

- 主キー: `(code, disclosed_date, fiscal_period)` 想定。実フィールドは実機確認。

### （Phase 5）米国 ETF 日足

`UsEtfAdapter` 由来。`daily_quotes` と同形のスキーマで `us_etf_daily` 等に分離するか、`daily_quotes` に `source` 列を足して同居させるかは Phase 5 着手時に決める。

---

## 3. 自分のデータ（手入力 / 画面操作）

### `portfolios` — ポートフォリオ

| 列 | 型 | 説明 |
|---|---|---|
| `portfolio_id` | INTEGER PK | ポートフォリオ ID（複数持てる器） |
| `name` | TEXT | 名前（例「メイン」「実験用」） |
| `created_at` | TEXT | 作成日時 |

> 単一ユーザーだが `portfolio_id` を持たせ、将来の拡張余地を残す（[decisions.md ADR-001](decisions.md)）。

### `holdings` — 保有銘柄

| 列 | 型 | 説明 |
|---|---|---|
| `id` | INTEGER PK | |
| `portfolio_id` | INTEGER | FK → portfolios |
| `code` | TEXT | FK → stocks.code |
| `shares` | REAL | 保有株数 |
| `avg_cost` | REAL | 平均取得単価 |

### `watchlist` — 監視銘柄

| 列 | 型 | 説明 |
|---|---|---|
| `id` | INTEGER PK | |
| `code` | TEXT | FK → stocks.code |
| `note` | TEXT | メモ（任意） |
| `added_at` | TEXT | 追加日時 |

---

## 4. 計算結果

### `signals` — スクリーニング結果（事前計算）

夜間バッチが計算して焼く。朝の通知・一覧画面はここを読むだけで即座に出る（[architecture.md 2.1](architecture.md)）。

| 列 | 型 | 説明 |
|---|---|---|
| `id` | INTEGER PK | |
| `date` | TEXT | 算出日 |
| `code` | TEXT | 銘柄コード（業種シグナルの場合は業種コード） |
| `signal_type` | TEXT | 種別（`momentum` / `volume_spike` / `ai_alpha` / `lead_lag` など） |
| `score` | REAL | スコア・強度 |
| `payload` | TEXT | 補足情報（JSON。指標値・根拠など） |

- インデックス: `(date, signal_type)` でその日の特定種別を高速取得。

---

## 5. メタ情報（任意）

差分取得のために「どこまで取得済みか」を持っておくと夜間バッチが冪等になる。

### `fetch_meta`

| 列 | 型 | 説明 |
|---|---|---|
| `source` | TEXT PK | データ種別（`daily_quotes` 等） |
| `last_fetched_date` | TEXT | 取得済みの最終営業日 |

初回は約 2 年分（Free）を一括バックフィルし、以降は `last_fetched_date` 以降の差分だけ取得する。
