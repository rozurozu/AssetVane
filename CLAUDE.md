# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## このプロジェクトについて

**AssetVane** は、日米の株式を分析し、**AI と投資方針を相談しながら銘柄・配分を提案する**、個人投資家 1 人用の投資ダッシュボード。自動売買はせず、提示に徹する。

**現状は「Phase 1〜4（Trend Vane / Portfolio Optimizer / AI Advisor / Stock Dossier）＋ Phase 6（Signal Beacon 通知）着工済み。backend＋frontend が縦に通し検証済み（Phase 4 は 2026-06-05・Phase 6 は 2026-06-06 に実機で Discord digest 到達・冪等まで確認）」**。設計の真実は `docs/` にある。実装を始める前に必ず `docs/` を読むこと。

- **実装済み（Phase 0〜4 の backend＋frontend が縦に通る）**: Phase 0 の縦スライス（`JQuantsAdapter` V2 → SQLite `stocks`/`daily_quotes` → `/stocks`・`/quotes` → frontend の実データローソク足）に加え、**Phase 1**（`batch/` の夜間バッチ runner/lock/notify ＋ 7 ジョブ・`signals` テーブルと momentum/volume_spike・`GET /signals`・`POST /batch/run`・APScheduler 同居 cron）、**Phase 2**（`portfolios`/`holdings`/`transactions`/`cash`/`external_assets`/`asset_snapshots`・相関／PyPortfolioOpt 最適化／backtest・`/holdings`・`/transactions`・`/portfolio/{id}/metrics`・`/optimize`・`/asset-overview`）、**Phase 3**（AI Advisor 2 軸・**Tool Calling 接続済み**・`submit_journal`・`policy`/`advisor_journal`/`proposals` の承認制提案）、**Phase 4**（Stock Dossier＝`watchlist`/`stock_dossiers`/`dossier_sources`・`investigate_stock` パイプライン・3 Tool〔min_phase=4〕・`/watchlist`・`/dossiers/{code}`・夜間巡回ジョブ・frontend の `/watchlist` ページ＋`DossierSection`。`fetch_news` も実ニュース源を実装済み＝`NewsAdapter`〔Google News RSS → httpx＋trafilatura で本文抽出 → AI 要約・**昼 MCP／夜 httpx の 2 系統は撤回し httpx 一本に**＝ADR-020 改訂〕・銘柄別の調査 cadence〔`interval_days`＋夜あたり天井・ADR-033〕）まで実装済み。`init_db`=`alembic upgrade head`（0009 まで）・pytest 333 件・`/health`・config・Docker Compose・App Router シェルも稼働。**手法（momentum/volume_spike 等）は TA-Lib を使わず自前 quant 純関数で実装**（ADR-016）。
- **残・次の山**: 全銘柄バッチの本番投入（実 J-Quants・日付一括取得・初回バックフィル所要の実測）、**Phase 3 の LLM 障害時フォールバックの詰め**（API エラー・空応答・Tool 不呼び出し時に observations/journal を欠かさず Discord 通知＝ADR-018 の堅牢化。コード側は coerce/フォールバック対応済み）。**LLM は本番＝クラウド強モデル前提（ADR-012：Tool Calling 確実な品質帯）。ローカル弱モデル（qwen3.5:9b 等）は開発時の動作確認用で、弱モデルに Tool を確実に呼ばせる作り込みはしない＝できないことは割り切る**。frontend は signals／ポートフォリオ／取引／スクリーナー／policy／journal／proposals／Dashboard 本体・常駐 Advisor チャット（画面コンテキスト注入込み）・Phase 4 の `/watchlist`＋銘柄詳細の `DossierSection` まで backend と配線され実データ描画する（Dashboard の watchlist も Phase 4 で実配線済み）。**Phase 4 は実ニュース取得まで完了**（`fetch_news`／`NewsAdapter` 稼働）。**一般ニュースダイジェスト（銘柄に紐づかない別系統・ADR-034）も実装済み**（`general_news` テーブル〔0011〕／夜間ジョブ `fetch_general_news`〔`run_advisor` 直前〕／Tool `get_general_news`〔min_phase=4・軸1/軸2 共用〕／`GET /general-news`／Dashboard の `GeneralNewsWidget`。カテゴリ定義は定数モジュール `general_news_config.py`）。あわせて `CURRENT_PHASE` を 3→4 に修正（Phase 4 完了時の上げ忘れ＝min_phase=4 の dossier 系 Tool もチャット・夜AI に露出）。**Phase 6（Signal Beacon 通知）は実装＋実機検証済み**（夜間バッチ末尾の `notify_digest` が⑦⑧＋夜AI 提案を 1 通の Discord digest に束ね、`notifications` テーブル＋`send_once` で冪等化。`/settings` 画面も配線・phase6-spec.md）。Phase 5（AI Alpha Scorer）・Phase 7（Sector Lead-Lag）は未着手。

## ドキュメントの地図（実装前に読む）

| 読むもの | 内容 |
|---|---|
| `README.md` | 全体像・技術スタック・起動手順 |
| `docs/decisions.md` | **ADR-001〜034。なぜそうしたかの全記録。最重要** |
| `docs/architecture.md` | システム構成・2 軸 AI・データフロー・通信/障害/運用 |
| `docs/screens.md` | 画面設計（IA）・ナビ方針・Dashboard 構成・常駐 Advisor チャット・画面コンテキスト |
| `docs/advisor.md` | AI Advisor の設計（CORE/POLICY プロンプト・Tool・手法の扱い） |
| `docs/data-model.md` | DB スキーマ（全テーブル） |
| `docs/api.md` | REST API 契約（Next ↔ FastAPI の境界） |
| `docs/jquants.md` | J-Quants API V2 の認証・プラン・エンドポイント |
| `docs/roadmap.md` | Phase 0〜7。**Phase 1〜4 ＋ Phase 6 実装済み（backend＋frontend 通し検証済み・実ニュース取得＋Discord 通知まで完了）。全銘柄バッチ本番投入の所要実測・Phase 3 フォールバック堅牢化が残。Phase 5・7 未着手** |

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

- 接続先は frontend の `NEXT_PUBLIC_API_BASE_URL`、CORS は backend の `.env` の `CORS_ALLOW_ORIGINS`。
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
| クエリ・Table 定義（`db/repo.py` ・`db/schema.py`） | `backend-repo-pattern` |
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
