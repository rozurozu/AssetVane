# Phase 着工仕様（phase-specs）

AssetVane の **Phase 1〜6 を「あとはコーディングするだけ」の状態**にまとめた着工仕様。
設計の真実は引き続き `docs/`（README / decisions / architecture / data-model / api / advisor / roadmap / jquants / screens）にある。本ディレクトリはその上に立つ**実装可能な詳細**——ファイル単位の変更リスト・DDL・関数シグネチャ・確定パラメータ・テスト計画・着工順——を Phase ごとに置く。

> **位置づけ**: コードはまだ書いていない（Phase 0 完了済み・Phase 1 が次）。本仕様は「実装の実際（現状コード）」に接地させたうえで、`docs/` の留意点・未確定を**決め切った**もの。各 Phase 着手時にこのファイルを開けば、迷いなく書き始められる。

---

## 0. これは何か / どう作られたか

複数エージェントのチーム（`assetvane-design`）で、**レーン別起草 → 横断レビュー（相互指摘）→ lead 裁定 → Phase 別合成**の順に固めた。

- **レーン**: data-arch（データ取得/バッチ/cron/移行/通知）・quant（シグナル/最適化/ML）・app（REST/画面/型）・ai-advisor（CORE/Tool/調査）。
- **横断レビュー**: adr-guardian が ADR 不変条件違反・レーン間契約不整合・破壊的変更を点検（`_drafts/_review.md`）。**ADR 違反ゼロ・既存 Phase 0 実装の作り直しゼロ**を確認。
- **lead 裁定**: 不整合の正本を 1 枚に固定（`_drafts/_arbitration.md`）。本 README §2 がその要約。

---

## 1. 成果物

### 主たる着工仕様（Phase 別）

| ファイル | Phase | 内容 |
|---|---|---|
| [phase1-spec.md](phase1-spec.md) | 1 Trend Vane | 全銘柄バッチ（営業日ループ）＋ momentum/volume_spike ＋ `/signals` ＋一覧 |
| [phase2-spec.md](phase2-spec.md) | 2 Portfolio Optimizer | 資産モデル（transactions 導出）＋相関/シャープ/最大DD＋ PyPortfolioOpt 最適化＋資産概要 |
| [phase3-spec.md](phase3-spec.md) | 3 AI Advisor（核心） | CORE/POLICY ＋ Tool Calling ＋夜の分析AI/相談チャットAI ＋ policy/journal/proposals |
| [phase4-spec.md](phase4-spec.md) | 4 Stock Dossier | `investigate_stock` 調査パイプライン＋ dossiers/sources ＋ watchlist |
| [phase5-spec.md](phase5-spec.md) | 5 AI Alpha Scorer | LightGBM（学習=別PC・推論のみ）→ `signals(ai_alpha)` ＋ランキング |
| [phase6-spec.md](phase6-spec.md) | 6 Signal Beacon | Discord 通知（リバランス/ブレイクアウト/夜AI提案）＋送信冪等 |

### 横断・運用

- [_open-questions.md](_open-questions.md) — **ユーザー裁定が必要な 9 件**（投資の好み・コスト・運用時間）。推奨値を既定採用済みなのでこのまま着工可。違うなら差し替え。
- `_drafts/` — チームの作業記録（**provenance**・破棄せず保持）: 各レーンの起草（data-arch/quant/app/ai-advisor）・横断レビュー（`_review.md`）・lead 裁定（`_arbitration.md`）・現状コードマップ（`_current-state.md`）。

---

## 2. 横断の正本（全 Phase 共通・実装時はここに合わせる）

レーン間で割れていた契約を lead が裁定した結果。**Phase 仕様の数値・型はすべてこれに一致している**。

### 2.1 Alembic 通し番号（単線チェーン）

移行ファイルの発行は **data-arch が一元管理**、テーブル定義の正本は所属レーンが持つ。

| revision | down_revision | Phase | テーブル | 定義レーン |
|---|---|---|---|---|
| `0001_baseline` | — | 0(既存) | stocks, daily_quotes | （既存） |
| `0002_fetch_meta` | 0001 | 1 | fetch_meta（+updated_at） | data-arch |
| `0003_signals` | 0002 | 1 | signals（+UNIQUE(date,code,signal_type)） | quant |
| `0004_portfolio_and_assets` | 0003 | 2 | portfolios, holdings, transactions, cash, external_assets, index_quotes, asset_snapshots | data-arch |
| `0005_financials` | 0004 | 2 | financials | data-arch |
| `0006_advisor_state` | 0005 | 3 | policy, advisor_journal, proposals（+depends_on） | ai-advisor |
| `0007_dossier` | 0006 | 4 | watchlist, stock_dossiers, dossier_sources | ai-advisor |
| `0008_notifications` | 0007 | 6 | notifications（送信冪等ログ） | data-arch |

### 2.2 単位の約束

比率・weight・cash_ratio・deviation の current/limit は **すべて 0..1**（DB / API / Tool 返却）。**UI でのみ ×100 して %** 表示。遅延フラグは **`is_delayed: bool`**、鮮度日は **`as_of: "YYYY-MM-DD"`**（旧 `delayed` は廃止）。

### 2.3 Tool 返却スキーマ（AI は計算しない＝ADR-014。quant が事実を計算し handler が薄く包む）

```jsonc
get_indicators(code) -> {code, as_of, adj_close, sma25, sma75, rsi14, vol_ma20, is_delayed}   // 平坦・sma5なし
get_signals(date, type?) -> {date, is_delayed, signals:[{code, company_name, signal_type, score, payload}]}
screen_stocks(criteria) -> {date, is_delayed, items:[{code, company_name, signal_type, score, indicators}]}
   // criteria キーは内部列名 {signal_type?, sector33_code?, min_score?, limit?}
get_portfolio_metrics(portfolio_id) ->
   {portfolio_id, as_of, annual_return, annual_volatility, sharpe, max_drawdown,
    correlation:{codes, labels, matrix}, lookback_days, is_delayed,
    deviations:[{kind, label, current, limit, breached}]}
optimize_portfolio(portfolio_id) ->
   {portfolio_id, as_of, objective, cash_weight,
    weights:[{code, current_weight, target_weight, delta}],
    expected_annual_return, expected_annual_volatility, expected_sharpe, constraints_applied, infeasible}
get_asset_overview() ->
   {as_of, total_value, stock_value, cash_value, external_value, pnl, is_delayed,
    allocation:[{name, value, weight}], policy_targets, deviations:[...], trend}
get_financials(code) -> {code, items:[{disclosed_date, fiscal_period, net_sales, operating_profit, profit, eps, bps}]}
// Phase4+: get_dossier(code) / investigate_stock(code, mode) / fetch_news(code, since)
```

- `correlation` は `{codes, labels, matrix[][]}`（順序保証・UI 直結）。`weights` は配列（dict 返し禁止）。
- `deviations` は **quant の単一関数**が計算し `/asset-overview` と `get_portfolio_metrics` の両方へ同値供給（計算1か所・出力先2つ）。
- `signals.payload`(JSON) に `label`(短文)・`change_5d` を quant が格納。`company_name` はルータの `JOIN stocks`。

### 2.4 /chat 契約（ADR-024 常駐・ADR-025 軽量 context）

- `context: {page, focus?: {type: "stock"|"portfolio"|"signal"|"proposal", code?, id?}}`（stock/signal=code、portfolio/proposal=id）。**数値は載せない**。
- レスポンスに `tool_runs: [{name, args?}]`（実行した Tool を UI に可視化・**結果値は載せない**）。

### 2.5 ADR-002「書き手 1 つ」の実際

DB に触る OS プロセスは **FastAPI 1 つだけ**（ADR-005）。夜間バッチは **APScheduler で FastAPI プロセス内に同居**するので、バッチ書き込みと API 書き込みは同一プロセス内で直列化され、クロスプロセスの書×書は起きない。`flock` は別 OS プロセスで起動されうる手動バッチ（`POST /batch/run` 裏ジョブ・`scripts.backfill`）との相互排他。加えて SQLite `busy_timeout`（例 5000ms）で稀な競合を吸収。

---

## 3. 着工順（推奨）

各 Phase は前の完了条件を満たしてから（roadmap の原則）。**Phase 1 着手の最初のゲート**は「**numpy/pandas を載せた Docker イメージが aarch64(ラズパイ) で通るか**」の実機確認（ADR-021・依存選定=quant／クロスビルド段取り=data-arch）。

Phase 1 → 2 → 3（核心）→ 4 → 5 → 6。Phase 3 の Tool は Phase 1/2 で実装した計算関数を呼ぶので、その順序が前提になる。

---

## 4. 着工前に潰す「実機確認」（好みではなく事実確認）

`_open-questions.md` の補足にも記載。J-Quants V2 の未確認エンドポイント（`/v2/equities/master` 全件取得・財務 statements・取引日カレンダー・主要指数）は `jquants.md` §6 要再確認リストに追記済み。依存ライブラリ（numpy/pandas・cvxpy・lightgbm）の aarch64 ビルド可否も各 Phase 着手時のゲート。
