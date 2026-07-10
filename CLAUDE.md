# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## このプロジェクトについて

**AssetVane** は、日米の株式を分析し、**AI と投資方針を相談しながら銘柄・配分を提案する**、個人投資家 1 人用の投資ダッシュボード。自動売買はせず、提示に徹する。

**現状は「Phase 1〜4（Trend Vane / Portfolio Optimizer / AI Advisor / Stock Dossier）＋ Phase 6（Signal Beacon 通知）＋ Phase 7(A)（Sector Lead-Lag）着工済み・Phase 5（AI Alpha Scorer）は推論経路＋初回学習まで実装済み（ADR-066＝Mac コンテナで初回学習を実測・`.pkl` 配置済み）。backend＋frontend が縦に通し検証済み（Phase 4 は 2026-06-05・Phase 6 は 2026-06-06 に実機で Discord digest 到達・冪等まで確認）」**。設計の真実は `docs/` にある。実装を始める前に必ず `docs/` を読むこと。

- **実装済み機能の詳細・残課題・次回運用時の確認事項は `docs/status.md` が正本**（本節にあった長文ステータスを 2026-07-10 に移設。以後の実装状況の追記も `docs/status.md` へ）。設計判断の「なぜ」は `docs/decisions.md`（ADR-001〜092）。
- **横断の要点**: `CURRENT_PHASE=7`・migration head=0044・pytest 約 1275 green。自己改善ループ／AI 判断改善の各機能はコード green だが、**実 LLM E2E は dev の LLM 未設定（提案 0 件）が共通ボトルネック**＝解錠口は ADR-092（`POST /batch/run-advisor`＋`advisor_turns`・`/advisor-turns`）。

## ドキュメントの地図（実装前に読む）

| 読むもの | 内容 |
|---|---|
| `README.md` | 全体像・技術スタック・起動手順 |
| `docs/decisions.md` | **ADR-001〜092。なぜそうしたかの全記録。最重要** |
| `docs/architecture.md` | システム構成・2 軸 AI・データフロー・通信/障害/運用 |
| `docs/screens.md` | 画面設計（IA）・ナビ方針・Dashboard 構成・常駐 Advisor チャット・画面コンテキスト |
| `docs/advisor.md` | AI Advisor の設計（CORE/POLICY プロンプト・Tool・手法の扱い） |
| `docs/data-model.md` | DB スキーマ（全テーブル） |
| `docs/api.md` | REST API 契約（Next ↔ FastAPI の境界） |
| `docs/jquants.md` | J-Quants API V2 の認証・プラン・エンドポイント |
| `docs/roadmap.md` | Phase 0〜7。**Phase 1〜4 ＋ Phase 6 ＋ Phase 7(A) ＋ Phase 7(B-1)（米株スクリーナー・提示専用・ADR-055）＋ Phase 7(B-2)（FX/保有波及・ADR-057・2026-06-11）実装済み・Phase 5 は推論経路＋初回学習まで実装済み（ADR-066＝Mac コンテナで実測・`.pkl` 配置。backend＋frontend 通し検証済み・実ニュース取得＋Discord 通知＋日米業種リードラグ＋米株スクリーナー＋米株保有 JPY 合算まで完了。Phase 2 backtest 画面接続は 2026-06-08 に完了）。ニュース意味検索系（ADR-045/051/052）・テーマタグ（ADR-050 改訂/ADR-056・段階 A〔米株〕は 2026-06-10・段階 B〔JP 調査済みオーバーレイ〕・段階 C〔EDINET 全ユニバース〕は 2026-06-11 実装済み＝初回バックフィルのみ手動）が残** |
| `docs/status.md` | **実装ステータスの正本**（フェーズ別の実装済み詳細・残課題・次回運用時の確認事項。旧 CLAUDE.md 冒頭の長文を 2026-07-10 移設） |
| `tasks/review-2026-06-12.md` | **2026-06-12 全体レビューの未着手残課題の正本**（裏取り済みの実装バグ・設計改善・テスト欠落。docs 不整合は同日修正済み＝同ファイル §4 参照） |
| `tasks/review-2026-07-01.md` | **2026-07-01 全体レビュー結果の正本**（ADR-062〜067 新機能の穴を中心に 30 所見を ATDD＋ADR 同期で修正済み＝目玉は共通 `_upsert` の NULL 上書き族 #1/#7/#8。#25 のみ理由付きで見送り） |

## アーキテクチャの不変条件（default の直感を上書きする。違反しないこと）

これらは ADR で決まった意図的な選択。「普通こうする」を理由に破らないこと。

- **DB に触れるのは FastAPI だけ。Next.js は UI 専用で、データは REST 経由で取る**（ADR-005）。**Prisma は使わない**。Next 側に DB アクセスを足さない。
- **AI に数値を計算させない**。Python が「事実（数字）」を計算し、LLM は Tool Calling で受け取った事実を**解釈・提案するだけ**（ADR-014）。プロンプトに生データを丸投げしない。
- **Advisor チャット（軸2）は全ページ常駐**（フローティング・Next の root layout・ページ遷移で会話保持）（ADR-024）。フローティングを廃して専用ページに閉じ込めることはしない。ただし ADR-065 で**専用大画面ページ `/advisor` を追加済み**＝会話状態を Context（`AdvisorChatProvider`）で共有し、フローティングと `/advisor` は同一会話（`/advisor` 表示中だけフローティングを隠す）。会話本体は `ChatConversation` に抽出して共用する。
- **画面コンテキストは軽量ヒントのみ**（見ているページ＋主対象）。チャットに画面の数値を載せず、必要時に Tool で取り直す（ADR-025）。ADR-014 と同じ規律。
- **手法（一目均衡表・モメンタム・リードラグ等）は必ずテスト済みコードで実装する**。LLM にその場でコードを書かせて計算させない（再現性・backtest が壊れる＝ADR-016）。「手法 DB」は索引であってコードの代替ではない。
- **システムプロンプトは「不変 CORE（リポジトリの prompt ファイル）＋ 可変 POLICY（DB の `policy`）」に分離**（ADR-015）。CORE はチャットで書き換えない。専門性は CORE・Tool・手法カードに宿す。
- **投資方針 `policy` は単一を育てる**。複数ペルソナ切り替えや版管理機構は作らない。履歴は `advisor_journal` のスナップショットで残す（ADR-013）。
- **J-Quants は V2（`x-api-key` ヘッダー）を使う**。V1 のトークン 2 段階方式（`/v1/...`）は 2026-06-01 に終了済み。ネット上の記事は大半が古い V1 なので流用しない（ADR-008）。J-Quants は**日本株専用**。**API キーと契約プランは env ではなく DB（`jquants_config`〔0024〕）＋ `/settings` の WebUI で管理する＝ADR-061**。`JQuantsAdapter` は settings を読まず `services/jquants_config.build_jquants_adapter`（DB 解決ファクトリ）から `api_key`/`plan` を渡される。`JQUANTS_API_KEY`/`JQUANTS_PLAN` env は撤去。
- **通知は Discord Webhook**。LINE Notify は終了済みなので使わない（ADR-007）。
- **SQLite（WAL）。DB に触れる OS プロセスは FastAPI に限定**してロック競合を避ける（ADR-002/005）。書き込み系統は夜間バッチ・昼の手入力・チャット/承認の 3 系統だが、同一プロセス内で扱う。再取得で壊れないよう UPSERT で冪等にする。**dev の DB は named volume `assetvane-db` に載せる＝bind mount にしない**（macOS Docker Desktop の gRPC-FUSE/virtiofs 上で WAL/mmap が壊れ 2026-06-22 に実際に破損した＝ADR-060。prod ラズパイはネイティブ Linux で bind mount 維持）。named volume はホストから素見えしないのでバックアップ/復元は `make db-backup`/`db-restore`。
- **単一ユーザー・認証なし**（ADR-001）。`user_id` を足さない。家庭内 LAN 前提で外部公開しない。
- **データソースはアダプタ越し**（`JQuantsAdapter` / `IndexAdapter` / `UsEquityAdapter` / `NewsAdapter` / `FxAdapter`）。直結ハードコードしない（ADR-010）。
- **銘柄ドシエは DB に保存**（`stock_dossiers` の markdown 列）＋ソース台帳（`dossier_sources`、本文は持たず要約＋URL）。リポジトリ markdown には置かない（AI が頻繁に自動更新するため＝ADR-020）。逆に CORE プロンプト（規律・ペルソナ）は安定資産なのでリポジトリ markdown に置く（`core_prompt.md`）。**知識（市場文脈・外部メモ・ユーザー知識）は ADR-062 で DB の `knowledge_cards`〔0025〕＋ `/cards` 管理画面・RAG へ移管**（UI で増減・AI 審査 triage で振り分け・active 化は人間承認）。一般教科書知識は LLM に任せ、計算は引き続き `quant/*.py`（＝ADR-014/016）。**手法の解釈（何を測る・スコアの読み方・限界）は ADR-075 で `knowledge_cards` でなく「手法カード＝リポジトリ所有の `app/advisor/method_cards/<key>.md`」に置く**＝アプリ非編集（手法追加はコード変更を伴う）・`get_method_card(key)` でオンデマンド注入（skill 型 progressive disclosure・教科書手法は薄く独自手法は厚く）。**ADR-079 で `kind`（signal|strategy）に一般化**＝`signal`（毎晩 signals に焼く signal_type・ファイル名＝signal_type・ドリフト検査対象）／`strategy`（signal を持たない screen 手法・ファイル名＝スラッグ・ドリフト検査対象外＝清原式 `net_cash_value.md`）。**住み分けは「編集不要の正典＝method_cards／UI で育てる知識＝knowledge_cards」（＝誰が所有し編集するか・ADR-079 が「外部手法は knowledge_cards」を上書き）**。**`knowledge_cards.linked_signal_type` は ADR-075 で非推奨化し 0035 で DROP 済み**（対応は method_cards がファイル名キーで持つ）。作法は skill `method-card-authoring`。
- **重い処理の置き場所**: ML 学習は別 PC（開発機 Mac の Docker コンテナで実行＝ADR-006 を ADR-066 で具体化・スペック要時は GPU ゲーミングPC。ラズパイは `.pkl` で推論のみ＝ADR-006）。LLM 推論は OpenRouter（クラウド、`.env` で差替可＝ADR-012）。MCP によるニュース取得は昼チャットでは使えるが、**無人 cron では使えないことがある**ので夜は軽め（ADR-020）。

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
- 秘密情報のうち **LLM・J-Quants・EDINET DB（edinetdb.jp）・公式 EDINET のキーは DB（`/settings` で編集）に移管済み**（ADR-058/059/061/064/087）。env に残る秘密（Discord Webhook 等）は **backend の `.env` のみ**。いずれも frontend には渡さない。
- J-Quants のプランは DB（`jquants_config`・`/settings` で編集）で管理する（ADR-061）。**現在は Light プラン（遅延なし）で運用**。ロジックはプラン非依存で、データが遅延しているかは契約プランの仮定でなく `as_of` の鮮度で判定する（ADR-071）。

データ投入・テスト・移行（`backend/` で。Compose なら `docker compose exec backend …`）:

```bash
uv run python -m app.scripts.backfill            # 既定 3 銘柄（7203/6758/9984）を取得
uv run python -m app.scripts.backfill 7203 6758  # 任意銘柄。再実行しても重複しない（冪等 UPSERT）
uv run pytest -q                                  # テスト（DB は触らず一時 SQLite で回る）
uv run ruff check . && uv run ruff format .       # lint / format（ADR-023）
uv run alembic upgrade head                       # スキーマ移行（起動時の init_db とは別に手動でも）
```

**dev でバッチ稼働中の操作に注意（ADR-070）**: `POST /batch/stop`（停止）は `data/batch.stop` ファイルで効くので、`--reload` で分裂したプロセスや CLI 起動でも走行中バッチに届く（メモリ旗だった旧実装は届かなかった）。長尺ジョブ（`fetch_quotes` 等）は `stop_aware` で最内ループも見るので数秒〜1営業日で止まる。ただし**バッチ中にソース編集や `uv run`（bytecode を pre-compile しリロードを誘発）をすると、走行中バッチが古プロセスに取り残される**（orphan・named volume＋WAL なので破損はしないが、UI の `running` は false に見え停止ボタンが出ない）。**編集したければ stop→編集→再開**が安全。稼働中の状態確認は reload を誘発しない urllib 直叩き（`http://localhost:8000/batch/status`）で。

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
| AI Advisor の Tool（`app/advisor/tools/` の registry/schemas/handlers） | `advisor-tool-pattern` |
| 手法カード（`app/advisor/method_cards/` ・新 signal_type/手法の解釈文脈・`get_method_card`） | `method-card-authoring` |
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

> AI Advisor の **Tool**（`app/advisor/tools/` の registry/schemas/handlers）の作法は `advisor-tool-pattern` に切り出した。**CORE/POLICY プロンプト**そのものの作法は将来 `advisor-pattern` として追記する。

## バージョン管理・言語

- **バージョン管理は Jujutsu（`jj`）を使う**（git ではなく）。コミットは指示があった時だけ行う。
- **ドキュメント・コメント・会話は日本語**で書く。
