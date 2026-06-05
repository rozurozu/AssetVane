# lead 裁定メモ（R3 の正本）

> team-lead による横断不整合の確定。`_review.md` B 節の是正案を踏まえ、**正本を 1 枚に固定**する。
> R3 で各レーンはこのメモに機械的に揃える。コードは書かない・git/jj 触らない・過去コミット不可侵。
> 作成: 2026-06-03。

---

## 決定1: Alembic 通し番号表（B-1・B-13 解消）

**単線チェーン。data-arch が全移行の発行（ファイル作成）を一元管理**するが、テーブルの定義内容は所属レーンが正本を持つ（下表「定義レーン」）。実装は各 Phase 着手時にそのリビジョンを書く（順序の予約）。

| revision | down_revision | Phase | テーブル | 定義レーン |
|---|---|---|---|---|
| `0001_baseline` | — | 0(既存) | stocks, daily_quotes | （既存） |
| `0002_fetch_meta` | 0001 | 1 | fetch_meta（+updated_at） | data-arch |
| `0003_signals` | 0002 | 1 | signals（+UNIQUE(date,code,signal_type)） | quant |
| `0004_portfolio_and_assets` | 0003 | 2 | portfolios, holdings, transactions, cash, external_assets, index_quotes, asset_snapshots | data-arch |
| `0005_financials` | 0004 | 2 | financials | data-arch（B-7） |
| `0006_advisor_state` | 0005 | 3 | policy, advisor_journal, proposals（+depends_on） | ai-advisor |
| `0007_screening` | 0006 | 1 | valuation_snapshots, screening_filters（ADR-031・後付け割り込み） | data-arch |
| `0008_dossier` | 0007 | 4 | watchlist, stock_dossiers, dossier_sources | ai-advisor（B-13） |
| `0009_notifications` | 0008 | 6 | notifications（送信冪等ログ・必要時） | data-arch |

- **watchlist は `0007`(Phase 4・ai-advisor)に一本化**。data-arch は Phase 2 から watchlist を外す（B-13）。
- **financials は `0005`(Phase 2・data-arch)** で取得仕様ごと定義（B-7）。

---

## 決定2: Tool 返却スキーマの正本（B-2）

**原則**: quant の純関数が「事実」を計算→ Tool handler(ai-advisor) は薄く包む→ app の REST 型と一致。
**全レーン共通の約束**:
- 比率・weight・cash_ratio・deviation の current/limit は **すべて 0..1**（DB/API/Tool）。UI でのみ ×100 して %。
- 遅延フラグは **`is_delayed: bool`**（`delayed` は廃止）。鮮度日は **`as_of: "YYYY-MM-DD"`**。
- correlation は **`{codes: string[], labels: string[], matrix: number[][]}`**（順序保証・UI 直結）。
- weights は **配列 `[{code, current_weight, target_weight, delta}]`**（dict 返しは禁止・順序安定）。

### 確定スキーマ

```jsonc
get_indicators(code) ->
  {code, as_of, adj_close, sma25, sma75, rsi14, vol_ma20, is_delayed}
  // 平坦。sma5 は P1 では計算しない（quant 実装が真実）。ネスト sma:{} は採用しない。

get_signals(date, type?) ->
  {date, is_delayed, signals: [{code, company_name, signal_type, score, payload}]}
  // company_name は signals JOIN stocks（ルータ）。行レベル date は持たない（トップのみ）。
  // payload(JSON) に label(短文) と change_5d を quant が格納（B-6）。

screen_stocks(criteria) ->
  {date, is_delayed, items: [{code, company_name, signal_type, score, indicators}]}
  // criteria キーは内部列名: {signal_type?, sector33_code?, min_score?, limit?}
  // 各 item は indicators(指標値dict) を持つ（payload ではなく indicators）。

get_portfolio_metrics(portfolio_id) ->
  {portfolio_id, as_of, annual_return, annual_volatility, sharpe, max_drawdown,
   correlation: {codes, labels, matrix}, lookback_days, is_delayed,
   deviations: [{kind, label, current, limit, breached}]}
  // deviations は quant の単一関数が計算（B-12）。current/limit は 0..1。

optimize_portfolio(portfolio_id) ->
  {portfolio_id, as_of, objective, cash_weight,
   weights: [{code, current_weight, target_weight, delta}],
   expected_annual_return, expected_annual_volatility, expected_sharpe,
   constraints_applied, infeasible}
  // weights/cash_weight は 0..1。

get_asset_overview() ->
  {as_of, total_value, stock_value, cash_value, external_value, pnl, is_delayed,
   allocation: [{name, value, weight}], policy_targets,
   deviations: [{kind, label, current, limit, breached}], trend}
  // allocation は name 単位(株式/現金/投信)・weight は 0..1。
  // deviations は get_portfolio_metrics と同じ Python 関数(quant)から供給（出力先2つ・計算1か所）。

get_financials(code) ->
  {code, items: [{disclosed_date, fiscal_period, net_sales, operating_profit, profit, eps, bps}]}
  // financials テーブル(0005)から。data-arch が取得仕様を定義。
```

---

## 決定3: /chat 契約（B-4・B-5）

- `context.focus` の正本 = **`{type: "stock"|"portfolio"|"signal"|"proposal", code?: string, id?: number}`**（ai-advisor 形）。portfolio/proposal は code を持たないため id を使う。
- Tool 実行可視化の正本 = **`tool_runs: [{name: string, args?: object}]`**（app 形）。`tool_calls_made: string[]` は廃止。**結果の数値は載せない**（ADR-025）。
- api.md §4 を `context: {page, focus?: {type, code?, id?}}` に拡張（DOC-4）。

---

## 決定4: proposals.depends_on（B-8）→ 採用

`proposals` に **`depends_on INTEGER NULL`（FK→proposals.id）** を追加（policy_change→buy の承認順制御）。`0006_advisor_state` の DDL・data-model.md §5 に反映。

---

## 決定5: ADR-002「書き手」の解釈補注（B-9）

- **DB に触れる OS プロセスは FastAPI 1 つだけ**（ADR-005）。夜間バッチは **APScheduler で FastAPI プロセス内に同居**（data-arch 方式C）するので、バッチ書き込みと API 書き込みは**同一プロセス内**で直列化され、クロスプロセスの書×書競合は原理的に起きない。
- `flock` は **別 OS プロセスで起動されうる手動バッチ**（`POST /batch/run` の裏ジョブ・`python -m app.scripts.backfill`）が、同居スケジューラと同時に走るのを防ぐ防御。
- 加えて **SQLite `busy_timeout`（例 5000ms）** を設定し、稀な競合はリトライで吸収。
- 運用規律: 夜間バッチ実行帯はユーザーが手入力しない（単一ユーザーゆえ自然に守れる）。
- → ADR-002/ data-model.md に「書き手の系統と衝突回避の実際」を 1 段落補注（DOC-9）。**コード上の追加は busy_timeout 設定のみ**（flock は既出）。

---

## 決定6: 責任分界・所在（B-10・B-11・B-12）

- **ARM ビルド**: 依存の追加判断＝quant、Docker クロスビルド検証の段取り＝data-arch。Phase 1 着手の**最初のゲート**（numpy/pandas を入れたイメージが aarch64 で通るか）。
- **/batch/run 契約の正本 = data-arch**: body `{full_backfill?: bool}`・成功 `202 {started: bool, job_id?: string}`・ロック競合 `409`。app は §P6-1 をこれに合わせる。
- **deviations 計算は quant の単一関数**。`/asset-overview`（画面）と `get_portfolio_metrics`（Tool）の**両方に同値を供給**（計算1か所・出力先2つ）。

---

## 決定7: lead 裁量 OPEN（F-2・27件）→ 全て推奨値で確定

`_review.md` F-2 表（L-1〜L-27）の推奨値をそのまま採用する。主要:
- L-1 cron=APScheduler 同居（C）/ L-2 /batch/run 非同期202+409 / L-3 営業日=曜日+空レス吸収 / L-4 is_etf 是正は Phase7 温存 / L-5 master 全件は実機確認・不可なら daily の code から補完 / L-6 `JQUANTS_MIN_INTERVAL_SECONDS`(Free13/Light1) / L-7 自分データは FK 張る・生データは張らない / L-8 portfolios `(1,'Default')` seed / L-9 既定 portfolio = `GET /portfolios` 先頭 / L-10 IndexAdapter=Stooq 既定 / L-13 get_indicators は P1 オンザフライ再計算 / L-14 期待リターン=historical mean+Ledoit-Wolf / L-15 backtest 手数料=無視+注記 / L-16 SSE=Phase3 は非ストリーミング+tool_runs 同梱 / L-17 journal=`submit_journal` Tool で受ける / L-18 depends_on 追加（決定4）/ L-22 watchlist stale=21日 / L-23 investigate=同期で着工 / L-24 markdown=react-markdown+rehype-sanitize / L-26 adj_close 欠損=skip / L-27 watchlist=Phase4(決定1)。

---

## 決定8: ユーザー裁定 OPEN（F-1・9件）→ 推奨値を「既定」に採用しつつ `_open-questions.md` で要確認

U-1〜U-9（momentum 重み 0.6/0.4・volume 閾値 3.0・rf=0・P5 ラベル 60日回帰・LLM コスト上限・会話履歴=localStorage・policy 更新は構造化=承認/rationale=即時・夜ドシエ N 件 21日・cron 02:00 JST）は**推奨値を spec のデフォルトとして書く**が、投資の好み・コスト・運用時間に関わるので `docs/phase-specs/_open-questions.md` に列挙してユーザー確認に回す。値は後から差し替え可能な形（env/policy/設定）で実装する前提。
