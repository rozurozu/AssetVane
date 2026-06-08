# Roadmap（開発ロードマップ）

薄い「縦スライス」から始め、横に太らせていく。各 Phase に**完了条件**を置き、満たすまで次へ進まない。
**主役は日米株（当面は日本株、米国株は後期）。AI Advisor が製品の中心**で、後続 Phase はその AI に「養分（材料）」を足していく。

---

## Phase 0: 縦スライス（Vertical Slice）

**目的**: DB・API・UI を細く 1 本貫通させ、全部品の繋ぎ方を体得する（認証は単一ユーザーのため無し＝[ADR-001](decisions.md)）。

- backend に最小の FastAPI を立てる（`0.0.0.0` で待ち受け・CORS 設定。別端末から見るため）。
- `JQuantsAdapter`（V2 / `x-api-key`）で**数銘柄**の日足を取得。
- SQLite（WAL）に `stocks` / `daily_quotes` を保存。**再取得しても重複しないよう UPSERT（`INSERT OR REPLACE` 等）で冪等にする**（Phase 1 の再取得で壊れないように最初から）。
- FastAPI が「指定銘柄の日足を返す」REST エンドポイントを公開（[api.md](api.md)）。
- Next.js が API を叩いて**株価チャート**を表示（相対パス `/api` を叩き Next の rewrites が backend へ転送＝[ADR-037](decisions.md)）。

**完了条件**: ブラウザで「3 銘柄の株価チャート」が表示され、データが SQLite 経由で FastAPI から来ている。**同じ銘柄を再取得しても重複行が出ない**。

> いきなり全銘柄バッチに行くと、データ取得・DB・API・UI を同時に相手にして折れる。まず 3 銘柄で貫通させる。

---

## Phase 1: Trend Vane（短期モメンタム検知）— 機能①②

**目的**: 全銘柄バッチとスクリーニングを成立させる。

- 全銘柄の日足を夜間バッチで取得（初回バックフィル + 差分取得、`fetch_meta`）。
- **cron による夜間バッチ起動の最小実装をここで導入**（Phase 3 の「夜の分析AI」が前提にするため前倒し。手動起動 `/batch/run` も用意）。
- TA-Lib でテクニカル指標（移動平均上抜け、RSI 反転 等＝①）。
- 出来高急増シグナル（②）。
- 結果を `signals` に保存。スクリーニング結果の一覧画面。

**完了条件**: cron で夜間バッチが全銘柄を処理して `signals` を更新し、一覧画面で「今日の強い銘柄」が見られる。

> **留意点（初回バックフィル × レート制限）**: Free は **5 req/分**。銘柄ごとに 1 リクエストで全銘柄（約4000）を巡回すると理論上 13 時間超になる。**日付単位の一括取得 API（全銘柄 × 1 日）で日数ループする方式**を採用済み（[jquants.md](jquants.md)）。
>
> **実機実測（2026-06-06・ラズパイ／Free プラン・手動フル取得）**: 日付単位ループ方式で全 **4443 銘柄**の初回バックフィルが完走。所要 **約 4 時間 37 分**で、内訳は `fetch_quotes`（1,904,074 行・462 営業日）が約 2 時間、`fetch_financials`（31,240 行）が約 2 時間と、この 2 ジョブでほぼ全部を占める（日付単位なので 1 営業日あたり約 16 秒＝銘柄ループ方式の破綻は回避できている）。差分運転（毎晩 cron）は差分ゼロで **約 8 分半**。Free の 12 週間遅延により `fetch_quotes`／`fetch_financials` は「契約範囲の前線」（実測時 2026-03-16）で打ち切られる（範囲外日付は 400 が返る＝正常終了・[ADR-008](decisions.md)）。所要実測という当初課題は解消。残るは初回 4.5 時間をどう扱うか（許容 or 分割）の運用判断のみ。
>
> **留意点（TA-Lib）**: TA-Lib は C ライブラリで、ARM（ラズパイ）・pip 単体ではビルドに失敗しやすい。OS パッケージで本体を先に入れる手順を確認するか、純 Python の `pandas-ta` 等の代替も検討する。
>
> **留意点（遅延）**: Free は 12 週間遅延のため見えるのは約 3 か月前の「強い株」。ロジックは正しく、Light 以上で最新データに切り替わる（[decisions.md ADR-008](decisions.md)）。

---

## Phase 2: Portfolio Optimizer（資産比率最適化）— 機能④⑤⑥ ＋ 資産モデル

**目的**: 複数銘柄をまとめて扱い、数理最適化と「資産の全体像」を導入する。

- ④ 保有銘柄の相関ヒートマップ／ポートフォリオのバランス確認。
- ⑤ PyPortfolioOpt による平均分散最適化（リバランス比率提案）。
- ⑥ ポートフォリオ・バックテスト（主要指数との比較）。
- `portfolios` / `holdings` / `transactions`（取引記録→保有を導出）/ `cash` / `external_assets` の入力 UI、`asset_snapshots` の記録。
- `IndexAdapter`（軽量）で主要指数（TOPIX / S&P500 等）の水準を `index_quotes` に取得し、マクロ文脈に。

**完了条件**: 取引を記録すると保有・平均取得単価が導出され、相関マップ・最適比率・過去シミュレーション・資産全体の割合（遅延注記付き）が見える。

> ✅ **解消済み（2026-06-08・c97d50c）**: 過去シミュレーション（backtest⑥）を画面接続。`GET /portfolio/{id}/backtest`（現保有 buy&hold vs TOPIX・spec §4.4）を 1 本足して frontend に繋いだ。これで相関マップ・最適比率・過去シミュレーション・資産全体が出揃い、Phase 2 完了条件「過去シミュレーションが見える」を満たす。

> **当面は単一 portfolio 固定で運用**する（`portfolio_id` は将来の器として持つが、UI/最適化は 1 個前提で進める）。複数 portfolio を全機能に引き回すのは増えてから（[ADR-001](decisions.md)）。

---

## Phase 3: 🧠 AI Advisor（製品の核心）— 2 軸

**目的**: 「で、どうすべきか」を AI が一緒に考える。Phase 0〜2 の事実を材料に方針づくりと提案を行う。

- **軸1 夜の分析AI**: **Phase 1 で導入済みの cron 夜間バッチ**に乗せ、「昨日までの方針（`policy`）」＋「今日の状況（`signals`・ポートフォリオ・資産・指数）」を突き合わせ、方針見直しを提案し `advisor_journal` を生成。方針変更は**承認制**（`proposals.status` で消し込み）。
- **軸2 相談チャットAI**: ダッシュボードのチャットで投資方針を対話調整。Tool Calling で Python の計算結果を引き、**根拠付きで銘柄・比率を提案**。
- **Tool Calling 原則**: AI は計算しない。Python の事実だけを解釈（[decisions.md ADR-014](decisions.md)）。
- **LLM アダプタ**: OpenRouter 既定、`.env` で差替可（[ADR-012](decisions.md)）。
- `policy`（単一・チャットで育てる）／`advisor_journal`（スナップショット履歴）／`proposals`（承認状態）を実装。
- **エラー処理**: LLM 失敗（例外）・無応答（observations 空＝縮退）ともリトライ→ダメなら journal をスキップし、`run_advisor` ジョブが `ok=False` で返す。通知は **runner 集約に一本化**（nightly ジョブ自身は `notify.error` しない）。journal は「observations 非空のときだけ書く」が不変条件（[ADR-018](decisions.md)）。

**完了条件**: チャットで「資産が小さいので短期はリスク取りたい、でもマイナスは避けたい、ゼロカットは許容」と相談すると、AI がトレードオフを整理して `policy` を更新し、夜の分析AI が翌朝それに沿った提案と日記を出す。

> ここが核心。Phase 4 以降は、この AI に渡せる「材料」を増やす作業。

---

## Phase 4: 📚 Stock Dossier（個別銘柄の定性ファンダ調査）

**目的**: ニュース・財務（将来は適時開示）を読み、個別銘柄の**定性的な調査レポート（ドシエ）**を作って更新し続ける。数理・ML（数字）を補う「物語」担当。

**状況（2026-06-05・実装完了）**: backend＋frontend を縦に通し実装済み（schema `0008_dossier`＋`0009_news_extraction_and_watchlist_interval`・repo・`investigate_stock` パイプライン・3 Tool〔`investigate_stock`/`get_dossier`/`fetch_news`・min_phase=4〕・REST〔`/watchlist`・`/dossiers/{code}`〕・夜間巡回ジョブ〔銘柄別 `interval_days`＋夜あたり天井＝ADR-033・MCP 非依存〕・`/watchlist` ページ・`DossierSection`〔react-markdown+rehype-sanitize〕）。pytest green。**`fetch_news` も実ニュース源を実装済み＝`NewsAdapter`〔Google News RSS → httpx＋trafilatura で本文抽出 → AI 要約〕。昼 MCP／夜 httpx の 2 系統は撤回し httpx 一本にした（ADR-020 改訂）**。MCP は将来 403/JS 必須サイト・Google URL 復元の代替候補として残す。**一般ニュースダイジェスト（銘柄に紐づかない別系統・ADR-034）も実装済み（2026-06-06）**＝`general_news` テーブル〔0011〕／夜間ジョブ `fetch_general_news`〔`run_advisor` 直前〕／Tool `get_general_news`〔min_phase=4・軸1/軸2 共用〕／`GET /general-news`／Dashboard `GeneralNewsWidget`。あわせて `CURRENT_PHASE` を 3→4 に修正（Phase 4 Tool 群の露出上げ忘れを是正。**その後 Phase 7(A) 着工で 4→7 に更新済み＝現在の値は 7**）。

- **`investigate_stock(code)` の調査パイプラインを 1 本実装**し、**夜間バッチ（watchlist 巡回・軽め）とチャット Tool（「この銘柄調査して」・リッチ）の両方から呼ぶ**（[ADR-020](decisions.md)）。
- `watchlist`（監視銘柄）＋ `stock_dossiers`（1 銘柄 1 レポート・markdown 要約・`last_investigated_at`）＋ `dossier_sources`（URL＋要約＋日付の台帳・**本文は保存しない**・`source_type` で将来 Twitter 等も）を実装。**`watchlist` はこの Phase 4 で追加**（ドシエと同時・管轄=ai-advisor・`0008_dossier`＝先行の `0007_screening`〔ADR-031〕と revision 衝突を避けて繰り下げ）。Phase 2 では追加しない（`_arbitration.md` 決定1・[DOC-12]）。
- ニュース取得は `fetch_news` Tool 裏に隠す。**当初案の「昼 MCP／夜 httpx」は撤回し、httpx 一本にした（本文抽出は httpx＋trafilatura で足りるため＝ADR-020 改訂）**。発行 1 週間以内・URL 重複排除。MCP は将来 403/JS 必須サイトの代替候補として残す。
- **watchlist 一覧ページに「最終調査日」を表示**し、再調査を促す。
- データ源: 初期は**財務（J-Quants Free）＋一般ニュース（Web/MCP）**。**適時開示（TDnet 有料アドオン）は課金後に後付け**。

**完了条件**: watchlist 銘柄が夜間に軽く調査されてドシエが更新され、チャットで「この銘柄調査して」と言うと同じパイプラインでリッチなレポートが生成・表示される。watchlist 一覧に最終調査日が出る。

**留意点**: 無料の安定した JP ニュース API は不確実なため当面 AI の Web 取得で代替。MCP は無人 cron では使えないことがあるため夜は軽め。

---

## Phase 5: AI Alpha Scorer（決算スコアリング）— 機能③

**目的**: 機械学習スコアを Advisor の材料に加える。

- `financials` と将来リターンで LightGBM の特徴量を設計。
- **学習は別 PC**、`.pkl` をラズパイにコピーして推論のみ（[decisions.md ADR-006](decisions.md)）。
- スコアを `signals`（`signal_type=ai_alpha`）に保存。ランキング画面。

**完了条件**: 学習済みモデルでスコアが算出・ランキング表示され、AI Advisor がスコアを根拠に使える。学習の再現手順がドキュメント化されている。

**状況（推論経路まで実装済み）**: 特徴量・学習・推論の純関数（`quant/ml/{features,train,infer}.py`）・`.pkl` 配置/読込（`ml/model_store.py`）・推論ジョブ `score_ai_alpha`（NIGHTLY_JOBS 登録済み）・signals `signal_type=ai_alpha`・frontend の ai_alpha タブまで実装済み（pytest green）。**残**: 学習済み `.pkl` 未配置（モデル無時は `ok=True` で静かに skip＝ADR-006）・別 PC で学習を一度回して `ml-training.md` の `【実測】`（ハイパラ確定値・CV 評価）を埋めること（完了条件の「学習の再現手順ドキュメント化」が未達）。

---

## Phase 6: Signal Beacon（通知）— 機能⑦⑧

**目的**: 画面を開かなくても重要な変化と AI の提案を受け取れるようにする。

- ⑦ 定期リバランス・アラート（前回見直しから一定期間経過で通知）。
- ⑧ 急変動・ブレイクアウト通知（高スコア銘柄・出来高異常を検知時）。
- **夜の分析AI の当日提案**も Discord へプッシュ。
- **Discord Webhook** で送信（[decisions.md ADR-007](decisions.md)）。cron スケジュール。

**完了条件**: 条件合致時・毎朝、Discord に通知（AI の提案要約を含む）が届く。

**状況（2026-06-06・実装完了・実機検証済み）**: 夜間バッチ末尾に `notify_digest` ジョブを追加し、⑦⑧＋夜AI 当日提案を **1 通の Discord digest** に束ねて送る（phase6-spec.md）。schema `0010_notifications`（複合 PK `notify_key:channel`・自然キー `digest:<UTC日付>`）＋ `DiscordAdapter`（送信失敗で握り・ADR-018）＋冪等 `send_once` で二重送信を防止。⑧は `score>=ALERT_SCORE_MIN` または quant が焼いた `payload.notable` で抽出（3 倍判定を通知層で再閾値化しない＝ADR-016）・score 降順 Top N。⑦は `policy.updated_at` 基準。frontend は `/settings`（health の env 詳細＋手動バッチ起動）を配線。**実機で digest 到達・2 回目の二重送信なしを確認済み**。env: `ALERT_SCORE_MIN`/`ALERT_TOP_N`/`REBALANCE_ALERT_DAYS`/`ALWAYS_DAILY_DIGEST`・`DISCORD_WEBHOOK_URL`。

---

## Phase 7: Sector Lead-Lag ＋ 米国株拡張

**目的**: 米国データを「マクロ文脈」から「定量シグナル」へ昇格させ、研究ベース戦略を追加する。

**分割（[ADR-039](decisions.md)）**: Phase 7 は性質の違う 2 成果物（軽い提示シグナルと重い米株基盤拡張）を束ねていたため、**(A) Sector Lead-Lag を先行**・**(B) 米国株拡張（米株スクリーナー＋通貨/FX＋個別株 OHLCV）を別サブフェーズに分離**した。

### Phase 7(A): Sector Lead-Lag（日米業種リードラグ）— 実装済み

**背景**: 「部分空間正則化付き主成分分析を用いた日米業種リードラグ投資戦略」（中川慧ほか, 人工知能学会 SIG-FIN-036, 2026）。米国業種ショックが翌営業日の日本市場に波及する効果を、事前部分空間へ正則化した PCA で低ランク予測器として捉える。日足のみ・軽量計算でラズパイ夜間バッチに適合。論文要約は [docs/methods/lead-lag.md](methods/lead-lag.md)。

- **取得アーキの逸脱（[ADR-039](decisions.md)）**: 当初想定の `UsEquityAdapter` ではなく、**既存 `IndexAdapter`（フォールバック連鎖）に `YahooIndexSource`（yfinance・配当調整後 close）を足して `index_quotes` に流用**する。米国 SPDR 業種 ETF 11 本（XLB/XLE/XLF/XLI/XLK/XLP/XLU/XLV/XLY/XLC/XLRE）はこの経路で取得。理由は「終値のみ・通貨/FX 不要・最小変更」で、`UsEquityAdapter` の OHLCV/通貨という重い関心を持ち込まないため。同時に Stooq の BOT 判定で死んでいた既存指数取得（^SPX 等）も復旧する。
- 日本側は TOPIX-17 業種別 ETF（1617〜1633・J-Quants の `daily_quotes`）。
- 日米結合相関行列を事前部分空間へ正則化 → 固有分解 → 翌日の日本業種スコアを `signals`（`signal_type=lead_lag`）に最新日のみ UPSERT し提示。**提示専用**（[ADR-009](decisions.md)）。手法はテスト済み純関数 `quant/lead_lag.py`（[ADR-014](decisions.md)/[ADR-016](decisions.md)）。
- 提示/AI: Dashboard ウィジェット（`LeadLagWidget`）＋ AI Tool `get_lead_lag`（`min_phase=7`・軸1/2 共用）＋ `GET /lead-lag`。専用ページなし。
- **Free プラン時**: ハード無効化せず、計算は出した上で**目立つ低信頼バナー**を出す（Free=株価約 12 週間遅延でシグナル日付が約 3 ヶ月古く実用外と明示。Light なら本来機能＝[ADR-039](decisions.md)）。
- **検証（軽量）**: 履歴で Spearman IC ＋ 3 分位ロングショート（q=0.3）の R/R・方向的中率を算出し `meta` に同梱。FF/Carhart 回帰・フル backtest 基盤は対象外。

**完了条件（A）**: 「翌日強含む日本業種ランキング」が夜間バッチで算出・提示され（`GET /lead-lag`・`signals?type=lead_lag`）、Dashboard で描画され、AI Advisor が `get_lead_lag` で材料に使う。

**状況（実装済み）**: 純関数 `quant/lead_lag.py`（部分空間正則化 PCA・確定パラメータ L=60/K0=3/K=3/λ=0.9/q=0.3）・サービス `services/lead_lag.py`・夜間ジョブ `calc_lead_lag`（NIGHTLY_JOBS 登録済み）・`GET /lead-lag`・AI Tool `get_lead_lag`（min_phase=7）・Dashboard `LeadLagWidget`・`IndexAdapter` への `YahooIndexSource`（米国業種 ETF・ADR-039）／`JQuantsIndexSource`（^TPX・ADR-040）まで end-to-end 実装済み（テスト green）。残は別 PC での実データ初回検証指標の穴埋め程度で、完了条件（A）は満たしている。

**留意点（A）**: 論文は取引コスト控除後の超過収益の有無を明示していない。提示用途では軽視できるが、将来の実弾運用では検証必須。

### Phase 7(B): 米国株拡張 — 未着手（繰り延べ・[ADR-039](decisions.md)）

- `UsEquityAdapter` を新設し米国個別株（数千・OHLCV）・米国ファンダ源を取得（[architecture.md 4](architecture.md)）。
- **米株スクリーナー `/us-stocks`（[ADR-031](decisions.md)）**: 日本株スクリーナー（`/stocks`・PER/PBR/時価総額/配当利回り）と同型を米株でも作る。ただし**別ルート・別 `valuation_snapshots` 相当**にする（通貨が ¥↔$、業種分類が 33業種↔GICS、財務ソースが J-Quants↔別ソースで市場を跨げないため）。
- 通貨列・`FxAdapter`・FX 換算と holdings/cash/asset_snapshots への波及・GICS 分類はここで導入する。

**完了条件（B）**: 米国株データを扱え、米株スクリーナー `/us-stocks` が日本株版と同等に動く。通貨/FX 換算が資産評価に反映される。

---

## バリュエーション判断基準（ADR-048・横断 TODO）

Phase をまたぐ機能なのでここに TODO を集約する。**日本株のバリュエーション判断基準は実装済み（2026-06-07・[ADR-048](decisions.md)）**＝`valuation_snapshots` に ROE/営業利益率/純利益率/売上・利益・EPS の YoY 成長率を追加し、AI Tool `get_valuation(code)`／`screen_valuation(criteria)`（min_phase=2・market:JP 明示）と参照知識カード（`backend/app/advisor/cards/valuation.md`・`jp-market-context.md`・常時注入）で、AI が PER/PBR/ROE を根拠に割安/割高を解釈・提示できる。残る山:

- **25 指標フル充足（要 J-Quants 実機確認）**: ROA/ROIC/自己資本比率/D-E/流動比率/EBITDA は総資産・負債を要するが、現 `financials`（売上/営業利益/純利益/EPS/BPS/配当/株数）に無い。J-Quants `fins/summary` の総資産系フィールド有無を確認し、財務取得を拡張してから後付けする。
- **カードローダ機構（近接の planned）**: 今は全カード常時注入。カードが増える前に、メタデータだけ常時露出・本文は選ばれた時にロードする on-demand 機構（progressive disclosure）を用意する（[ADR-048](decisions.md)）。
- **米株バリュエーション**: 上記 Phase 7(B) の `/us-stocks` 別スナップショットで ROE 等も含めて拡張し、通貨/業種（GICS）を跨がず市場内ランクにする。日米横断の "both" バランスは portfolio/資産概要レイヤ（FX 換算）で見る。

---

## 米国データの「深掘りの軌跡」

米国の扱いは固定ではなく、ロードマップが進むと深まる。

| 段階 | 米国データの使い方 | 深さ |
|---|---|---|
| Phase 2〜3 | 為替・主要指数を**マクロ材料**として配分判断に使う | 浅（受動的）|
| **Phase 7** | 米国業種 ETF を**定量シグナル源**に（US→JP 予測）＋米国株拡張 | 中〜深（能動的・ここで昇格）|

---

## 進め方の原則

- 各 Phase は前の完了条件を満たしてから着手する。
- Phase 2 の最適化・バックテスト基盤ができてから Phase 3（AI）、Phase 7（戦略）に進むと検証しやすい。
- 「動く最小」を常に保ち、壊れたら STOP して再計画する。
