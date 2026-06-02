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
- 🧠 **AI Advisor**（2軸のAIアドバイザー / 製品の核心）: 「**夜の分析AI**」が毎晩、昨日までの方針と今日の状況を突き合わせて方針を見直し提案し、「**相談チャットAI**」がダッシュボード上で投資方針を一緒に調整し、銘柄・比率を提案する。AI は計算せず、Python が計算した事実だけを根拠に判断する（Tool Calling）。
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
   ├─ 数理計算（PyPortfolioOpt / TA-Lib / LightGBM）… "事実"を計算
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
| フロントエンド | Next.js (App Router), TypeScript, Tailwind CSS, Shadcn UI |
| バックエンド | Python 3.11+, FastAPI |
| 数理・分析 | Pandas, NumPy, PyPortfolioOpt, TA-Lib, LightGBM |
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
| [docs/advisor.md](docs/advisor.md) | AI Advisor 設計（CORE/POLICYプロンプト・Tool・相談フロー）|
| [docs/api.md](docs/api.md) | REST API 契約（Next ↔ FastAPI の境界）|
| [docs/decisions.md](docs/decisions.md) | 主要な設計判断とその理由（ADR-001〜020） |
| [docs/data-model.md](docs/data-model.md) | DB スキーマ・J-Quants データ対応 |
| [docs/jquants.md](docs/jquants.md) | J-Quants API V2 の認証・エンドポイント・プラン |
| [docs/roadmap.md](docs/roadmap.md) | Phase 0〜7 の開発ロードマップ |

---

## Getting Started (Development)

### 1. Prerequisites

- Node.js (v18+)
- Python (3.11+)
- J-Quants API のアカウントおよび **API キー**（[V2](docs/jquants.md) 方式）
- LLM の API キー（OpenRouter 等。`.env` で差し替え可能）

AssetVane は **backend（FastAPI）と frontend（Next.js）の 2 プロセス**で動く。両方を起動する。

### 2. Backend Setup & Run (Python / FastAPI)

```bash
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp ../.env.example .env      # .env に J-Quants / LLM の API キー等を記入
# 別端末のブラウザから見るため 0.0.0.0 で待ち受ける
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

> LLM キーが無くても Phase 0（J-Quants のみ）は起動する。CORS 許可オリジンは `.env` の `CORS_ALLOW_ORIGINS` で指定。

### 3. Frontend Setup & Run (Next.js)

```bash
cd frontend
npm install
echo "NEXT_PUBLIC_API_BASE_URL=http://localhost:8000" > .env.local  # FastAPI の場所
npm run dev
```

> ラズパイで運用する場合は `NEXT_PUBLIC_API_BASE_URL` を `http://raspberrypi.local:8000` 等に。API 契約は [docs/api.md](docs/api.md) を参照。

> ℹ️ 開発は J-Quants **Free プラン**（株価12週間遅延・約2年分）で進められる。ロジックはプラン非依存なので、実運用時に Light 以上へ切り替えれば同じコードが最新データで動く。**Free 期間は評価額・P/L も遅延値**になる点に注意。

---

## License

This project is licensed under the MIT License - see the LICENSE file for details.
