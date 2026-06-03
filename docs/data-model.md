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
| 計算結果 | `signals` | スクリーニング結果（事前計算） | 夜間バッチ |
| AI | `policy` | 投資方針（単一・アクティブ） | チャット/承認 |
| AI | `advisor_journal` | 投資日記＋方針スナップショット | 夜の分析AI |
| AI | `proposals` | AI 提案（承認状態つき）| AI/画面操作 |
| AI | `stock_dossiers` | 個別銘柄の調査レポート（1銘柄1行・更新）| 調査パイプライン |
| AI | `dossier_sources` | ドシエのソース台帳（URL＋要約＋日付）| 調査パイプライン |
| AI | `method_cards` | 手法カタログ/参照知識（将来予約・初期はリポジトリ管理） | （将来）|
| 記録 | `asset_snapshots` | 日次の総資産スナップショット | 夜間バッチ |
| 運用 | `fetch_meta` | 取得済みの最終営業日 | 夜間バッチ |
| 運用 | `notifications` | 送信済み通知の冪等ログ（Phase 6・必要時）| 夜間バッチ |

> **通貨について**: 当面は日本株のみで全て JPY 前提。`daily_quotes`/`holdings`/`cash` に通貨列は持たない。**米国株（USD 建て）が入る Phase 7 で、通貨列と FX 換算を導入する**（[roadmap.md Phase 7](roadmap.md)・[ADR-010](decisions.md)）。それまで通貨は YAGNI。

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
| `sector17_code` | TEXT | 17 業種コード |
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
| `net_sales`/`operating_profit`/`profit` | REAL | 売上/営業利益/純利益 |
| `eps`/`bps` | REAL | EPS/BPS |

- 主キー `(code, disclosed_date, fiscal_period)` 想定。実フィールドは実機確認。

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
夜の分析AI が毎晩 1 件書く。方針スナップショットを内包し、これが履歴になる。

| 列 | 型 | 説明 |
|---|---|---|
| `id` | INTEGER PK | |
| `date` | TEXT | 日付 |
| `situation_briefing` | TEXT | その日 AI に渡した構造化事実（JSON）|
| `observations` | TEXT | AI の所見（自由文）|
| `proposal` | TEXT | 当日の提案（銘柄・比率・方針変更案）|
| `proposed_policy_change` | TEXT | 方針変更の提案 JSON（承認待ち・任意）|
| `policy_snapshot` | TEXT | その時点の `policy` まるごと（JSON）＝履歴 |
| `llm_model` | TEXT | 使用モデル（監査用）|
| `created_at` | TEXT | 生成日時 |

> チャットAI（軸2）で方針を変えた場合も、その日の journal に snapshot と変更理由を残す。

### `proposals` — AI 提案（承認状態つき）
夜の分析AI・チャットAI が出す提案（方針変更／銘柄・比率）を独立して持ち、**承認状態を消し込めるようにする**。後から「提案を採ったか／結果どうだったか」を振り返り、提案精度の検証にも使う。

| 列 | 型 | 説明 |
|---|---|---|
| `id` | INTEGER PK | |
| `created_date` | TEXT | 提案日 |
| `kind` | TEXT | `policy_change` / `buy` / `sell` / `rebalance` |
| `body` | TEXT | 提案内容（JSON。変更案や銘柄・比率）|
| `rationale` | TEXT | 根拠（AI の説明）|
| `status` | TEXT | `pending` / `approved` / `rejected` |
| `outcome` | TEXT | 採否後の結果メモ（任意・振り返り用）|
| `resolved_at` | TEXT | 承認/却下した日時 |
| `journal_id` | INTEGER | 関連する `advisor_journal`（任意）|
| `depends_on` | INTEGER NULL | 先に承認が要る提案（FK→`proposals.id`・任意）|

- インデックス `status`（未処理の提案を拾う）。
- `depends_on` は提案間の承認順制御に使う（例: `policy_change` を承認してから `buy` を承認）。Dashboard が「依存」を表現する（[screens.md §3](screens.md)・`_arbitration.md` 決定4）。`0006_advisor_state` の DDL に含める。

### `stock_dossiers` — 個別銘柄の調査レポート（1銘柄1行）
`investigate_stock(code)` が生成・更新する living document（[ADR-020](decisions.md)）。watchlist 一覧で「最終調査日」を表示し、「そろそろ調査」を促す。

| 列 | 型 | 説明 |
|---|---|---|
| `code` | TEXT PK | 銘柄（FK→stocks）。1 銘柄 1 行 |
| `summary_md` | TEXT | AI 生成の調査要約（**markdown**）。UI でそのまま描画 |
| `key_facts` | TEXT | 構造化した要点 JSON（PER/成長率/直近トピック等）|
| `last_investigated_at` | TEXT | 最終調査日時（watchlist 一覧の「最終調査日」）|
| `updated_at` | TEXT | 更新日時 |

### `dossier_sources` — ソース台帳（URL＋要約＋日付）
取り込んだソースを記録。**記事全文は持たず、短い要約と URL だけ**残す（[ADR-020](decisions.md)）。`source_type` で将来 Twitter/X 等も同じ台帳に入る。

| 列 | 型 | 説明 |
|---|---|---|
| `id` | INTEGER PK | |
| `code` | TEXT | 銘柄（FK→stocks）＝紐付け |
| `source_type` | TEXT | `news` / `disclosure` / `twitter` 等 |
| `url` | TEXT | ソース URL（**UNIQUE**＝重複防止）|
| `title` | TEXT | 見出し（任意）|
| `summary` | TEXT | 短い要約（本文は保存しない）|
| `published_at` | TEXT | 発行日 |
| `processed_at` | TEXT | 取り込んだ日時 |

- インデックス: `url`（UNIQUE）、`code`。
- 取り込みは「発行が直近（例 1 週間以内）の新着のみ・URL 重複排除」。
- 適時開示（TDnet）は有料アドオンのため後付け。`source_type='disclosure'` でこの台帳に入れる（構造が複雑になれば専用テーブルに分離）。

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
| `total_value` | REAL | 総資産 |
| `stock_value` | REAL | 株式評価額 |
| `cash_value` | REAL | 現金 |
| `external_value` | REAL | 投信等 |
| `pnl` | REAL | 評価損益 |

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
| `notify_key` | TEXT | 通知の論理キー（例 `signals:2026-06-03`・冪等の単位）|
| `channel` | TEXT | 送信先チャネル（例 `discord`）|
| `payload` | TEXT | 送信内容の要約（任意）|
| `sent_at` | TEXT | 送信日時 |

- **複合 PK `(notify_key, channel)`** で送信を冪等化する（`0008_notifications`・定義レーン=data-arch・`_arbitration.md` 採番 0008）。

---

> **DB の書き手の系統と衝突回避の実際（[ADR-002](decisions.md) 補注・`_arbitration.md` 決定5）**: DB に触れる OS プロセスは FastAPI 1 つだけ（[ADR-005](decisions.md)）。夜間バッチは APScheduler で FastAPI プロセス内に同居するため、バッチ書き込みと API 書き込みは**同一プロセス内で直列化**され、クロスプロセスの書×書競合は原理的に起きない。書き手の系統は実際には (a) 夜間バッチ、(b) 昼の手入力（`transactions`/`cash`/`external_assets`）、(c) チャット/承認（`policy`/`proposals`/`stock_dossiers`）の 3 系統だが、いずれも同居プロセス内なので衝突しない。`flock` は別 OS プロセスで起動されうる手動バッチ（`POST /batch/run` の裏ジョブ・`python -m app.scripts.backfill`）が同居スケジューラと同時に走るのを防ぐ防御。加えて SQLite `busy_timeout`（例 5000ms）を設定し、稀な競合はリトライで吸収する。運用規律として夜間バッチ実行帯はユーザーが手入力しない（単一ユーザーゆえ自然に守れる）。
