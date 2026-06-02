# 🧭 AssetVane (アセットベイン)

> **"Read the market wind, optimize your wealth."**
> J-Quants API と数理モデルを活用し、相場の風向き（トレンド）を検知してポートフォリオを最適化する、個人投資家のための投資ダッシュボード。

---

## Overview

AssetVane は、**自動売買を目的とせず**、データ分析に基づいて「短期的な強い銘柄のスクリーニング」と「中長期のポートフォリオ最適化（リバランス）」を行うための、**自分専用（単一ユーザー）**の投資ダッシュボードアプリです。

超軽量・常時起動の計算サーバー（Raspberry Pi）が毎晩データを自動収集・解析し、Next.js で構築されたモダン UI が、あなたの投資判断を強力にサポートします。**シグナルは「提示」するだけで、発注はあなた自身が手で行います。**

### 核心機能（Core Features）

- 📈 **Trend Vane**（短期モメンタム検知）: テクニカル分析と出来高急増シグナルから、上昇気流に乗った銘柄を全自動スクリーニング。
- ⚖️ **Portfolio Optimizer**（資産比率最適化）: 現代ポートフォリオ理論に基づき、保有銘柄のリスクを最小化しリターンを最大化するリバランス比率を数理最適化で提案。
- 📊 **AI Alpha Scorer**（財務・決算スコアリング）: J-Quants の決算データを機械学習モデルに読み込ませ、業績に対して株価が歪んでいる「隠れた本命株」をサーチ。
- 🧭 **Sector Lead-Lag**（日米業種リードラグ）: 米国市場の業種別ショックが翌営業日の日本市場に波及する効果を、部分空間正則化付き PCA で捉え、翌日強含む日本業種をスコアリング（[研究背景](docs/roadmap.md#phase-5-sector-lead-lag日米業種リードラグ)）。
- 🔔 **Signal Beacon**（Discord 通知）: 毎朝の相場開始前に、スクリーニング結果やリバランスのタイミングを自動通知。

---

## Architecture

モダンな Web フロントエンドと、データサイエンスに特化した Python バックエンドを分離した構成です。Raspberry Pi 4B などの省電力環境で常時起動・夜間バッチ運用することを前提に最適化しています。

```
[ブラウザ]
   │ HTTP/JSON
[Next.js (フロントエンド / UIのみ)]
   │ REST (HTTP/JSON)
[FastAPI (Python / データ・計算・DBの単一所有者)]
   │
   ├─ SQLite (WALモード) … 唯一の書き手はFastAPI
   ├─ 夜間バッチ (cron) … データ取得→指標計算→signals保存
   ├─ データソース・アダプタ
   │     ├─ J-Quants API (V2) … 日本株・ETFの日足/財務
   │     └─ 米国ETFソース (Stooq等) … リードラグ戦略用
   └─ Discord Webhook … 通知
```

**重要な設計方針**: DB に触れるのは **FastAPI のみ**です。Next.js は DB を直接触らず、すべて FastAPI の REST API 経由でデータを取得します（理由は [docs/decisions.md](docs/decisions.md) を参照）。

### 🛠️ Technical Stack

| レイヤー | 採用技術 |
|---|---|
| フロントエンド | Next.js (App Router), TypeScript, Tailwind CSS, Shadcn UI |
| バックエンド | Python 3.11+, FastAPI |
| 数理・分析 | Pandas, NumPy, PyPortfolioOpt, TA-Lib, LightGBM |
| データベース | SQLite (WALモード) |
| データソース | J-Quants API **V2**（日本株）, Stooq 等（米国ETF） |
| 通知 | Discord Webhook |
| インフラ | Raspberry Pi 4B (8GB推奨) ローカル運用 / ML学習時のみ別PC |

詳細は [docs/architecture.md](docs/architecture.md) を参照してください。

---

## Documentation

| ドキュメント | 内容 |
|---|---|
| [docs/architecture.md](docs/architecture.md) | システム構成・データフロー・ディレクトリ構成 |
| [docs/decisions.md](docs/decisions.md) | 主要な設計判断とその理由（ADR） |
| [docs/data-model.md](docs/data-model.md) | DB スキーマ・J-Quants データ対応 |
| [docs/jquants.md](docs/jquants.md) | J-Quants API V2 の認証・エンドポイント・プラン |
| [docs/roadmap.md](docs/roadmap.md) | Phase 0〜5 の開発ロードマップ |

---

## Getting Started (Development)

### 1. Prerequisites

- Node.js (v18+)
- Python (3.11+)
- J-Quants API のアカウントおよび **API キー**（[V2](docs/jquants.md) 方式）

### 2. Backend Setup (Python / FastAPI)

```bash
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp ../.env.example .env      # .env に J-Quants の API キー等を記入
```

### 3. Frontend Setup (Next.js)

```bash
cd frontend
npm install
npm run dev
```

> ℹ️ 開発は J-Quants **Free プラン**（株価12週間遅延・約2年分）で進められます。ロジックはプラン非依存なので、実運用時に Light 以上へ切り替えれば同じコードが最新データで動きます。

---

## License

This project is licensed under the MIT License - see the LICENSE file for details.
