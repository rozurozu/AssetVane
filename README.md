# 🧭 AssetVane (アセットベイン)

> **"Read the market wind, optimize your wealth."**
> 日米の株式データと数理モデルを解析し、**AI と投資方針を相談しながら**、買う銘柄とポートフォリオ比率を提案してくれる、個人投資家のための投資ダッシュボード。

---

## Overview

AssetVane は、**自動売買をしない**、**自分専用（単一ユーザー）**の投資ダッシュボードである。日本株・米国株を分析し、テクニカル／ファンダメンタルズ／ポートフォリオ理論で計算した「客観的な事実」を土台に、**AI が投資判断の相談役**となって方針づくりと銘柄・比率の提案を行う。

最大の特徴は、**結果を見せるだけのビューアではなく、AI が「で、どうすべきか」を一緒に考える**点にある。発注はあくまでユーザー自身が手で行い、AI とシステムは**判断材料の提示と提案**に徹する。

ラズパイ等の省電力環境で常時起動し、毎晩データを自動収集・解析する。

### 核心機能（Core Features）

- 📈 **Trend Vane**（短期モメンタム検知）: テクニカル分析と出来高急増シグナルから、上昇気流に乗った銘柄を全自動スクリーニング。
- ⚖️ **Portfolio Optimizer**（資産比率最適化）: 現代ポートフォリオ理論に基づき、保有銘柄のバランス確認とリバランス比率を提案。
- 🧠 **AI Advisor**（2軸のAIアドバイザー / 製品の核心）: 「**夜の分析AI**」が毎晩、昨日までの方針と今日の状況を突き合わせて方針を見直し提案し、「**相談チャットAI**」が**全ページ常駐のチャット**で（画面を見ながら）投資方針を一緒に調整し、銘柄・比率を提案する。AI は計算せず、Python が計算した事実だけを根拠に判断する（Tool Calling）。
- 📚 **Stock Dossier**（個別銘柄の定性ファンダ調査）: ニュース・財務（将来は適時開示）を読んで銘柄ごとの調査レポートを作り、更新し続ける。夜は watchlist を自動調査、昼はチャットで「この銘柄調査して」。
- 📊 **AI Alpha Scorer**（財務・決算スコアリング）: 決算データを機械学習でスコア化し、業績に対して株価が歪んでいる銘柄をサーチ。
- 🔔 **Signal Beacon**（Discord 通知）: 相場開始前に、スクリーニング結果や AI の提案を自動通知。
- 🧭 **Sector Lead-Lag**（日米業種リードラグ / 後期）: 米国業種ショックの翌日波及を捉えて日本業種をスコアリング（[研究背景](docs/roadmap.md)）。米国株対応もこの段階で拡張。

---

## Architecture

モダンな Web フロントエンドと、データサイエンスに特化した Python バックエンドを分離した構成。常時起動・夜間バッチ運用を前提に最適化している。

```
[ブラウザ]
   │ HTTP/JSON（画面表示 / AIチャット）
[Next.js (フロントエンド / UIのみ)]
   │ REST
[FastAPI (Python / データ・計算・DBの単一所有者)]
   ├─ 数理計算（PyPortfolioOpt / 自前 quant 純関数 / LightGBM）… "事実"を計算
   ├─ AI Advisor（2軸）
   │     ├─ 夜の分析AI（cron起動・方針見直し提案・投資日記）
   │     └─ 相談チャットAI（対話で方針を調整・銘柄/比率提案）
   │     └─ LLMアダプタ（OpenRouter等・差替可）/ Tool Calling
   ├─ データソース・アダプタ（J-Quants V2 ＝日本株 / 将来 米株・指数・FX）
   ├─ SQLite (WAL) … 唯一の書き手はFastAPI
   └─ Discord Webhook … 通知
```

**重要な設計方針**:
- DB に触れるのは **FastAPI のみ**。Next.js は REST 経由でデータを取得する。
- **AI は数字を計算しない**。Python が計算した客観的事実を AI が解釈・提案する（[Tool Calling 原則](docs/decisions.md)）。

### 🛠️ Technical Stack

| レイヤー | 採用技術 |
|---|---|
| フロントエンド | Next.js (App Router), TypeScript, Tailwind CSS v4, Biome（lint/format）。shadcn/ui は任意・未導入 |
| バックエンド | Python 3.12+, FastAPI（uv / Ruff / pyright ＝ADR-023）|
| 数理・分析 | Pandas, NumPy, PyPortfolioOpt, yfinance, LightGBM（手法は TA-Lib を使わず自前 quant 純関数で実装＝ADR-016）|
| AI | LLM（OpenRouter 既定 / Ollama 等ローカルへ差替可）, Tool Calling |
| データベース | SQLite (WALモード) |
| データソース | J-Quants API **V2**（日本株）/ 将来: 米株・主要指数・FX |
| 通知 | Discord Webhook |
| インフラ | Raspberry Pi 4B (8GB推奨) ローカル運用 / 将来 Mac mini でローカルLLM / ML学習時のみ別PC |

詳細は [docs/architecture.md](docs/architecture.md) を参照。

---

## Documentation

| ドキュメント | 内容 |
|---|---|
| [docs/architecture.md](docs/architecture.md) | システム構成・2軸AI・データフロー・通信/障害/運用・ディレクトリ構成 |
| [docs/screens.md](docs/screens.md) | 画面設計（IA）・ナビ方針・Dashboard 構成・常駐 Advisor チャット・画面コンテキスト |
| [docs/advisor.md](docs/advisor.md) | AI Advisor 設計（CORE/POLICYプロンプト・Tool・相談フロー）|
| [docs/api.md](docs/api.md) | REST API 契約（Next ↔ FastAPI の境界）|
| [docs/decisions.md](docs/decisions.md) | 主要な設計判断とその理由（ADR-001〜052） |
| [docs/data-model.md](docs/data-model.md) | DB スキーマ・J-Quants データ対応 |
| [docs/jquants.md](docs/jquants.md) | J-Quants API V2 の認証・エンドポイント・プラン |
| [docs/roadmap.md](docs/roadmap.md) | Phase 0〜7 の開発ロードマップ |
| [docs/deploy.md](docs/deploy.md) | 本番デプロイ運用（Mac arm64 ビルド→ghcr→ラズパイ ssh・`make deploy`）|

---

## Getting Started (Development)

### 1. Prerequisites

- **Docker / Docker Compose**（推奨パス。dev/prod とも Compose で動かす＝[ADR-021](docs/decisions.md)）
- ホスト直で動かす場合のみ: Node.js (v18+) ＋ [uv](https://docs.astral.sh/uv/)（backend の依存・venv 管理＝[ADR-023](docs/decisions.md)）。Python 3.12 は uv が用意する
- J-Quants API のアカウントおよび **API キー**（[V2](docs/jquants.md) 方式）
- LLM の API キー（OpenRouter 等。`.env` で差し替え可能）

AssetVane は **backend（FastAPI）と frontend（Next.js）の 2 プロセス**で動く。

### 2. 推奨: Docker Compose で起動

```bash
cp .env.example backend/.env   # backend/.env に J-Quants / LLM の API キー等を記入
docker compose up              # backend(:8000) ＋ frontend(:3000) を同時起動
# ポート衝突時: FRONTEND_PORT=3100 BACKEND_PORT=8100 docker compose up
```

> 秘密情報（J-Quants / LLM のキー）は **backend/.env のみ**に置く（frontend には渡さない＝[ADR-005](docs/decisions.md)）。frontend は API を相対パス `/api` で叩き、Next の rewrites が裏で backend へ転送する（同一オリジン化＝[ADR-037](docs/decisions.md)。CORS も API_URL 焼き込みも不要）。LLM キーが無くても Phase 0（J-Quants のみ）は起動する。

**データ投入（Phase 0）**: 起動しただけでは DB が空。数銘柄の日足を取得して SQLite に入れる（CLI バックフィル）。Compose の DB はリポジトリ直下 `data/assetvane.db`（named volume）なので、**コンテナ内で**実行する。

```bash
docker compose exec backend uv run python -m app.scripts.backfill   # 既定 3 銘柄（7203/6758/9984）
```

これで `/stocks` と銘柄詳細の株価チャートにデータが出る。再実行しても重複しない（冪等 UPSERT＝[ADR-002](docs/decisions.md)）。Free は 12 週間遅延なので最新は約 3 か月前まで。

> ⚠️ **依存を足したときの落とし穴**: `node_modules` / `.venv` はコンテナの**匿名ボリュームにイメージビルド時へ焼かれる**ため、`package.json` / `pyproject.toml` に依存を足しても起動中のコンテナには入らない（`Module not found` 等）。ソースは bind mount で hot reload されるが、**依存だけは別**。次のどちらかで反映する。
> - 速い: `docker compose exec frontend npm install`（or `backend uv sync`）→ `docker compose restart <svc>`
> - 確実: `docker compose up --build -V`（`-V`＝`--renew-anon-volumes`。ただの `--build` だと古い匿名ボリュームが残って効かない）
>
> lockfile（`package-lock.json` / `uv.lock`）は必ずコミットすること。デプロイ時のイメージビルドが `npm ci` / `uv sync` で**同じ依存を再現**する（dev は exec install で回し、本番はビルドして Pi は pull だけ＝[ADR-021/006](docs/decisions.md)）。

### 3. 代替: ホスト直で起動（2 プロセス）

backend の依存は **uv 管理**（`pip`/`requirements.txt` は使わない）。

```bash
# Backend (FastAPI) — 別端末のブラウザから見るため 0.0.0.0 で待ち受ける
cd backend
cp ../.env.example .env        # .env に J-Quants / LLM の API キー等を記入
uv sync                         # .venv 作成＋依存解決（Python 3.12 も uv が用意）
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

```bash
# Frontend (Next.js) — 別端末で
cd frontend
npm install
npm run dev
```

> ホスト直 dev では Next の rewrites 転送先が `BACKEND_ORIGIN` 未設定で `http://localhost:8000` に落ちるので、backend を同じホストの :8000 で動かしていれば**設定不要**（同一オリジン化＝[ADR-037](docs/decisions.md)）。backend が別ホスト/別ポートなら `BACKEND_ORIGIN=http://host:port npm run dev`。CORS の指定は不要になった。API 契約は [docs/api.md](docs/api.md) を参照。

> ℹ️ 開発は J-Quants **Free プラン**（株価12週間遅延・約2年分）で進められる。ロジックはプラン非依存なので、実運用時に Light 以上へ切り替えれば同じコードが最新データで動く。**Free 期間は評価額・P/L も遅延値**になる点に注意。

### 4. （任意）AI を codex で動かす（API 課金を避ける・[ADR-032](docs/decisions.md)）

AI Advisor は既定で OpenAI 互換 API（OpenRouter 等）を使うが、**面別に `codex` へ切り替えられる**。codex は ChatGPT サブスク認証で動くため **LLM の API 従量課金を避けられる**（限界費用ゼロ）。

```bash
# 1) 一度だけ codex に ChatGPT でログイン（API キー不要。~/.codex/auth.json に保存される）。
#    Docker 利用時はホストに codex を入れなくてよい（イメージに同梱・後述）。コンテナ内で:
#      docker compose exec backend codex login
#    ホストに codex CLI があるなら、ホストで叩いても同じ（auth.json をマウントで共有する）:
codex login

# 2) backend/.env で切り替えたい面だけ codex に（既定は全面 openai）
LLM_PROVIDER_CHAT=codex        # 相談チャットを codex で
# LLM_PROVIDER_NIGHTLY=openai  # 夜間バッチは当面 openai 推奨（無人トークン継続が未実証）
# LLM_PROVIDER_DOSSIER=openai  # ドシエ要約も既定 openai
CODEX_MODEL=gpt-5.5            # codex 側の強モデル
```

仕組み: codex は `codex app-server`（stdio JSON-RPC）として **FastAPI プロセス内に常駐**し、自前 Tool は FastAPI 内の **MCP サーバ（`/mcp`）越し**に呼ぶ（DB に触れるのは FastAPI だけ＝[ADR-005](docs/decisions.md)）。**要 codex-cli 0.136.0 以上**（app-server は experimental protocol）。codex が失敗しても **API へ自動フォールバックしない**（chat は 502・夜間は通知＝[ADR-018](docs/decisions.md)）。

> 🐳 **Docker で使う場合**: codex CLI（rust-v0.137.0・Node 不要の musl バイナリ）は **backend イメージに同梱済み**なので**ホストに codex を入れる必要はない**（[ADR-032](docs/decisions.md)）。必要なのは login 済みの `~/.codex/auth.json` だけで、dev は `compose.yaml` が `${HOME}/.codex` を、本番ラズパイは `compose.prod.yaml` が `/opt/assetvane/.codex` を `/root/.codex` にマウントして読む（codex がリフレッシュで書き換えるため read-write）。本番への auth.json 供給は [docs/deploy.md](docs/deploy.md) を参照。ホスト直起動（前項）で codex を使う場合のみ、ホストに codex CLI を入れて `CODEX_BIN` に絶対パスを指定する。

---

## 便利コマンド（Makefile）

よく使う運用・開発コマンドを `Makefile` に集約した。**運用ターゲットは dev でも Pi 本番でも同じコマンドで動く**（compose ファイルの違いは Makefile が自動判定で吸収する。dev=`compose.yaml`／Pi=`compose.prod.yaml`）。Pi 本番では `make deploy` で Makefile も配られるので、ラズパイに `ssh` してそのまま叩ける。

| コマンド | 内容 | 実行場所 |
|---|---|---|
| `make discord-test` | Discord に疎通テストを 1 通送る（冪等回避＝毎回飛ぶ。digest を待たず通知を確認） | dev / Pi 共通 |
| `make jquants-test` | J-Quants V2 に認証ピングを 1 発投げる（DB 非依存。初回デプロイ前の確認・ADR-036） | dev / Pi 共通 |
| `make batch-full` | 全銘柄フルバックフィルを 1 回流す（初回投入/復旧・約100〜150分・ADR-036） | dev / Pi 共通 |
| `make test` | backend テスト（`uv run pytest -q`・一時 SQLite） | Mac（開発）|
| `make lint` | backend lint（Ruff・ADR-023） | Mac（開発）|
| `make format` | backend format（Ruff・ADR-023） | Mac（開発）|
| `make deploy` | Mac で arm64 ビルド → ghcr.io → ラズパイへデプロイ | Mac 専用 |
| `make deploy-build` | ビルド→push のみ（ラズパイは触らない） | Mac 専用 |

`discord-test`/`jquants-test` の実体は `app.scripts.notify_test`/`app.scripts.jquants_test`（CLI 口）。同じ脳を REST（`POST /diagnostics/discord-test`・`/diagnostics/jquants-test`）と `/settings` 画面のボタンからも叩ける（ADR-011「1つの脳・複数の起動口」）。バッチは `/settings` でフル取得（確認ダイアログ付き）・進捗・停止まで操作でき、`GET /batch/status`・`POST /batch/stop` が裏側（ADR-036）。`make deploy`/`deploy-build` は Pi で誤実行すると 1 行ガードで止まる（Mac から実行する）。

---

## Deployment (Production)

本番（ラズパイ 4B・aarch64・家庭内 LAN）へは **Mac（Apple Silicon）で `linux/arm64` をネイティブビルド → `ghcr.io` に push → 同一 LAN のラズパイへ `ssh` で `compose pull → up`** する。1 コマンド:

```bash
make deploy        # = scripts/deploy.sh（build→push→backup→pull→up→health→自動ロールバック）
make deploy-build  # ビルド→push のみ
```

GitHub Actions は使わない（クラウド x86 の QEMU エミュ回避＋家庭内ラズパイへの到達性＝[ADR-035](docs/decisions.md)）。初回セットアップ（ラズパイの `docker login ghcr.io`・`backend/.env` 配置）、タグ運用、ロールバック、バックアップ／復元の手順は **[docs/deploy.md](docs/deploy.md)** を参照。

---

## License

This project is licensed under the MIT License - see the LICENSE file for details.
