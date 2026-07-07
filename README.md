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

## 重要な概念（使う前に知っておくと迷わない語彙）

AssetVane には独特の語彙がいくつかある。**「AI が何を根拠に、どこに何を書くのか」**が分かると使いこなしやすい。以下は使う視点での要約で、設計の「なぜ」は [docs/decisions.md](docs/decisions.md)（ADR）に全て残っている。

### 手法カード と 知識カード（AI の知識源）

AI Advisor の知識は **4 つの資産**に宿る。**CORE**（不変・規律と専門性・リポジトリの `core_prompt.md`）、**POLICY**（可変・あなたの投資方針・DB の `policy`）、そして **知識カード** と **手法カード**。後ろ 2 つは似た名前だが役割が明確に違う。

- **手法カード**（`backend/app/advisor/method_cards/<signal_type>.md`・[ADR-075](docs/decisions.md)）は、**手法の解釈**を書いたもの。「モメンタムのスコアは何を測るか・どう読むか・どこに限界があるか」といった、その手法を正しく使うための注意書きである。リポジトリが所有し、**アプリからも AI からも編集できない**（手法を増やすときは必ず `quant/*.py` のコード変更を伴うので、コードレビュー経由で入る）。AI は必要になった手法だけを `get_method_card` で読み込む（skill 型の progressive disclosure）。**計算そのものは書かない** — 計算は必ずテスト済みの純関数 `quant/*.py` にある（[ADR-014](docs/decisions.md)/[ADR-016](docs/decisions.md)）。
- **知識カード**（DB の `knowledge_cards`＋管理画面 `/cards`・[ADR-062](docs/decisions.md)）は、**非自明な知識の断片**をためる場所。市場文脈・外部メモ・あなた自身の知見など、LLM が一般常識では知らないことを 1 枚 1 トピックで蓄積する。UI・AI・人間の誰でも増やせ、追加時に AI 審査（triage）が下書き（draft）・要 CORE 化・要計算実装・不要・有効候補に振り分ける。ただし**本番の助言に効かせる「active 化」は人間がワンクリックで最終承認する**（[ADR-009](docs/decisions.md)）。関連する場面で意味検索（RAG）により自動で引き出される。

違いをひと言でいうと:

| | 手法カード | 知識カード |
|---|---|---|
| 誰が編集する | リポジトリ（コード変更＋レビュー） | UI / AI / 人間（`/cards`） |
| 何を書く | 手法の解釈（読み方・限界） | 市場文脈・外部メモ・ユーザー知識 |
| どう呼ばれる | `signal_type` を指名して必要時ロード | 意味検索で自動 surface（一部は常時） |

住み分けの原則: **一般的な教科書知識はカードにせず LLM に任せ、規律・ペルソナは CORE に吸収する**。カードに置くのは「LLM が知らない・忘れる・間違える」非自明な知識だけ。

### 投資ジャーナル（advisor_journal）

「**いつ何を考え、方針がどう動いたか**」を日付ごとに残す**判断の履歴**（[ADR-013](docs/decisions.md)/[ADR-029](docs/decisions.md)）。1 件に、その日与えられた事実の要約・AI の所見・提案・そのときの `policy` スナップショットがまとまっている。

夜の分析 AI が毎晩 1 件書き、昼のチャットでも「この会話を残しておこう」と承認すれば書ける。**所見が空っぽの晩（実質何もしなかった晩）は書かない**という不変条件がある（[ADR-018](docs/decisions.md)）。投資方針 `policy` に別途の版管理機構は作らず、**方針変更の履歴はこのジャーナルのスナップショットが担う**（[ADR-013](docs/decisions.md)）。なお、生のチャットのやり取り自体は保存されず（ブラウザ側に一時保持）、**残す価値のある要点だけを承認してジャーナルに昇格**させる二層構造になっている（[ADR-029](docs/decisions.md)）。

### 銘柄ドシエ（stock dossier）

1 銘柄につき 1 つの、**継続的に更新される調査レポート**（living document・[ADR-020](docs/decisions.md)）。ニュースや財務を読んだ定性ファンダを markdown で持ち、「**この銘柄は今どうか**」を上書きし続ける。同じ調査パイプライン `investigate_stock` を、夜（ウォッチリストを銘柄ごとの調査間隔で自動巡回）と昼のチャット（「この銘柄を調べて」）の両方から呼ぶ。

**個別銘柄の「知識ノート」との違い**（よく混同する点）: ドシエは「**今の事実／現況**」で、調査のたびに**上書き**される（前の内容は消える）。一方、知識カードの銘柄版（知識ノート）は「**耐久的な解釈やアノマリー**」で、上書きされず**積み上がる**（承認制）。用途が逆向き（現況をノートに、恒久的な知見をドシエに書こうとする等）なら、AI が書く前に置き場所を提案してくれる。

> 補足: 調査で参照したソースの台帳（本文は持たず要約＋URL のみ）は、統合ニュースコーパス `news` に統合済み（[ADR-044](docs/decisions.md)）。独立した台帳テーブルは今はない。

### AI Advisor（2 軸の AI・製品の核心）

投資判断の相談役となる AI で、**性格の違う 2 つの軸**を持つ。

- **夜の分析 AI**（cron 起動・画面を持たない）: 前日までの `policy` と今日の事実（signals・ポートフォリオ・資産・ニュース）を突き合わせ、方針の見直しを提案してジャーナルを 1 件書く。方針変更や売買は**承認制**で起票するだけで、**発注はしない**。
- **相談チャット AI**（全ページ常駐のフローティング＋専用の大画面 `/advisor`・[ADR-024](docs/decisions.md)/[ADR-065](docs/decisions.md)）: 「見ているページと主対象」という**軽いヒントだけ**を受け取り、「これ」「この銘柄」といった指示語を解決する。**画面の数値そのものは渡さず、必要なら AI が Tool で取り直す**（[ADR-025](docs/decisions.md)）。相談して合意すれば `policy` を更新する。

両軸に共通する芯: **AI は数字を計算しない**。Python が事実（数字）を計算し、AI は Tool Calling でそれを受け取って**解釈・提案するだけ**（[ADR-014](docs/decisions.md)/[ADR-016](docs/decisions.md)）。使える Tool は開発 Phase に応じて段階解放される（現在 Phase 7）。LLM のプロバイダ・モデルは、面（チャット／夜間分析／銘柄調査／タグ付け／カード審査）ごとに `/settings` 画面で割り当てる（[ADR-058](docs/decisions.md)/[ADR-059](docs/decisions.md)）。

### シグナル（signals）

夜間バッチが（対象銘柄について）計算する「**銘柄の兆候**」。テクニカル・出来高・機械学習・業種波及の各手法が、その日の銘柄にスコアを付けて `signals` テーブルに焼く。核心機能の **Trend Vane**（上昇気流の銘柄スクリーニング）と **Signal Beacon**（Discord 通知）は、このシグナルの上に乗っている。画面では `GET /signals`、手動再計算は `POST /batch/run` から辿れる。

種類（`signal_type`）は現在いくつかある:

- **`momentum`**: 移動平均（SMA25/75）と RSI で上昇の勢いを測る。ゴールデンクロスが起きた当日は加点される（ゴールデンクロスは独立したシグナルではなく `momentum` の一要素）。
- **`volume_spike`**: 出来高の急増。
- **`stealth_accum`**: 機関投資家のステルス的な仕込みの兆候（[ADR-074](docs/decisions.md)）。
- **`ai_alpha`**: 決算・財務を機械学習でスコア化（AI Alpha Scorer・Phase 5）。
- **`lead_lag`**: 米国業種ショックの翌日波及から日本業種を採点（Sector Lead-Lag・Phase 7・[ADR-039](docs/decisions.md)）。

各シグナルの**スコアの読み方や限界**は、対応する **手法カード**（上記）に書いてあり、AI は `get_method_card` で必要なときだけ読む。スコアの数値そのものは Python の純関数が計算し、AI はそれを解釈するだけ（[ADR-014](docs/decisions.md)/[ADR-016](docs/decisions.md)）。

### その他おさえておくと良い用語

- **policy（投資方針）**: AI と一緒に育てていく**単一**の方針。複数ペルソナの切り替えはせず、CORE を操縦するハンドルとして扱う。
- **proposals（提案）／ notable_picks（注目銘柄）**: proposals は買い・売り・方針変更・リバランスの**承認制**の起票で、承認しても**状態が変わるだけで発注はしない**（提示専用・[ADR-009](docs/decisions.md)）。notable_picks は夜の通知（digest）向けに AI が選ぶ「今夜の注目銘柄」で、複数の材料が重なった候補（合流ゲート）に絞ってから選別する（[ADR-067](docs/decisions.md)）。
- **Phase（フェーズ）**: 開発段階のこと。Phase が上がるほど使える Tool と手法カードが増え、Advisor ができることが広がる（各 Tool の `min_phase` で段階解放）。

> **全体の流れ**: 夜間バッチが J-Quants・米株・ニュース等を取り込み、`quant` の純関数が「事実」を計算して signals などに焼く。夜になると AI がその事実と `policy` を突き合わせ、Tool 越しに解釈して提案とジャーナルを書き、Discord に通知する。昼はチャット AI が同じ Tool 群を使って根拠付きで相談に応じる — **データが事実になり、AI がそれを解釈して提案に変える**、という一巡が毎日回る。

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
| [docs/decisions.md](docs/decisions.md) | 主要な設計判断とその理由（ADR-001〜086） |
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

> ℹ️ J-Quants のプランは **`/settings` の WebUI（DB 保存）で管理**する（[ADR-061](docs/decisions.md)）。**現在は Light プラン（遅延なし）で運用**。ロジックはプラン非依存なので Free/Light/Standard/Premium いずれでも同じコードが動く。データが遅延しているかは契約プランの仮定でなく `as_of` の鮮度で判定する（[ADR-071](docs/decisions.md)）。

### 4. LLM プロバイダ・面別 model を設定する（[ADR-058](docs/decisions.md)）

LLM の provider・API キー・base_url・model と**面別割当**は **`/settings` の WebUI で登録・編集**する（DB 保存）。OpenAI 互換 API を複数登録でき（OpenRouter / OpenAI 直 / ローカル LLM / Sakana 等）、**面（chat/nightly/dossier/tagger）ごとに provider と model を割り当てられる**（例: チャットは Claude Opus 4.8、夜間AI は安価な強モデル、タグ付けは安い高速モデル）。provider は OpenAI 互換のみ（codex 経路は [ADR-073](docs/decisions.md) で撤去）。

> ⚠️ **migration 後、初回は `/settings` で provider を登録するまで LLM は動かない**（chat は 503・夜間/ドシエは通知付き skip・タグ付けは沈黙 skip）。シードしないので初回は手動登録が要る。

---

## 便利コマンド（Makefile）

よく使う運用・開発コマンドを `Makefile` に集約した。**運用ターゲットは dev でも Pi 本番でも同じコマンドで動く**（compose ファイルの違いは Makefile が自動判定で吸収する。dev=`compose.yaml`／Pi=`compose.prod.yaml`）。Pi 本番では `make deploy` で Makefile も配られるので、ラズパイに `ssh` してそのまま叩ける。

| コマンド | 内容 | 実行場所 |
|---|---|---|
| `make up` | front/back をバックグラウンド起動（初回は build/pull も走る） | dev / Pi 共通 |
| `make down` | front/back を停止してコンテナ/ネットワークを削除（`./data` は bind mount なので残る） | dev / Pi 共通 |
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
