# 🧭 AssetVane (アセットベイン)

> **"Read the market wind, optimize your wealth."**
> J-Quants API と数理モデルを活用し、相場の風向き（トレンド）を検知してポートフォリオを最適化する、個人投資家のための投資ダッシュボード。

---

## Overview

AssetVane は、自動売買を目的とせず、データ分析に基づいて「短期的な強い銘柄のスクリーニング」と「中長期のポートフォリオ最適化（リバランス）」を行うための自己運用型投資ダッシュボードアプリです。

超軽量・常時起動の計算サーバー（Raspberry Pi）が毎晩データを自動収集・解析し、Next.jsで構築された美しいモダンUIが、あなたの投資判断を強力にサポートします。

### 核心機能（Core Features）
- 📈 Trend Vane (短期モメンタム検知): TA-Libを用いたテクニカル分析と出来高急増シグナルから、今まさに上昇気流に乗った銘柄を全自動スクリーニング。
- ⚖️ Portfolio Optimizer (資産比率最適化): 現代ポートフォリオ理論に基づき、保有銘柄のリスクを最小化しリターンを最大化するリバランス比率をAI（数理最適化）が提案。
- 📊 AI Alpha Scorer (財務・決算スコアリング): J-Quantsの決算短信データを機械学習モデルに読み込ませ、業績に対して株価が歪んでいる「隠れた本命株」をサーチ。
- 🔔 Signal Beacon (Discord/LINE通知): 毎朝の相場が始まる前に、スクリーニングされた注目銘柄やリバランスのタイミングを自動通知。

---

## Architecture

本システムは、モダンなWebフロントエンドと、データサイエンスに特化したPythonバックエンドを分離したマイクロサービス構成を採用しています。Raspberry Pi 4B などの省電力環境でも快適に動作するよう最適化されています。

■ システムのデータフロー:
1. ブラウザ <-> Next.js (フロントエンド / Web API) [HTTP/JSON通信]
2. Next.js <-> FastAPI (Python / データ・AI) [内部API連携]
3. FastAPI <-> SQLite / Postgres [データベース接続]
4. FastAPI -> J-Quants API からデータを取得
5. FastAPI -> PyPortfolioOpt / TA-Lib で計算処理を実行

### 🛠️ Technical Stack
- Frontend / Core API: Next.js (App Router), TypeScript, Tailwind CSS, Shadcn UI
- Data / AI Backend: Python 3.11+, FastAPI, Pandas, NumPy
- Quantitative Libraries: PyPortfolioOpt (ポートフォリオ最適化), TA-Lib (テクニカル指標)
- Database: SQLite (または PostgreSQL)
- Infrastructure: Raspberry Pi 4B (常時起動運用) / Windows PC (重い機械学習の学習時のみ)

---

## Getting Started (Development)

### 1. Prerequisites
- Node.js (v18+)
- Python (3.10+)
- J-Quants API のアカウントおよび APIキー

### 2. Backend Setup (Python / FastAPI)
- cd backend
- python -m venv .venv
- source .venv/bin/activate (Windows環境の場合は .venv\Scripts\activate)
- pip install -r requirements.txt

### 3. Frontend Setup (Next.js)
- cd frontend
- npm install
- npm run dev

---

## License

This project is licensed under the MIT License - see the LICENSE file for details.
