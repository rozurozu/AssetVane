# Phase 2 着工仕様: Portfolio Optimizer（資産比率最適化＋資産モデル）

> 出所: roadmap.md Phase 2 / ADR-019(transactions 導出)/ ADR-013(policy 制約二重活用)/ ADR-001(単一ユーザー・`portfolio_id` は器)/ ADR-010(IndexAdapter)/ ADR-014(AI は計算しない)/ ADR-008(Free 12週遅延)/ ADR-005(DB は FastAPI のみ)/ ADR-002(SQLite WAL・UPSERT 冪等)/ ADR-021(ARM クロスビルド)。
> レビュー・裁定（`_drafts/_arbitration.md` 正本）反映済み。**コード未実装＝着工仕様**（コードは書かない）。
> 単位の約束: 比率・weight・cash_ratio・deviation の current/limit は **すべて 0..1**（DB/API/Tool）。UI でのみ ×100 して %。遅延フラグは `is_delayed: bool`、鮮度日は `as_of: "YYYY-MM-DD"`。correlation は `{codes, labels, matrix}`。weights は配列 `[{code, current_weight, target_weight, delta}]`（dict 返し禁止）。

---

## 0. 目的と完了条件

**目的**（roadmap.md Phase 2）: 複数銘柄をまとめて扱い、数理最適化と「資産の全体像」を導入する。
- ④ 保有銘柄の相関ヒートマップ／ポートフォリオのバランス確認。
- ⑤ PyPortfolioOpt による平均分散最適化（リバランス比率提案）。
- ⑥ ポートフォリオ・バックテスト（主要指数との比較）。
- `portfolios`/`holdings`/`transactions`（取引記録→保有を導出＝ADR-019）/`cash`/`external_assets` の入力 UI、`asset_snapshots` の日次記録。
- `IndexAdapter`（軽量）で主要指数（TOPIX / S&P500 等）の水準を `index_quotes` に取得し、マクロ文脈に。

**完了条件**: 取引を記録すると保有・平均取得単価が導出され、相関マップ・最適比率・過去シミュレーション・資産全体の割合（遅延注記付き）が見える。

**前提する Phase 1**: 全銘柄バッチ＋差分取得（`fetch_meta`）／夜間バッチ基盤（`app/batch/` の `runner.run_nightly`・`NIGHTLY_JOBS`・`lock.py`・`calendar.py`・APScheduler 同居＝裁定 L-1）／`signals`・`numpy`/`pandas` 依存。Phase 2 のジョブはこの `NIGHTLY_JOBS` に append する。

---

## 1. 全体像（前提する Phase 1・当面単一 portfolio 固定）

- **当面は単一 portfolio 固定で運用**（roadmap.md Phase 2・ADR-001）。API は将来の複数化に耐えるよう `{id}` を取る形にするが、UI/最適化は 1 個前提。`0004` 移行で `portfolio_id=1, name='Default'` を 1 行 seed（裁定 L-8）。既定ポートフォリオは `GET /portfolios` の**先頭**で解決（id 固定にしない＝裁定 L-9／app の `DEFAULT_PORTFOLIO_ID=1` は初期定数だが解決は GET 先頭）。
- **holdings は transactions からの導出値**（ADR-019）。直接編集は禁止。追加・変更は `POST /transactions` → サーバ側で holdings 再計算。
- **AI に数値を計算させない**（ADR-014）。評価額・相関・シャープ・最適化・逸脱はすべて Python（quant 純関数）が「事実」を計算。
- **DB に触れるのは FastAPI だけ**（ADR-005）。Next は `lib/api.ts` 経由の REST のみ。書き込みは UPSERT 冪等（ADR-002）。
- レーン境界:
  - **data-arch**: DDL・Alembic 移行（`0004`/`0005`）・`IndexAdapter`・`index_quotes`/`asset_snapshots`/`financials` の取得＆焼きジョブ。
  - **quant**: 相関・シャープ・最大DD・PyPortfolioOpt 最適化・backtest・`compute_deviations`（純関数・`backend/app/quant/`）。
  - **app**: `transactions` 入力 API・`holdings` 導出ロジック配線・REST 契約・Next 画面。
  - 導出関数（holdings 再計算）の置き場所（repo か service か）は **[OPEN]** app/data-arch 合意。

---

## 2. スキーマ変更（`0004_portfolio_and_assets` / `0005_financials`）

`_arbitration.md` 決定1 の Alembic 通し番号表に一致。**単線チェーン・発行は data-arch が一元管理**。

| revision | down_revision | テーブル | 定義レーン |
|---|---|---|---|
| `0004_portfolio_and_assets` | `0003_signals` | portfolios, holdings, transactions, cash, external_assets, index_quotes, asset_snapshots | data-arch |
| `0005_financials` | `0004` | financials | data-arch（B-7） |

- DDL は `backend/app/db/schema.py`（`metadata` が単一の真実）に追記 → `alembic/versions/0004_portfolio_and_assets.py` / `0005_financials.py` に autogenerate。
- **watchlist はここで作らない**（B-13）。Phase 4・ai-advisor の `0008_dossier` に一本化（二重 CREATE で移行が壊れるため）。
- **FK 方針（裁定 L-7）**: **自分データ（手入力）は FK を張る**（誤入力防止の価値が高い・`foreign_keys=ON` は engine.py 既設）＝`transactions`/`holdings`/`financials` の `code→stocks.code`、`portfolio_id→portfolios.portfolio_id`。**生データ間（daily_quotes→stocks）は既存どおり張らない**方針を維持。
- **`portfolios` seed**: `0004` 移行内で `(portfolio_id=1, name='Default')` を 1 行 seed。

### 2.1 DDL（全列）

```sql
CREATE TABLE portfolios (
    portfolio_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    created_at    TEXT                 -- ISO8601
);

CREATE TABLE transactions (              -- 一次データ（ADR-019）。holdings はここから導出
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id  INTEGER NOT NULL REFERENCES portfolios(portfolio_id),
    code          TEXT    NOT NULL REFERENCES stocks(code),
    side          TEXT    NOT NULL,      -- 'buy' / 'sell'
    shares        REAL    NOT NULL,
    price         REAL    NOT NULL,      -- 約定単価
    fee           REAL,                  -- 手数料（任意）
    traded_at     TEXT    NOT NULL       -- 約定日 'YYYY-MM-DD'
);
CREATE INDEX ix_transactions_portfolio ON transactions(portfolio_id);
CREATE INDEX ix_transactions_code      ON transactions(code);

CREATE TABLE holdings (                  -- transactions からの導出値（ADR-019・直接編集しない）
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id  INTEGER NOT NULL REFERENCES portfolios(portfolio_id),
    code          TEXT    NOT NULL REFERENCES stocks(code),
    shares        REAL    NOT NULL,      -- 導出: Σbuy.shares − Σsell.shares
    avg_cost      REAL,                  -- 導出: 移動平均取得単価
    UNIQUE (portfolio_id, code)          -- 1 ポートフォリオ 1 銘柄 1 行（UPSERT キー）
);

CREATE TABLE cash (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    balance       REAL NOT NULL,         -- 投資用待機現金（JPY・通貨列は Phase 7 まで持たない）
    updated_at    TEXT
);

CREATE TABLE external_assets (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    name                   TEXT NOT NULL,   -- 「オルカン」等
    category               TEXT,            -- 投信/コモディティ等
    value                  REAL,            -- 評価額（手入力）
    proxy_symbol           TEXT,            -- 概算 proxy（指数等）
    monthly_contribution   REAL,            -- 毎月積立（任意）
    as_of                  TEXT             -- 基準日
);

CREATE TABLE index_quotes (              -- 主要指数の水準（daily_quotes とは別粒度・別出所）
    symbol  TEXT NOT NULL,               -- 'TOPIX' / '^GSPC' 等
    date    TEXT NOT NULL,               -- 'YYYY-MM-DD'
    close   REAL,                        -- 終値（水準）
    PRIMARY KEY (symbol, date)
);
CREATE INDEX ix_index_quotes_symbol ON index_quotes(symbol);

CREATE TABLE asset_snapshots (           -- 日次総資産（夜間バッチが焼く・1 日 1 行）
    date            TEXT PRIMARY KEY,    -- 'YYYY-MM-DD'
    total_value     REAL,
    stock_value     REAL,
    cash_value      REAL,
    external_value  REAL,
    pnl             REAL
);

CREATE TABLE financials (                -- 財務・決算（`0005_financials`・data-model.md §2）
    code             TEXT NOT NULL REFERENCES stocks(code),
    disclosed_date   TEXT NOT NULL,    -- 開示日 'YYYY-MM-DD'
    fiscal_period    TEXT NOT NULL,    -- 会計期間（例 '2025Q1' / 'FY2024'・実値は実機確認）
    net_sales        REAL,             -- 売上高
    operating_profit REAL,             -- 営業利益
    profit           REAL,             -- 純利益
    eps              REAL,             -- EPS
    bps              REAL,             -- BPS
    PRIMARY KEY (code, disclosed_date, fiscal_period)
);
CREATE INDEX ix_financials_code ON financials(code);
```

`schema.py` 追記イメージ（既存 `Table` 流儀・`from __future__ import annotations`／型注釈必須）:

```python
# financials（data-model.md §2・0005_financials）
financials = Table(
    "financials",
    metadata,
    Column("code", String, ForeignKey("stocks.code"), nullable=False),
    Column("disclosed_date", String, nullable=False),  # 'YYYY-MM-DD'
    Column("fiscal_period", String, nullable=False),
    Column("net_sales", Float),
    Column("operating_profit", Float),
    Column("profit", Float),
    Column("eps", Float),
    Column("bps", Float),
    PrimaryKeyConstraint("code", "disclosed_date", "fiscal_period", name="pk_financials"),
    Index("ix_financials_code", "code"),
)
```

---

## 3. データ取得（IndexAdapter / index_quotes / financials 取得ジョブ / asset_snapshots 日次）

### 3.1 IndexAdapter（軽量・Stooq 既定＝裁定 L-10）

`backend/app/adapters/index.py`（ADR-010・data-model.md §2）。J-Quants 範囲外（米国指数・日本指数の一部）なので別ソース。

- **取得対象（初期）**: TOPIX、日経225、S&P500（`^GSPC`）。マクロ文脈用なので**終値（水準）のみ**（`index_quotes.close` 1 列）。
- **データソース**: **Stooq を既定**（`https://stooq.com/q/d/l/?s=...&i=d` の CSV・無料で日次終値が安定）。`.env.example` の `US_EQUITY_SOURCE=stooq` 系で差替可。
- **[OPEN]** TOPIX/日経に J-Quants 指数 API があるか未確認（jquants.md 未記載）。無ければ Stooq の該当シンボルで代替。ソース確定は Phase 2 着手時に実機確認。
- ソース固有の「外部キー名→内部列名」対応はこのファイルに閉じ込める（ADR-010）。

```python
# backend/app/adapters/index.py（ADR-010）
class IndexAdapter:
    def fetch_index_quotes(self, symbol: str, from_: str | None = None, to: str | None = None
                           ) -> list[dict[str, Any]]: ...
        # 戻り: [{"symbol","date","close"}, ...]（内部列名）。
```

```python
# backend/app/batch/jobs/fetch_index.py（NIGHTLY_JOBS に append）
def run() -> JobResult: ...
    # 対象 symbol ごとに fetch_index_quotes（差分は fetch_meta['index_quotes:<symbol>']）→ upsert_index_quotes。
```

### 3.2 financials 取得ジョブ（`0005`・B-7）

`backend/app/adapters/jquants.py` に `fetch_financials` を追加（V2 財務・正規化は既存 `_normalize_*` 流儀でこのファイルに閉じ込める）。

```python
# adapters/jquants.py 追加（V2 財務・実機確認後にパス/キー確定）
def fetch_financials(self, code: str | None = None, date: str | None = None
                     ) -> list[dict[str, Any]]: ...
    # code 指定で 1 銘柄、date 指定でその日開示の全銘柄（bars/daily と同じ「日付一括」が効くか実機確認）。
    # 戻り: [{code, disclosed_date, fiscal_period, net_sales, operating_profit, profit, eps, bps}]。

# backend/app/batch/jobs/fetch_financials.py（NIGHTLY_JOBS に append）
def run() -> JobResult: ...
    # 差分は fetch_meta['financials'].last_fetched_date（開示日ベース）。日付一括で取得（効かなければ保有+watchlist 銘柄ループ）→ upsert_financials → fetch_meta 前進。
```

- **`fetch_meta` source = `'financials'`**（開示日 ≦ last_fetched_date は取得済みで冪等再開）。
- **取得範囲（[OPEN]）**: Phase 2 は**保有＋watchlist 銘柄に限定**して取得（Free で全銘柄毎晩は過剰）。全銘柄バックフィルは Phase 5（ML 学習・別 PC 側＝ADR-006）で広げる。quant の P5 特徴量設計と要すり合わせ。
- **[DOCS要修正]**: V2 財務エンドポイント（`/v2/fins/summary` か `/v2/equities/statements` か）と実フィールド名は未確定。jquants.md §6 要再確認リストに追加し、実機確認後にパスと正規化を確定。

### 3.3 asset_snapshots 日次ジョブ

`backend/app/batch/jobs/snapshot_assets.py`（NIGHTLY_JOBS に append）。「保有評価額（最新 `daily_quotes`）＋現金＋外部資産」を集計し 1 日 1 行 UPSERT。**評価額の計算（holdings×最新株価）は app/quant の導出関数を呼ぶ**（本ジョブは「焼く・保存する」糊）。app の導出関数が出来てから配線。

---

## 4. 数理ロジック（quant 純関数・`backend/app/quant/`）

すべて **`adj_close` の日次リターンから**算出（分割・併合の段差除去）。年率換算は **252 営業日**。各関数は「入力 DataFrame → 出力 dict」の純粋関数（DB を知らない＝ADR-016）。`adj_close` 欠損（窓内に null）は当該銘柄・当該日を skip（補間しない＝裁定 L-26）。

### 4.1 メトリクス（`get_portfolio_metrics` の実体）

```python
# backend/app/quant/portfolio.py（ADR-016: 純粋関数・docs/data-model.md §3）
def compute_portfolio_metrics(
    price_panel: pd.DataFrame,   # index=date, columns=code, 値=adj_close（保有銘柄ぶん）
    weights: dict[str, float],   # 現在の構成比（時価ベース・0..1）
    policy: dict | None = None,  # 逸脱判定に使う（無ければ deviations は空）
    labels: dict[str, str] | None = None,  # code -> company_name（correlation.labels 用）
) -> dict: ...
```

| 指標 | 式 | パラメータ |
|---|---|---|
| 日次リターン | `ret = price_panel.pct_change().dropna()` | — |
| 相関行列 | `ret.corr()`（ピアソン） | ヒートマップ用 |
| 共分散（年率） | `ret.cov() * 252` | 最適化入力にも使う |
| ポート日次リターン | `port_ret = (ret * w).sum(axis=1)` | w=weights |
| 年率リターン | `port_ret.mean() * 252` | — |
| 年率ボラ | `port_ret.std(ddof=1) * sqrt(252)` | — |
| シャープ | `(年率リターン − rf) / 年率ボラ` | **rf=0.0**（U-3 裁定済み・`RISK_FREE_RATE=0.0` 名前付き定数／ADR-027 レーン） |
| 最大DD | `cum=(1+port_ret).cumprod(); dd=cum/cum.cummax()-1; mdd=dd.min()` | 負値 |

- **lookback**: 直近 **252 営業日**。不足なら取得できた日数で計算し `lookback_days` に実数。
- 返却 dict = Tool `get_portfolio_metrics`（§5 P2-5 と同形）。`correlation` は `{codes, labels, matrix}`（`codes[i]`/`labels[i]` が `matrix[i][j]` に対応）。`is_delayed` は as_of の鮮度で判定（プラン仮定でなく実測・ADR-071）。

### 4.2 deviations（policy 逸脱）の単一計算関数（B-12・決定6）

**deviations の計算は quant の単一関数に一本化**し、`/asset-overview`（画面）と `get_portfolio_metrics`（Tool）の**両方へ同値**を供給（計算 1 か所・出力先 2 つ）。

```python
# backend/app/quant/portfolio.py
def compute_deviations(
    weights: dict[str, float],          # 現在の銘柄ウェイト（0..1）
    cash_ratio: float,                  # 現在の現金比率（0..1）
    sector_weights: dict[str, float],   # 業種別合計ウェイト（0..1）
    policy: dict,                       # policy の構造化コア
    labels: dict[str, str] | None = None,
) -> list[dict]: ...
    # 戻り: [{kind, label, current, limit, breached}]（current/limit は 0..1）。
```

| kind | current | limit | breached の意味 |
|---|---|---|---|
| `max_position` | 各銘柄の現ウェイト | `max_position_weight` | 1 銘柄が上限超過 |
| `cash_ratio` | 現在の現金比率 | `target_cash_ratio` | 現金が目標を**下回る**（現金は最低ライン） |
| `sector_cap` | 業種別合計ウェイト | `sector_caps[sector]` | 業種が上限超過 |

> 判定方向は kind ごとに固定。`cash_ratio` は「下回ると違反」、`max_position`/`sector_cap` は「上回ると違反」。kind 名は実装・REST・frontend で統一（旧 `position`/`cash`/`sector` 表記は廃止）。

### 4.3 平均分散最適化（`optimize_portfolio` の実体）— policy 制約の写像

```python
# backend/app/quant/optimize.py（純粋関数。PyPortfolioOpt or 自前 SLSQP）
def optimize_portfolio(
    price_panel: pd.DataFrame,        # index=date, columns=code, adj_close（候補銘柄群）
    policy: dict,                     # policy 行（構造化コア）
    sectors: dict[str, str],          # code -> sector33_code（sector_caps 用）
    objective: str = "max_sharpe",    # 'max_sharpe' | 'min_volatility' | 'efficient_return'
) -> dict: ...
```

**policy（data-model.md §5）→ 最適化制約への写像（ADR-013 の二重活用）**:

| policy 列 | 型 | 最適化制約への写像 |
|---|---|---|
| `target_cash_ratio` | REAL（0..1） | 株式合計ウェイト上限 = `1 - target_cash_ratio`（残りは現金・最適化対象外） |
| `max_position_weight` | REAL（0..1） | 各銘柄 `w_i <= max_position_weight`（`weight_bounds=(0, max_position_weight)`） |
| `sector_caps` | JSON `{sector33_code: cap}` | 業種ごと `sum(w_i for i in sector) <= cap`（`add_sector_constraints`） |
| `no_leverage` | INTEGER（0/1） | `w_i >= 0`（空売り禁止）＋ `sum(w) <= 1`（レバ無し）。1 で long-only |
| `exclusions` | JSON `[code,...]` | 候補から除外（price_panel から落とす） |
| `target_return` | REAL（任意） | `efficient_return` のときの目標年率リターン。null なら max_sharpe |
| `risk_tolerance`/`time_horizon` | TEXT | 直接の制約にしない（objective 選択のヒント。高→max_sharpe／低→min_volatility 等） |

- **期待リターンの推定（確定・L-14）**: PyPortfolioOpt の `mean_historical_return`（年率・historical mean）＋共分散は `CovarianceShrinkage().ledoit_wolf()`（Ledoit-Wolf 縮小推定。標本共分散は不安定）。Black-Litterman は過剰なので後回し。
- **`infeasible`**: 制約が厳しすぎて解が無い場合 `true`＋空 `weights`（422 ではなく `infeasible:true` で返し、画面が提示できる）。
- 返却 dict = Tool `optimize_portfolio`（§5 P2-6 と同形）。`weights` は配列・すべて 0..1・`delta = target_weight − current_weight`。

### 4.4 バックテスト（⑥・対指数）

```python
# backend/app/quant/backtest.py（純粋関数）
def backtest_portfolio(
    price_panel: pd.DataFrame,    # 候補/保有銘柄の adj_close
    weights: dict[str, float],    # 検証する固定ウェイト（最適化結果等）
    benchmark: pd.Series,         # 主要指数の水準（index_quotes 由来・TOPIX 等）
    rebalance: str = "none",      # 'none' | 'monthly'（初期は none=buy&hold）
) -> dict: ...
```

- **初期は buy & hold（rebalance='none'）**。月次リバランスは `monthly` 引数だけ予約（後付け）。
- 比較指標: 累積リターン曲線・年率リターン・シャープ・最大DD を**ポート vs ベンチ**で並べる。ベンチは `index_quotes`（IndexAdapter 供給・TOPIX 既定）。
- **取引コスト・手数料・スリッページは無視＋注記のみ**（確定・L-15。提示用途。実弾運用時に要検証と注記）。

---

## 5. REST API 契約（app レーン・`lib/api.ts` と Pydantic は 1:1）

横断方針: JSON 日付は `YYYY-MM-DD`、金額/株数/指標は `number`（JPY 前提）。エラーは FastAPI 標準 `{"detail": ...}`。取得できない値は `null`。評価額系は `is_delayed`/`as_of` を**フラットに**載せる（`asset-overview`/`metrics`/`optimize`）。`holdings` のみ `valuation_meta:{as_of,is_delayed,plan}` ラッパに包む（保有行配列と並ぶため）。`plan`（"free"/"light"）は遅延文脈の説明用。

### P2-1. `GET /portfolios`（当面 GET 中心）
- `GET /portfolios` → `Portfolio[]`。POST/PUT/DELETE は単一前提で当面未実装。
- Pydantic: `PortfolioOut`。lib/api.ts: `getPortfolios(): Promise<Portfolio[]>`。
- 既定ポートフォリオは配列**先頭**で解決（裁定 L-9・OPEN-C 確定）。

```ts
export interface Portfolio { portfolio_id: number; name: string; created_at: string; }
```

### P2-2. `GET /holdings` / `POST /transactions`（→holdings 再計算・ADR-019）
- `GET /holdings?portfolio_id=` → `HoldingsResponse`。
- `POST /transactions` body=`TransactionInput` → 記録 → **サーバ側で holdings 再計算**（ADR-014/019・フロントは送るだけ） → レスポンスは更新後 holdings を含む `TransactionResult`。
- Pydantic: `HoldingOut`/`HoldingsResponse`/`ValuationMeta`/`TransactionIn`/`TransactionResult`。lib/api.ts: `getHoldings(portfolioId)` / `postTransaction(input): Promise<TransactionResult>`（`postJSON` ヘルパ新設）。

```ts
export interface Holding {
  id: number;
  code: string;
  company_name: string | null;   // stocks JOIN
  shares: number;                 // 取引から導出
  avg_cost: number;               // 平均取得単価（取引から導出・移動平均）
  last_close: number | null;      // daily_quotes の MAX(date) の close（評価用）
  market_value: number | null;    // shares * last_close（遅延値）
  unrealized_pnl: number | null;  // market_value - shares*avg_cost
  weight: number | null;          // 株式内の比率（0..1。UI で ×100）
}
export interface HoldingsResponse {
  portfolio_id: number;
  holdings: Holding[];
  valuation_meta: ValuationMeta;
}
export interface ValuationMeta { as_of: string | null; is_delayed: boolean; plan: string; }
export interface TransactionInput {
  portfolio_id: number;
  code: string;
  side: "buy" | "sell";
  shares: number;
  price: number;          // 約定単価
  fee?: number | null;    // 任意
  traded_at: string;      // 約定日 YYYY-MM-DD
}
export interface TransactionResult { transaction_id: number; holdings: HoldingsResponse; }
```

### P2-3. `GET/PUT /cash`
- `GET /cash` → `Cash`。`PUT /cash` body `{ balance }` → 更新後 `Cash`。
- Pydantic: `CashOut`/`CashIn`。lib/api.ts: `getCash()` / `putCash(balance)`。

```ts
export interface Cash { balance: number; updated_at: string; }
export interface CashInput { balance: number; }
```

### P2-4. `GET/POST/PUT/DELETE /external-assets`
- `GET` → `ExternalAsset[]` / `POST` → 作成行 / `PUT /external-assets/{id}` → 更新行 / `DELETE /external-assets/{id}` → `{ ok: true }`。
- Pydantic: `ExternalAssetOut`/`ExternalAssetIn`。lib/api.ts: `getExternalAssets()` / `createExternalAsset(input)` / `updateExternalAsset(id, input)` / `deleteExternalAsset(id)`。

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

### P2-5. `GET /portfolio/{id}/metrics`
正本 = `get_portfolio_metrics`（_arbitration 決定2）。計算は quant、本書は入れ物の形。`annual_return`/`annual_volatility`/`max_drawdown` は 0..1。
- Pydantic: `PortfolioMetricsOut`/`CorrelationMatrixOut`（`Deviation` は P2-7 共用）。lib/api.ts: `getPortfolioMetrics(portfolioId)`。
- null 条件（保有 1 銘柄・履歴不足）は quant が確定。

```ts
export interface PortfolioMetrics {
  portfolio_id: number;
  as_of: string | null;
  is_delayed: boolean;
  annual_return: number | null;     // 年率リターン（0..1）
  annual_volatility: number | null; // 年率ボラ（0..1）
  sharpe: number | null;
  max_drawdown: number | null;      // 最大ドローダウン（0..1）
  lookback_days: number | null;     // 計算に使った遡及日数
  correlation: CorrelationMatrix;
  deviations: Deviation[];          // /asset-overview と同じ quant 単一関数が供給
}
export interface CorrelationMatrix {
  codes: string[];                  // 行/列の順序（銘柄コード）
  labels: string[];                 // 表示名（company_name）
  matrix: number[][];               // codes×codes の相関係数（-1..1）
}
```

### P2-6. `POST /portfolio/{id}/optimize`
正本 = `optimize_portfolio`（_arbitration 決定2）。すべての比率は 0..1。
- 省略時は現在の policy 制約をそのまま使う（backend が policy を読む）。上書きは任意で送る。
- 解なしは `422` ではなく `infeasible:true`＋空 `weights` で返す。
- Pydantic: `OptimizeIn`/`OptimizeResultOut`/`OptimizeWeightOut`。lib/api.ts: `optimizePortfolio(portfolioId, body?)`。
- **[要一致 ai-advisor]** policy 制約のキー名・単位（0..1）は `policy` レスポンス（P3）と一致（ADR-013 二重活用）。

```ts
export interface OptimizeRequest {     // 上書きしたい時のみ送る（任意・すべて 0..1）
  target_cash_ratio?: number | null;
  max_position_weight?: number | null;
  sector_caps?: Record<string, number> | null;
}
export interface OptimizeResult {
  portfolio_id: number;
  as_of: string | null;
  is_delayed: boolean;
  objective: string;                 // 例 "max_sharpe"
  cash_weight: number;               // 現金比率（0..1）
  weights: OptimizeWeight[];         // 配列（正本フィールド名は weights）
  expected_annual_return: number | null;
  expected_annual_volatility: number | null;
  expected_sharpe: number | null;
  constraints_applied: {             // 実際に使った制約（すべて 0..1）
    target_cash_ratio: number | null;
    max_position_weight: number | null;
    sector_caps: Record<string, number> | null;
  };
  infeasible: boolean;               // true なら weights は空
}
export interface OptimizeWeight {
  code: string;
  company_name: string | null;       // app の UI 直結のため JOIN 付与
  current_weight: number | null;     // 現状比率（0..1）
  target_weight: number;             // 最適比率（0..1）
  delta: number;                     // target - current（0..1・リバランス差分）
}
```

### P2-7. `GET /asset-overview`
正本 = `get_asset_overview`（_arbitration 決定2）。`allocation` は `[{name,value,weight}]`・`weight`/`deviations.current,limit` は 0..1。
- **逸脱（deviations）は Python が計算**し `/asset-overview` の戻り値に含める（決定6・B-12。新 Tool を立てない）。quant の**単一関数**が `get_portfolio_metrics` と**同値**を供給。
- Pydantic: `AssetOverviewOut` ほか（`Deviation` は P2-5 と共用）。lib/api.ts: `getAssetOverview()`。

```ts
export interface AssetOverview {
  as_of: string | null;
  is_delayed: boolean;
  plan: string;                      // "free" 等
  total_value: number;
  stock_value: number;
  cash_value: number;
  external_value: number;
  pnl: number;                       // 評価損益
  allocation: AllocationSlice[];     // 配分ドーナツ用
  policy_targets: {                  // policy との対比（0..1）
    target_cash_ratio: number | null;
    max_position_weight: number | null;
  };
  deviations: Deviation[];
  trend: AssetSnapshotPoint[];       // 資産推移スパークライン（日次）
}
export interface AllocationSlice {
  name: "株式" | "現金" | "投信";
  value: number;                     // 評価額（JPY）
  weight: number;                    // 総資産内の比率（0..1）
}
export interface Deviation {
  kind: "max_position" | "cash_ratio" | "sector_cap";  // app の REST 表記（quant の position/cash/sector に対応）
  label: string;                     // "最大銘柄比率" 等
  current: number;                   // 0.182（0..1）
  limit: number;                     // 0.15（0..1）
  breached: boolean;                 // current が limit を逸脱
}
export interface AssetSnapshotPoint { date: string; total_value: number; }
```

### Tool 返却スキーマ正本（_arbitration 決定2・参考）
REST 型はこれと同形。`get_financials` は `0005` から `{code, items:[{disclosed_date, fiscal_period, net_sales, operating_profit, profit, eps, bps}]}`。

---

## 6. frontend（取引入力 UI・相関ヒートマップ・最適比率・資産推移・遅延注記）

`docs/screens.md` #5・#6・§3。Tailwind v4 トークン（DESIGN.md）・density-first。フロント着工順: ①lib/api.ts（型＋ヘルパ）→ ②画面 → ③Sidebar nav の href 化。

- **`frontend/src/app/portfolio/page.tsx`**（新規・screens.md #5）: 保有テーブル（`getHoldings`）＋相関ヒートマップ（`getPortfolioMetrics`）＋最適比率（`optimizePortfolio` ボタン起動）＋資産推移（`getAssetOverview`）。評価額カードに遅延注記（`valuation_meta.as_of`）。
  - **`frontend/src/components/portfolio/CorrelationHeatmap.tsx`**（新規）: props `{ data: CorrelationMatrix }`。SVG グリッド・セル色は `accent`↔`down` の二極グラデ（正相関=accent 寄り・負=down 寄り）・対角は除外・density-first（`num`）。
  - **`frontend/src/components/portfolio/OptimizeTable.tsx`**（新規）: props `{ result: OptimizeResult }`。`current → target` と `delta`（増=`text-up`/減=`text-down`）。
- **`frontend/src/app/transactions/page.tsx`**（新規・screens.md #6）: 取引入力フォーム（`postTransaction`）＋現金入力（`putCash`）＋投信入力（external-assets CRUD）。
  - **`frontend/src/components/portfolio/TransactionForm.tsx`**（新規）: props `{ portfolioId, stocks: Stock[], onDone(holdings) }`。side（buy/sell トグル・buy=`up`/sell=`down`）・code・shares・price・traded_at・fee。送信後 `TransactionResult.holdings` で一覧更新。フォーム入力は `bg-canvas border-hairline focus:border-accent`。
- **Dashboard 実配線**: `app/page.tsx` の `kpis`/`allocation`/逸脱表示を `getAssetOverview()` に差し替え。`mock-data.ts` の `kpis`/`allocation`/`trendPath` 削除。Topbar 既存バッジ（`Free・株価12週遅延`）に加え各カードに `meta="12週遅延・<as_of>基準"`。
- **遅延注記**: Free 12週遅延（ADR-008）。評価額・P/L・現金比率を含むレスポンスは `is_delayed`/`as_of` を必ず表示。
- **Sidebar nav**: `Portfolio` を `href: "/portfolio"` 化。**[OPEN-D]** Transactions を独立 nav にするか Portfolio 内タブにするか。推奨: Portfolio 内に「保有 / 入力」タブ（nav を増やしすぎない）。
- **変更ファイル**: `lib/api.ts`（型・関数・`postJSON`/`putJSON`/`del` ヘルパ）・`lib/mock-data.ts`（nav・mock 削除）・`app/page.tsx`（Dashboard 配線）。

---

## 7. 追加依存ライブラリ（ARM ビルドゲート）

quant レーン（`backend/pyproject.toml`）:
```
scipy>=1.13           # 相関・統計
PyPortfolioOpt>=1.5   # 平均分散最適化（内部で cvxpy を使う）
# cvxpy は PyPortfolioOpt の依存として入る
```
（numpy/pandas は Phase 1 で導入済み。）

**ARM ビルド（ADR-021）— 最重要確認点・Phase 2 着手の最初のゲート**:
- 調査（2026-06）: **cvxpy 1.9.1 が manylinux aarch64 wheel を配布**・numpy2 対応済み。pip で ARM wheel が入る見込み。
- PyPortfolioOpt が古い numpy/cvxpy にピン留めしていないか・aarch64 で実際に解けるかは **Docker クロスビルド（ADR-021）で実機確認が必須**。
- **詰めば scipy SLSQP フォールバック**: cvxpy が ARM で破綻したら、平均分散最適化を自前 `scipy.optimize.minimize`（SLSQP・制約付き二次計画）で実装。**推奨はまず PyPortfolioOpt を試し、通らなければ自前 SLSQP**。
- 責任分界: **依存の追加判断＝quant**、**Docker クロスビルド検証の段取り＝data-arch**（決定6）。

---

## 8. テスト計画

**quant 純関数（DB 不要・既知系列 → 既知値・ADR-016）**:
- シャープ: 既知 2 資産・固定リターン系列 → 手計算シャープと ±0.01 一致。
- 最大DD: 山→谷→山の系列 → 既知 MDD（例 -0.30）と一致。
- 最適化 long-only: `no_leverage=1` で全 w>=0 かつ sum(w)+cash=1。
- max_position_weight: 上限 0.2 で全 w<=0.2。
- sector_caps: 同業種合計が cap 以下。
- infeasible: 矛盾制約（max 0.1 × 3 銘柄 + cash 0.8）→ `infeasible=true`＋空 weights。
- backtest: buy&hold の累積リターンが手計算と一致・ベンチ超過列の符号が正しい。
- deviations: position/cash/sector の各 breached 判定方向（cash は下回りで違反）。
- adj_close null: 窓内に null → skip。

**data-arch**:
- `test_migrations`: `0004`/`0005` 適用後に各テーブル・`portfolios` seed 行（id=1）が存在。
- `IndexAdapter`: HTTP モック（Stooq CSV）で `{symbol,date,close}` 正規化。
- `fetch_index`/`fetch_financials` ジョブ: アダプタをスタブし UPSERT 行数・`fetch_meta` 前進を検証。実 API は叩かない。

**app**:
- holdings 導出: 既知 transactions 列（buy/sell 混在）→ 期待 shares・avg_cost（移動平均）。全売却で行が消える/0 株になる確認。
- `POST /transactions` → `TransactionResult.holdings` が再計算後値。
- 各 GET の Pydantic 形・`valuation_meta`/`is_delayed`/`as_of`・0..1 単位の確認。

---

## 9. 着工順（チェックリスト）

1. **[ゲート]** PyPortfolioOpt/cvxpy の ARM クロスビルド実機確認（data-arch 段取り・quant 依存選定）。通らなければ SLSQP フォールバック判断。
2. **DDL**: `schema.py` に portfolios/holdings/transactions/cash/external_assets/index_quotes/asset_snapshots を追記 → `0004_portfolio_and_assets` autogenerate（`portfolios` seed 行込み）→ `test_migrations`。
3. `financials`: `schema.py` 追記 → `0005_financials` → `fetch_financials` アダプタ＋ジョブ＋テスト（V2 エンドポイント実機確認後）。
4. `IndexAdapter`（Stooq・ソース実機確認後）＋ HTTP モックテスト → `fetch_index` ジョブを `NIGHTLY_JOBS` に追加。
5. **quant**: `quant/portfolio.py`（`compute_portfolio_metrics`＋`compute_deviations`）→ test → `quant/optimize.py`（最適化＋policy 写像）→ test → `quant/backtest.py` → test。
6. **app holdings 導出**: 再計算ロジック（置き場 [OPEN]）＋ repo 読み → `GET /holdings`・`POST /transactions`・`GET/PUT /cash`・external-assets CRUD。
7. Tool/REST 配線: `GET /portfolio/{id}/metrics`・`POST /portfolio/{id}/optimize`・`GET /asset-overview`（deviations 同梱）。
8. `snapshot_assets` ジョブ（app の評価額導出関数が出来てから・`NIGHTLY_JOBS` に追加）。
9. **frontend**: lib/api.ts（型＋ヘルパ）→ asset-overview 配線で Dashboard 実化 → holdings/transactions 画面 → metrics/optimize 画面（CorrelationHeatmap/OptimizeTable/TransactionForm）→ Sidebar nav href 化。

---

## 10. このPhaseの [OPEN]（`_open-questions.md` 参照）

**ユーザー裁定（推奨値を既定に採用済み・env/設定/policy で差替可）**:
- **U-3 rf（無リスク金利・シャープ計算）** ✅裁定済み: **`RISK_FREE_RATE=0.0` 固定**（名前付き定数で外出し・magic number 禁止）。扱いは **ADR-027 レーン**（名前付き定数 → 将来 `method_settings`）で、**policy にも env にも入れない**。日本の無リスク金利はゼロ近傍でシャープの順位がほぼ不変・外部依存とテスト非決定性を避けるため。（`_open-questions.md` U-3）

**技術リスク（実機確認・ユーザー判断不要）**:
- PyPortfolioOpt/cvxpy が aarch64 で通るか（cvxpy 1.9.1 に wheel あり）。詰めば自前 SLSQP（§7）。
- TOPIX/日経の J-Quants 指数 API 有無（無ければ Stooq 代替・§3.1）。
- V2 財務エンドポイント（`/v2/fins/summary` か `/v2/equities/statements` か）と実フィールド名（§3.2・jquants.md 要再確認）。

**レーン内 [OPEN]**:
- FK を SQLAlchemy Core で張る範囲（自分データは張る＝裁定 L-7 で確定。adr-guardian と最終確認）。
- holdings 再計算ロジックの置き場所（repo か service か・app/data-arch 合意）。
- financials 取得対象の絞り込み（Phase 2 は保有+watchlist／Phase 5 で全銘柄・quant P5 とすり合わせ）。
- OPEN-D: Transactions を独立 nav か Portfolio 内タブか（推奨タブ）。
