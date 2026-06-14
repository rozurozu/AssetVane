# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## このプロジェクトについて

**AssetVane** は、日米の株式を分析し、**AI と投資方針を相談しながら銘柄・配分を提案する**、個人投資家 1 人用の投資ダッシュボード。自動売買はせず、提示に徹する。

**現状は「Phase 1〜4（Trend Vane / Portfolio Optimizer / AI Advisor / Stock Dossier）＋ Phase 6（Signal Beacon 通知）＋ Phase 7(A)（Sector Lead-Lag）着工済み・Phase 5（AI Alpha Scorer）は推論経路まで実装済み。backend＋frontend が縦に通し検証済み（Phase 4 は 2026-06-05・Phase 6 は 2026-06-06 に実機で Discord digest 到達・冪等まで確認）」**。設計の真実は `docs/` にある。実装を始める前に必ず `docs/` を読むこと。

- **実装済み（Phase 0〜4 の backend＋frontend が縦に通る）**: Phase 0 の縦スライス（`JQuantsAdapter` V2 → SQLite `stocks`/`daily_quotes` → `/stocks`・`/quotes` → frontend の実データローソク足）に加え、**Phase 1**（`batch/` の夜間バッチ runner/lock/notify ＋ 16 ジョブ・`signals` テーブルと momentum/volume_spike・`GET /signals`・`POST /batch/run`・APScheduler 同居 cron）、**Phase 2**（`portfolios`/`holdings`/`transactions`/`cash`/`external_assets`/`asset_snapshots`・相関／PyPortfolioOpt 最適化／backtest・`/holdings`・`/transactions`・`/portfolio/{id}/metrics`・`/optimize`・`/asset-overview`）、**Phase 3**（AI Advisor 2 軸・**Tool Calling 接続済み**・`submit_journal`・`policy`/`advisor_journal`/`proposals` の承認制提案）、**Phase 4**（Stock Dossier＝`watchlist`/`stock_dossiers`/`dossier_sources`・`investigate_stock` パイプライン・3 Tool〔min_phase=4〕・`/watchlist`・`/dossiers/{code}`・夜間巡回ジョブ・frontend の `/watchlist` ページ＋`DossierSection`。`fetch_news` も実ニュース源を実装済み＝`NewsAdapter`〔Google News RSS → httpx＋trafilatura で本文抽出 → AI 要約・**昼 MCP／夜 httpx の 2 系統は撤回し httpx 一本に**＝ADR-020 改訂〕・銘柄別の調査 cadence〔`interval_days`＋夜あたり天井・ADR-033〕）まで実装済み。`init_db`=`alembic upgrade head`（0019 まで）・pytest 約 730 件・`/health`・config・Docker Compose・App Router シェルも稼働。**手法（momentum/volume_spike 等）は TA-Lib を使わず自前 quant 純関数で実装**（ADR-016）。
- **残・次の山**: 全銘柄バッチの初回バックフィルは実機実測済み（2026-06-06・全 4443 銘柄・約 4 時間 37 分／差分運転は約 8 分半・roadmap.md）。所要実測という当初課題は解消し、残るは初回 4.5 時間をどう扱うかの運用判断のみ。**Phase 5 の学習を別 PC で一度回して `.pkl` を配置＋実測（ml-training.md の `【実測】` 欄埋め）**が次の山（Phase 7(B-1) 米株スクリーナーは 2026-06-09 に実装済み＝ADR-055・Phase 7(B-2) FX/保有波及は 2026-06-11 に実装済み＝ADR-057・Phase 2 backtest⑥ の画面接続は 2026-06-08 に完了）。**要確認のドリフトは全て解消済み**＝① Phase 2 backtest⑥ は 2026-06-08 に画面接続済み（c97d50c・roadmap 完了条件「過去シミュレーションが見える」達成）／② Phase 3 の journal 昇格（ADR-029）は 2026-06-09 に正式実装済み（cce9c36f＝`AdvisorChat.tsx` は `ChatResponse.journal_id` を読んで成否をインライン表示）／③ ADR-028 の warn 時 Discord 通知＋画面バナーは 2026-06-08 に接続済み（cdbfa24）／④ `GET /dossiers/{code}` は未調査時の振る舞いを **200＋空ドシエで確定**（2026-06-08・当初 spec §5.2 の「404 または空」二択のうち 200 固定を採用・spec/api.md と整合済み）。**Phase 3 の LLM 障害時フォールバックは実装済み**＝縮退（無応答・observations 空）検知を追加し、ハード失敗（LLM 例外/タイムアウト）と合わせて `run_advisor` の `ok=False` → runner 集約通知に一本化（nightly ジョブ自身の `notify.error` は撤去）。journal は「observations 非空のときだけ書く」を不変条件に揃えた（ADR-018）。**LLM は本番＝クラウド強モデル前提（ADR-012：Tool Calling 確実な品質帯）。ローカル弱モデル（qwen3.5:9b 等）は開発時の動作確認用で、弱モデルに Tool を確実に呼ばせる作り込みはしない＝できないことは割り切る**。frontend は signals／ポートフォリオ／取引／スクリーナー／policy／journal／proposals／Dashboard 本体・常駐 Advisor チャット（画面コンテキスト注入込み）・Phase 4 の `/watchlist`＋銘柄詳細の `DossierSection` まで backend と配線され実データ描画する（Dashboard の watchlist も Phase 4 で実配線済み）。**Phase 4 は実ニュース取得まで完了**（`fetch_news`／`NewsAdapter` 稼働）。**一般ニュースダイジェスト（銘柄に紐づかない別系統・ADR-034）も実装済み**（`general_news` テーブル〔0011〕／夜間ジョブ `fetch_general_news`〔`run_advisor` 直前〕／Tool `get_general_news`〔min_phase=4・軸1/軸2 共用〕／`GET /general-news`／Dashboard の `GeneralNewsWidget`。カテゴリ定義は定数モジュール `general_news_config.py`）。あわせて `CURRENT_PHASE` は現在 **7**（Phase 7(A) 着工で 4→7 に更新済み＝min_phase=4 の dossier 系・min_phase=7 の lead_lag Tool もチャット・夜AI に露出）。**Phase 6（Signal Beacon 通知）は実装＋実機検証済み**（夜間バッチ末尾の `notify_digest` が⑦⑧＋夜AI 提案を 1 通の Discord digest に束ね、`notifications` テーブル＋`send_once` で冪等化。`/settings` 画面も配線・phase6-spec.md）。**Phase 7(A)（Sector Lead-Lag）は実装済み**（`quant/lead_lag.py` の部分空間正則化 PCA・`calc_lead_lag` 夜間ジョブ・`GET /lead-lag`・`get_lead_lag` Tool〔min_phase=7〕・Dashboard `LeadLagWidget`。米国業種 ETF は `IndexAdapter` に `YahooIndexSource`、^TPX は `JQuantsIndexSource` を連鎖に追加＝ADR-039/040）。**Phase 5（AI Alpha Scorer）は推論経路まで実装済み**（`quant/ml/`・`score_ai_alpha` 夜間ジョブ・signals `signal_type=ai_alpha`）だが、学習済み `.pkl` は未配置で別 PC での学習実測が未（モデル無時は `ok=True` で静かに skip＝ADR-006）。**統合ニュースコーパス（ADR-044/046/047）も実装済み**（`news_corpus` テーブル〔0013〕／夜間ジョブ `fetch_sector_news`〔`fetch_general_news` 直後・`run_advisor` 前〕／統合ニュースページ `/news`＝一覧・貼付・削除〔`GET`/`POST`/`DELETE /news`〕／3 層文脈 Tool `get_news_context`〔min_phase=4〕＝銘柄/セクター/市況を階層タグで構造化）。**バリュエーション判断基準（ADR-048）も実装済み**（`valuation_snapshots` に ROE/利益率/YoY 成長率〔0012〕／Tool `get_valuation`・`screen_valuation`〔min_phase=2〕／参照知識カード常時注入）。**sector17 二体系の境界固定（ADR-053）も実装済み**（分類 S17／銘柄 ETF ティッカーの別系統を `app/reference/` の業種コード参照知識に集約）。**投資信託の保有管理（ADR-054）も実装済み**（`funds`/`fund_navs`/`fund_transactions`/`fund_holdings`〔0015〕／`FundNavAdapter`／夜間ジョブ `fetch_fund_navs`／`/funds` 系 API／含み損益の随時計算／`asset_snapshots.fund_value`・`external_assets` とは分離）。**意味検索（ADR-045）は段階 A 実装済み（2026-06-09）**＝統合コーパス `news` に embedding/embed_model/embedded_at 3 列〔0016_news_embedding〕／`adapters/embedding.py`（OpenAI 互換 1 本・未設定なら静かに機能オフ）／夜間 `embed_news`＋貼付時 best-effort 即時埋め込み／`search_news` Tool〔min_phase=4・`vec_distance_cosine` で BLOB 直接スキャン＝vec0 仮想テーブルは使わず次元非依存〕。段階 C（FTS5 ハイブリッド）・vec0 昇格は将来（コーパス 5 万行 or レイテンシ >200ms が叩き台）。**ニュース起点 buy/sell 提案の起票（ADR-052）も実装済み（2026-06-11）**＝専用 Tool `propose_trade(action, code, reason)`〔min_phase=4〕で `proposals(kind=buy/sell)` へ承認制起票。検証 only（実起票は `persist_trade_proposals_from_tool_runs` が tool_runs から全件拾い夜AI/チャット共通経路で `begin()` 内 insert）・body=`{code, company_name, market}`・数値ゼロ（ADR-014）・銘柄解決 JP→US で未知は drop（ADR-018）・pending dedup・depends_on=None・承認しても約定なし（ADR-009）・migration 不要。**能動配信（ADR-051・前提の ADR-049 polarity 列）も実装済み（2026-06-13）**＝`news.polarity` 列〔0020・3 値 `positive`/`negative`/`neutral`・NULL=未判定〕／夜間ジョブ `tag_news_polarity`〔`embed_news` 同型・`advisor/news_polarity` で `level='stock'` の未判定行のみ 3 値バッチ判定・LLM 例外/総崩れで `ok=False`＝embed_news 対称・NIGHTLY 順 `investigate_dossier`→`embed_news`→`tag_news_polarity`→…→`notify_digest`〕／`notify_digest` 拡張＝①急騰落の自動説明〔注目シグナル各行へ直近 3 日の stock 層ニュース 1 件 attach・holdings 非依存〕＋②保有銘柄の悪材料アラート〔JP holdings の `polarity='negative'`・`fetched_at` 24h 窓・最大 5 件＋残件数・注目シグナルより前に置き 1900 字截断から保護・has_content に含む〕。**テーマタグ（ADR-050 改訂＋ADR-056）は段階 A〔米株〕を実装済み（2026-06-10）**＝全ユニバース（JP＋US）を実在テキストに grounded で事前タグ付けする方針（名前推測禁止・code 同一性／信号源＝米株 `longBusinessSummary`・JP は EDINET 有報「事業の内容」＝ADR-056）。段階 A の実体は `themes`・`stock_themes`・`company_descriptions` 3 テーブル〔0018_themes・UPSERT＋時間窓 prune・source 列なし〕／`fetch_us_fundamentals` 相乗りで説明取り込み／grounded タガー `advisor/theme_tagger.py`〔evidence 本文照合・壊れた応答はタグ 0 件に倒す〕／夜間 `tag_us_themes`〔種テーマ冪等投入＋夜天井 150＋prune 90 日〕・`embed_themes`〔embedding＋near_duplicate_of 判定・自動マージなし〕／種テーマ 44 個 `reference/theme_seeds.py`／Tool 3 本〔`list_themes`/`get_stock_themes`/`screen_by_theme`・min_phase=7・業種絞りは JP=sector17_code/US=gics_sector の別引数＝ADR-053〕／一括バックフィル `app.scripts.backfill_themes`〔中断再開可・**初回実行は未**＝LLM コスト発生のため手動〕。**段階 B〔JP 調査済みオーバーレイ〕も実装済み（2026-06-11）**＝`investigate_stock` がドシエ要約 `summary_md` を `company_descriptions(JP, source='dossier')` に W2〔`upsert_company_description_tx`〕で焼き、夜間 `tag_jp_themes`〔`tag_us_themes` 対称・`list_jp_codes_for_theme_tagging` 起点・prune は market='JP' 限定・天井 `theme_tagging_jp_nightly_max=100`・NIGHTLY 順 `investigate_dossier`→`tag_jp_themes`→`embed_themes`〕が既存 `tag_stock_themes(market='JP')` を無改変で再利用。**毎晩 LLM 再タグ最適化**＝説明未変化なら LLM を呼ばず `bump_stock_themes_last_seen` で last_seen_at だけ bump（段階 B 固有）。**「2書き手共存」は reframe 済み**＝`company_descriptions` は `UNIQUE(market,code)` の1銘柄1テキスト（全市場共通・US も yfinance 1ソース）、調査済み JP は **dossier 優先**〔dossier ⊇ EDINET ゆえ共存不要・段階 C は「dossier 行があれば edinet で上書きしない」で解く〕、prune は「②説明変化で確認されなくなったタグの時間窓減衰」として効く（ADR-050 実装メモ）。watchlist から外してもタグは保持（減衰させない）。near_duplicate_of は段階 B でも「フラグのみ」（自動マージなし）。**段階 C〔EDINET 全ユニバース〕も実装済み（2026-06-11・ADR-056）**＝`EdinetAdapter`〔`adapters/edinet.py`・書類一覧 type=2／取得 type=5＝CSV ZIP・UTF-16 から `DescriptionOfBusinessTextBlock` を抜き HTML strip〕→ 要約 `advisor/edinet_summary` → `company_descriptions(JP, source='edinet')`。**取得モデルは提出日クロール型**（EDINET は提出日でしか一覧を引けない）でカーソルは `fetch_meta('edinet:crawl')` 1 本、クロール core `batch/jobs/fetch_edinet_descriptions.crawl` を夜間差分〔`run`＝カーソル翌日〜今日・NIGHTLY 順 `investigate_dossier`→`fetch_edinet_descriptions`→`tag_jp_themes`〕とバックフィル `app.scripts.backfill_edinet`〔約15ヶ月窓・中断再開可・**初回は LLM コストのため手動**〕が共有。**dossier 優先 2 段ガード**〔事前 skip＋`upsert_company_description_edinet` の `source!='dossier'` ガード〕で「dossier 行があれば edinet で上書きしない」、docTypeCode=120 のみ〔訂正 130 は対象外〕、migration 不要〔`company_descriptions` の既存列流用・`secCode`↔`stocks.code`（5桁）直接一致〕。`/settings` は `POST /edinet/run-differential`〔runner から抽出した `run_jobs([fetch_edinet_descriptions, tag_jp_themes])` をオンデマンド・夜間と同じ lock/state/通知〕。**Phase 7(B-1)（米株スクリーナー）は実装済み**（ADR-055）＝提示専用で既存 JPY 資産評価コアに無改変。データ源は yfinance 一本＝`UsEquityAdapter`〔`adapters/us_equity.py`・IndexAdapter 同型のフォールバック連鎖〕／日本株コアと別テーブル `us_stocks`/`us_daily_quotes`/`us_valuation_snapshots`〔0017_us_equity・`currency` 列は持たない〕／業種は Yahoo `.info.sector`〔GICS 相当 11 分類の文字列保持・厳密 GICS コードは追わない・和訳表 `app/reference/gics_sectors.py`〕／夜間 4 ジョブ〔`sync_us_universe`→`fetch_us_quotes`→`fetch_us_fundamentals`〔`.info` を ADR-033 同型でローテ巡回〕→`calc_us_valuation`・`snapshot_assets` 後の独立ブロック〕／派生比率・市場内ランクは `quant/valuation.py` 純関数で読み取り時 Python 計算（ADR-014/016）／AI Tool `get_us_valuation`・`screen_us_valuation`〔min_phase=7・`market:US`/`currency:USD` 明示・verdict なし〕は日本株 Tool 無改変／`GET /us-stocks`・`/us-stocks/screen`・`/us-stocks/{symbol}`・`/us-quotes/{symbol}`／frontend `/us-stocks` スクリーナー＋`/us-stocks/[symbol]` 詳細。YoY は `.info` 提供の実値中継で `op/eps_growth_yoy` は素なく None（捏造しない）。**Phase 7(B-2)（FX 換算/米株保有波及）は実装済み（ADR-057・2026-06-11）**＝FX 基盤（`FxAdapter`・`fx_rates`〔0019〕・夜間 `fetch_fx_rates`）＋米株保有管理（`us_transactions`/`us_holdings`・`recalc_us_holdings`・グローバル保有＝`portfolio_id` なし〔ADR-001〕）＋資産概要合算（`asset_snapshots.us_stock_value`・`/asset-overview` に米国株スライス・`get_us_holdings` Tool〔min_phase=7〕）。JPY 資産評価コア（holdings/cash/optimize）への通貨波及は行わず資産概要レイヤのみで合算（最小スコープ・ADR-031 市場分離維持）。

## ドキュメントの地図（実装前に読む）

| 読むもの | 内容 |
|---|---|
| `README.md` | 全体像・技術スタック・起動手順 |
| `docs/decisions.md` | **ADR-001〜057。なぜそうしたかの全記録。最重要** |
| `docs/architecture.md` | システム構成・2 軸 AI・データフロー・通信/障害/運用 |
| `docs/screens.md` | 画面設計（IA）・ナビ方針・Dashboard 構成・常駐 Advisor チャット・画面コンテキスト |
| `docs/advisor.md` | AI Advisor の設計（CORE/POLICY プロンプト・Tool・手法の扱い） |
| `docs/data-model.md` | DB スキーマ（全テーブル） |
| `docs/api.md` | REST API 契約（Next ↔ FastAPI の境界） |
| `docs/jquants.md` | J-Quants API V2 の認証・プラン・エンドポイント |
| `docs/roadmap.md` | Phase 0〜7。**Phase 1〜4 ＋ Phase 6 ＋ Phase 7(A) ＋ Phase 7(B-1)（米株スクリーナー・提示専用・ADR-055）＋ Phase 7(B-2)（FX/保有波及・ADR-057・2026-06-11）実装済み・Phase 5 は推論経路まで実装済み（backend＋frontend 通し検証済み・実ニュース取得＋Discord 通知＋日米業種リードラグ＋米株スクリーナー＋米株保有 JPY 合算まで完了。Phase 2 backtest 画面接続は 2026-06-08 に完了）。Phase 5 の学習実測・ニュース意味検索系（ADR-045/051/052）・テーマタグ（ADR-050 改訂/ADR-056・段階 A〔米株〕は 2026-06-10・段階 B〔JP 調査済みオーバーレイ〕・段階 C〔EDINET 全ユニバース〕は 2026-06-11 実装済み＝初回バックフィルのみ手動）が残** |
| `tasks/review-2026-06-12.md` | **2026-06-12 全体レビューの未着手残課題の正本**（裏取り済みの実装バグ・設計改善・テスト欠落。docs 不整合は同日修正済み＝同ファイル §4 参照） |

## アーキテクチャの不変条件（default の直感を上書きする。違反しないこと）

これらは ADR で決まった意図的な選択。「普通こうする」を理由に破らないこと。

- **DB に触れるのは FastAPI だけ。Next.js は UI 専用で、データは REST 経由で取る**（ADR-005）。**Prisma は使わない**。Next 側に DB アクセスを足さない。
- **AI に数値を計算させない**。Python が「事実（数字）」を計算し、LLM は Tool Calling で受け取った事実を**解釈・提案するだけ**（ADR-014）。プロンプトに生データを丸投げしない。
- **Advisor チャット（軸2）は全ページ常駐**（フローティング・Next の root layout・ページ遷移で会話保持）（ADR-024）。専用ページに閉じ込めない。
- **画面コンテキストは軽量ヒントのみ**（見ているページ＋主対象）。チャットに画面の数値を載せず、必要時に Tool で取り直す（ADR-025）。ADR-014 と同じ規律。
- **手法（一目均衡表・モメンタム・リードラグ等）は必ずテスト済みコードで実装する**。LLM にその場でコードを書かせて計算させない（再現性・backtest が壊れる＝ADR-016）。「手法 DB」は索引であってコードの代替ではない。
- **システムプロンプトは「不変 CORE（リポジトリの prompt ファイル）＋ 可変 POLICY（DB の `policy`）」に分離**（ADR-015）。CORE はチャットで書き換えない。専門性は CORE・Tool・手法カードに宿す。
- **投資方針 `policy` は単一を育てる**。複数ペルソナ切り替えや版管理機構は作らない。履歴は `advisor_journal` のスナップショットで残す（ADR-013）。
- **J-Quants は V2（`x-api-key` ヘッダー）を使う**。V1 のトークン 2 段階方式（`/v1/...`）は 2026-06-01 に終了済み。ネット上の記事は大半が古い V1 なので流用しない（ADR-008）。J-Quants は**日本株専用**。
- **通知は Discord Webhook**。LINE Notify は終了済みなので使わない（ADR-007）。
- **SQLite（WAL）。DB に触れる OS プロセスは FastAPI に限定**してロック競合を避ける（ADR-002/005）。書き込み系統は夜間バッチ・昼の手入力・チャット/承認の 3 系統だが、同一プロセス内で扱う。再取得で壊れないよう UPSERT で冪等にする。
- **単一ユーザー・認証なし**（ADR-001）。`user_id` を足さない。家庭内 LAN 前提で外部公開しない。
- **データソースはアダプタ越し**（`JQuantsAdapter` / `IndexAdapter` / `UsEquityAdapter` / `NewsAdapter` / `FxAdapter`）。直結ハードコードしない（ADR-010）。
- **銘柄ドシエは DB に保存**（`stock_dossiers` の markdown 列）＋ソース台帳（`dossier_sources`、本文は持たず要約＋URL）。リポジトリ markdown には置かない（AI が頻繁に自動更新するため＝ADR-020）。逆に CORE プロンプト・手法カード（参照知識）は安定資産なのでリポジトリ markdown に置く。
- **重い処理の置き場所**: ML 学習は別 PC（ラズパイは `.pkl` で推論のみ＝ADR-006）。LLM 推論は OpenRouter（クラウド、`.env` で差替可＝ADR-012）。MCP によるニュース取得は昼チャットでは使えるが、**無人 cron では使えないことがある**ので夜は軽め（ADR-020）。

## 開発コマンド

**開発・本番とも Docker Compose で動かす（ADR-021）。** リポジトリ直下で:

```bash
docker compose up    # backend(:8000) ＋ frontend(:3000)。backend/.env 編集で自動リロード
# ポート衝突時: FRONTEND_PORT=3100 BACKEND_PORT=8100 docker compose up
```

コンテナを使わずホスト直で動かす場合（2 プロセス・両方起動）。**backend の依存は uv 管理**（`pip`/`requirements.txt` は使わない＝ADR-023）:

```bash
# Backend (FastAPI) — 別端末から見るため 0.0.0.0 で待ち受け
cd backend && uv sync && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Frontend (Next.js)
cd frontend && npm install && npm run dev
```

- frontend は API を相対パス `/api` で叩き、Next の rewrites（`next.config.ts`・転送先 `BACKEND_ORIGIN`）が裏で backend へ素通しする（同一オリジン化＝ADR-037）。CORS と API_URL 焼き込みは廃止。
- 秘密情報（J-Quants / LLM のキー）は **backend の `.env` のみ**。frontend には渡さない。
- 開発は J-Quants **Free プラン**（株価 12 週間遅延）で進む。評価額・P/L も遅延値になる点に注意。

データ投入・テスト・移行（`backend/` で。Compose なら `docker compose exec backend …`）:

```bash
uv run python -m app.scripts.backfill            # 既定 3 銘柄（7203/6758/9984）を取得
uv run python -m app.scripts.backfill 7203 6758  # 任意銘柄。再実行しても重複しない（冪等 UPSERT）
uv run pytest -q                                  # テスト（DB は触らず一時 SQLite で回る）
uv run ruff check . && uv run ruff format .       # lint / format（ADR-023）
uv run alembic upgrade head                       # スキーマ移行（起動時の init_db とは別に手動でも）
```

frontend は `npm run lint`（Biome）/ `npm run format`（Biome）/ `npm run build`。`dev` のみ Turbopack、`build` は現状 webpack（ADR-022 の「不安定なら webpack へフォールバック」に従った状態）。

## コーディング作法（スキルが正本）

設計の「なぜ」は ADR、**「どう書くか」の作法はプロジェクト固有スキルに一本化した**。実体は `.skills/<name>/SKILL.md`（`.claude/skills` と `.codex/skills` が指すディレクトリ symlink）。**コードを書く前に該当スキルを必ず読む**。スキルと現存コードがズレていたら、既定は**スキルが正**（ドリフトはリファクタで詰める）。ただしスキル自体に改善余地ありと判断したら、黙ってコードへ寄せず**変更案を理由付きで提案し、ユーザーの承認を得てからスキルを更新する**（スキルは正本だが不変ではない＝ブラッシュアップ対象）。

| 何を書く・触るとき | 読むスキル |
|---|---|
| backend 全般（横断作法・レイヤ分離・型・例外・ログ・同期 def） | `backend-foundations` |
| REST ルータ（`app/routers/` ・サブパッケージ router） | `backend-router-pattern` |
| クエリ・Table 定義（`db/repo/` パッケージ ・`db/schema.py`） | `backend-repo-pattern` |
| 数理計算・下ごしらえ（`services/` ・`quant/`） | `backend-service-quant-pattern` |
| 外部 API クライアント（`adapters/`） | `backend-adapter-pattern` |
| 夜間バッチ（`batch/` の runner/jobs/lock/notify） | `batch-pattern` |
| テスト（pytest・一時 SQLite） | `testing-strategy` |
| ページ・コンポーネント・フック・共有 UI（frontend） | `frontend-component-pattern` |
| backend 呼び出し・`lib/api.ts`・型 | `frontend-api-client-pattern` |
| スキル自体の追加・改名・削除 | `project-skill-authoring` |

横断で外さない芯（詳細は各スキル）:
- **コメント・docstring・PR・会話はすべて日本語**。docstring 冒頭に**該当 ADR 番号や `docs/` 参照**を書く（意図の出所を辿れるように）。
- 設計判断を勝手に作らない。迷ったら `docs/` が真実。ズレを見つけたらドキュメント側も直す。
- lint/format で差分を出さない（backend=Ruff、frontend=Biome）。`ignore` する時は**理由をコメントで添える**。
- **AI に数値を計算させない**（quant の純関数が事実を計算・ADR-014/016）。**DB に触るのは FastAPI だけ**（ADR-005）。
- スタイルは **Tailwind v4 トークン（`DESIGN.md`）・density-first**。生の色やマジック値を散らさない。

> AI Advisor（CORE/POLICY プロンプト・Tool）の作法は Phase 3 が固まり次第 `advisor-pattern` スキルとして追記する。

## バージョン管理・言語

- **バージョン管理は Jujutsu（`jj`）を使う**（git ではなく）。コミットは指示があった時だけ行う。
- **ドキュメント・コメント・会話は日本語**で書く。
