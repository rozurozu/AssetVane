# Architecture（システム構成）

AssetVane のシステム構成、データフロー、ディレクトリ構成をまとめる。
個別の設計判断の「なぜ」は [decisions.md](decisions.md) を参照。

---

## 1. 全体構成

AssetVane は **2 つのプロセス**で構成される。

- **Next.js（フロントエンド）**: 画面表示とユーザー操作のみを担当。DB には一切触れない。
- **FastAPI（バックエンド）**: データ取得・計算・DB アクセスをすべて担当する**唯一のデータ所有者**。

```
┌──────────────┐
│   ブラウザ    │
└──────┬───────┘
       │ HTTP/JSON
┌──────▼─────────────────────────┐
│  Next.js (App Router)          │  ← UIのみ。DBは触らない
│  TypeScript / Tailwind / Shadcn │
└──────┬─────────────────────────┘
       │ REST (HTTP/JSON)
┌──────▼─────────────────────────────────────────┐
│  FastAPI (Python)                               │
│  - REST API（画面向け）                          │
│  - 数理計算（PyPortfolioOpt / TA-Lib / LightGBM）│
│  - データソース・アダプタ                         │
│  - 夜間バッチ（cron から起動）                    │
└──┬───────────────┬───────────────┬──────────────┘
   │               │               │
┌──▼────┐   ┌──────▼───────┐   ┌───▼──────────┐
│SQLite │   │データソース   │   │Discord Webhook│
│(WAL)  │   │ - J-Quants V2 │   │  通知         │
│       │   │ - 米国ETFソース│   └──────────────┘
└───────┘   └──────────────┘
```

### なぜこの分離か（要点）

- **DB の単一所有者を FastAPI に固定**することで、SQLite の「書き込みは同時に 1 つだけ」というロック競合を原理的に回避する。書き手は夜間バッチ（Python）だけになる。
- スキーマ定義が Python 側 1 箇所に集約され、二重管理による事故を防ぐ。
- 責務が「Next = 見せる係 / FastAPI = データと計算係」と明確に分かれる。

詳細な根拠は [decisions.md ADR-005](decisions.md) を参照。

---

## 2. データフロー

### 2.1 夜間バッチ（cron / 自動）

毎晩、ラズパイの cron が以下を順に実行する。

```
1. データ取得   : J-Quants V2 から日足・財務を取得 → stocks / daily_quotes / financials に保存
   （リードラグ用に米国ETF日足も別アダプタで取得）
2. 指標計算     : TA-Lib でテクニカル指標、出来高急増、リードラグスコア等を計算
3. シグナル保存 : スクリーニング結果を signals テーブルに焼く（事前計算）
4. 通知判定     : 条件に合致した銘柄があれば Discord Webhook へ送信
```

**事前計算の方針**: 重い探索（全銘柄スクリーニング）は夜間に `signals` テーブルへ保存しておき、朝の通知・画面表示を即座にする。個別銘柄のチャート用指標など軽い計算は、画面リクエスト時にその場で計算する（ハイブリッド）。

### 2.2 画面操作（オンデマンド）

```
ユーザー操作 → Next.js → FastAPI REST → SQLite 読み取り / 軽い計算 → JSON 返却 → 画面描画
```

ポートフォリオ最適化（PyPortfolioOpt）のようなユーザー起点の計算も、FastAPI が同期的に処理して結果を返す。

---

## 3. データソース・アダプタ

データ取得は**差し替え可能なアダプタ構成**にする。理由は、リードラグ戦略（Phase 5）が J-Quants には無い**米国 ETF データ**を必要とするため（[decisions.md ADR-010](decisions.md)）。

| アダプタ | 対象 | 用途 |
|---|---|---|
| `JQuantsAdapter` | 日本株・ETF の日足 / 財務（V2 API） | Phase 0〜5 全般 |
| `UsEtfAdapter` | 米国 SPDR 業種 ETF の日足（Stooq 等） | Phase 5 リードラグ |

各アダプタは「銘柄コードと期間を渡すと日足 DataFrame を返す」共通インターフェースを実装する。これにより、将来データ提供元が変わっても上位ロジックを書き換えずに済む。

---

## 4. 計算資源の運用方針

ラズパイ 4B（**8GB 推奨**）での常時運用を前提とする。重い処理は以下で回避する（[decisions.md ADR-003 / ADR-006](decisions.md)）。

- **ML の「学習」は別 PC** で実行し、完成したモデル（`.pkl`）だけをラズパイにコピーして「推論」のみ行う。
- 全銘柄をメモリに一括展開せず、**SQL で必要な銘柄・期間だけを読む**（OOM 回避）。
- DB は SQLite にして、Postgres のような別プロセスを同居させない。

---

## 5. ディレクトリ構成

```
AssetVane/
├── README.md
├── .env.example          # 環境変数のテンプレート（APIキー等）
├── .gitignore            # .env / data/ / .venv 等を除外
├── docs/                 # 設計ドキュメント
│   ├── architecture.md
│   ├── decisions.md
│   ├── data-model.md
│   ├── jquants.md
│   └── roadmap.md
├── frontend/             # Next.js（App Router）※Phase 0で作成
│   └── ...
├── backend/              # FastAPI + 夜間バッチ ※Phase 0で作成
│   ├── app/              #   REST API
│   ├── batch/            #   cron から起動する夜間バッチ
│   ├── adapters/         #   データソース・アダプタ
│   ├── requirements.txt
│   └── .env              # ローカルのみ（git管理外）
└── data/                 # SQLite ファイル置き場（git管理外）
    └── assetvane.db
```

> `frontend/` と `backend/` の実体は Phase 0 で作成する。現時点ではディレクトリ構成の合意のみ。
