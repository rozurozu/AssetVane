# Architecture（システム構成）

AssetVane のシステム構成、データフロー、ディレクトリ構成をまとめる。
個別の設計判断の「なぜ」は [decisions.md](decisions.md) を参照。

---

## 0. 製品の位置づけ

AssetVane は「数理計算の結果を見せるだけのダッシュボード」**ではない**。
**Python が計算した客観的な事実を土台に、AI が投資判断の相談役となって方針づくりと提案を行う**ツールである。主対象は日米の株式（当面は日本株、米国株は後期）。投信・現金・主要指数は「全体に対する割合・マクロの文脈」として軽く扱う。

---

## 1. 全体構成

2 プロセス構成。

- **Next.js（フロントエンド）**: 画面表示・ユーザー操作・AI チャット UI のみ。DB には触れない。
- **FastAPI（バックエンド）**: データ取得・数理計算・AI Advisor・DB アクセスをすべて担当する**唯一のデータ所有者**。

```
┌──────────────┐
│   ブラウザ    │  画面表示 / AIとのチャット
└──────┬───────┘
       │ HTTP/JSON
┌──────▼─────────────────────────┐
│  Next.js (App Router)          │  ← UIのみ。DBは触らない
└──────┬─────────────────────────┘
       │ REST
┌──────▼───────────────────────────────────────────────┐
│  FastAPI (Python)                                     │
│                                                       │
│  ┌─ 数理計算層（"事実"を計算）────────────────────┐  │
│  │  自前 quant 純関数 / PyPortfolioOpt / LightGBM │  │
│  │  → signals / portfolio指標 / ドローダウン 等   │  │
│  └───────────────────────┬───────────────────────┘  │
│                          │ 構造化された事実           │
│  ┌─ AI Advisor 層 ───────▼───────────────────────┐  │
│  │  [軸1] 夜の分析AI（cron・方針見直し・日記）     │  │
│  │  [軸2] 相談チャットAI（対話・方針調整・提案）   │  │
│  │  LLMアダプタ（OpenRouter既定/差替可）・ToolCalling│ │
│  └───────────────────────────────────────────────┘  │
│                                                       │
│  データソース・アダプタ / 夜間バッチ(cron) / 通知     │
└──┬───────────────┬───────────────┬──────────────────┘
   │               │               │
┌──▼────┐   ┌──────▼───────┐   ┌───▼──────────┐
│SQLite │   │データソース   │   │Discord Webhook│
│(WAL)  │   │ J-Quants V2   │   │  通知         │
│       │   │ (＋Yahoo/EDINET 等)│ └──────────────┘
└───────┘   └──────────────┘
```

### なぜこの分離か（要点）

- **DB の単一所有者を FastAPI に固定** → SQLite への接続経路を 1 プロセスに寄せてロック競合を抑える。書き込み系統は夜間バッチ・昼の手入力・チャット/承認の 3 つだが、同一 FastAPI プロセス内で扱う。スキーマも Python に一元化。
- 責務が「Next = 見せる・対話する係 / FastAPI = データ・計算・AI 係」と明確に分かれる。

詳細は [decisions.md ADR-005](decisions.md)。

---

## 2. AI Advisor（製品の核心）

### 2.1 基本原則：AI は計算しない（Tool Calling）

> **Python が「事実（数字）」を計算し、LLM は「事実の上で判断（方針・提案）」する。**

LLM に生データを丸投げすると数値を捏造する（ハルシネーション）。これを防ぐため、LLM には **Python が計算済みの構造化された事実**（保有の偏り・相関・モメンタム上位・最適化結果・想定最大損失など）だけを、**Tool Calling（関数呼び出し）**で渡す。LLM の仕事は計算ではなく**解釈・方針づくり・提案**に限定する（[decisions.md ADR-014](decisions.md)）。

専門性は「丁寧に質問すること」ではなく、**3 つの不変資産**（CORE プロンプト＝プロの規律・方法論／Tool ライブラリ＝実計算／手法カード＝ドメイン知識）に宿す。システムプロンプトは **不変の CORE（リポジトリ管理）＋ 可変の POLICY（DB の `policy` をコンパイル）** の 2 層に分け、専門性の核がチャットで drift しないようにする（[decisions.md ADR-015](decisions.md)）。プロンプト構成・Tool 一覧・相談フローの詳細は **[advisor.md](advisor.md)** を参照。

### 2.2 2 軸の AI（同じ脳・2 つの入口）

両軸は同じ状態（`policy` / `advisor_journal` / 数理計算の事実）を共有する。

| 軸 | 起動 | 役割 |
|---|---|---|
| **軸1: 夜の分析AI** | cron（毎晩・夜間バッチの後） | 「昨日までの方針」＋「今日の状況」を突き合わせ、方針の**見直しを提案**し、**投資日記（`advisor_journal`）**を1件書く |
| **軸2: 相談チャットAI** | ユーザー操作（ダッシュボードのチャット窓） | 自然言語で投資方針を**対話的に調整**。Python のスクリーニング結果を Tool で引き、**根拠付きで銘柄・比率を提案** |

夜に考えたこと（日記・提案）を、昼にチャットで続けられる。**状態の連続性**がこの製品の肝。

軸2 のチャットは**全ページ常駐のフローティング UI**として実装し、ページ遷移しても会話が保持される（[decisions.md ADR-024](decisions.md)）。ユーザーが「画面を見ながら」相談できるよう、チャットには**見ているページ＋主対象だけ**を軽量に渡す（数値は渡さない＝[ADR-025](decisions.md)）。画面構成・常駐チャットの詳細は **[screens.md](screens.md)** を参照。

### 2.3 投資方針 `policy` の更新フロー

`policy` は**単一のアクティブな方針**（[decisions.md ADR-013](decisions.md)）。更新経路は 2 つ。

- **相談チャットAI**: 対話の中で即時に編集（ユーザーは常に輪の中にいる）。
- **夜の分析AI**: 変更を**提案**し、ユーザーが**承認**したら反映（根幹方針の暴走を防ぐ＝承認制）。日々の細かな見解は日記に自由に書く。

方針が変わるたび、その日の日記に **`policy` のスナップショット**を残す。これが版管理の代わりになり、「方針がどう進化したか」を後から辿れる。

### 2.4 LLM アダプタ（差し替え可能）

LLM 接続は共通インターフェースのアダプタで抽象化する（OpenAI 互換 1 本・[decisions.md ADR-012](decisions.md)）。provider・API キー・ベース URL・model と**面別割当**（chat/nightly/dossier/tagger/triage＝triage は知識カード審査・[ADR-062](decisions.md)）は **DB に保存し `/settings` の WebUI で編集する**（[ADR-058](decisions.md)）。複数の OpenAI 互換 provider（OpenRouter / OpenAI 直 / Claude / ローカル / Sakana）を登録し、面ごとに provider と model を割り当てられる。真に特殊なのは codex（鍵なし組み込み）だけ。

- 既定: **OpenRouter**（クラウド・多モデルルーター）。母艦はラズパイのままで動く（推論はクラウド側）。
- 将来: **Ollama 等のローカル LLM**（Mac mini を導入したら、データを外に出さずローカル完結へ）。

### 2.5 RAG（後付け）

投資手法の論文（例：リードラグ論文）やニュースを AI に参照させる RAG は有用だが、最初は不要。論文が数本なら「**手法カード（要約）を直接プロンプトに差し込む**」で足りる。知識ベースが増えたら `sqlite-vec` で RAG 化する（SQLite のまま）。なお**ニュース統合コーパスの意味検索は別系統で実装済み**（[ADR-045](decisions.md) 段階 A・`search_news`）。手法カードの RAG 化は未着手。

---

## 3. データフロー

### 3.1 夜間バッチ（cron / 自動）

```
1. データ取得   : J-Quants V2 から日足・財務を取得 → DB保存
2. 指標計算     : モメンタム・出来高急増・最適化・スコア等を自前 quant 純関数で計算 → signals 保存（事前計算・ADR-016）
3. 夜の分析AI   : 方針×状況を突き合わせ → 見直し提案 + 投資日記を生成
4. 通知判定     : 条件合致時に Discord Webhook へ送信
```

重い探索は夜間に `signals` へ焼き、朝の通知・画面・チャットを即応にする（ハイブリッド）。

### 3.2 画面操作・チャット（オンデマンド）

```
画面操作   : Next.js → FastAPI REST → SQLite読み / 軽い計算 → JSON → 描画
AIチャット : Next.js → FastAPI → AI Advisor(軸2) → ToolでPython計算を取得 → LLM解釈 → 応答
```

---

## 4. データソース・アダプタ

取得は**差し替え可能なアダプタ構成**（[decisions.md ADR-010](decisions.md)）。

| アダプタ | 対象 | 投入フェーズ |
|---|---|---|
| `JQuantsAdapter` | 日本株・ETF の日足 / 財務（V2 API） | Phase 0〜 |
| `IndexAdapter`（軽量・フォールバック連鎖）| 主要指数（TOPIX / S&P500 等）の水準＋**米国 SPDR 業種 ETF の調整後 close**（Phase 7(A)・[ADR-039](decisions.md)）。ソースは `YahooIndexSource`（yfinance・主）→ `StooqIndexSource`（フォールバック）| Phase 2〜（マクロ文脈）／Phase 7(A)（業種 ETF）|
| `UsEquityAdapter`（yfinance・フォールバック連鎖） | 米国個別株の日足 OHLCV・財務（`.info`） | Phase 7(B-1)（実装済み・[ADR-055](decisions.md)）|
| `NewsAdapter`（Google News RSS → httpx＋trafilatura。**httpx 一本**＝[ADR-020](decisions.md) 改訂・昼 MCP／夜 httpx の 2 系統は撤回） | 個別銘柄ニュース（要約のみ保持） | Phase 4（ドシエ） |
| `FxAdapter`（yfinance） | USD/JPY 等 | Phase 7(B-2)（実装済み・[ADR-057](decisions.md)）|
| `EdinetAdapter` | EDINET 有報「事業の内容」テキスト（テーマタグの JP 信号源） | テーマタグ段階 C（[ADR-056](decisions.md)）|
| `FundNavAdapter` | 投資信託の基準価額 | 投信保有管理（[ADR-054](decisions.md)）|

各アダプタは「銘柄コードと期間を渡すと日足 DataFrame を返す」共通インターフェースを実装する。

> **Phase 7(A) の逸脱（[ADR-039](decisions.md)）**: 米国業種 ETF（11 本の SPDR）は当初 `UsEquityAdapter` 想定だったが、リードラグに必要なのは**調整後終値だけ**（OHLCV・通貨/FX 不要）のため、`UsEquityAdapter` を前倒し新設せず、既存 `IndexAdapter` のフォールバック連鎖に `YahooIndexSource` を足して `index_quotes` に流用する。これにより Stooq の BOT 判定で死んでいた既存指数取得（^SPX 等）も同時復旧する。`UsEquityAdapter`／通貨/FX／GICS は Phase 7(B) へ繰り延べ。

---

## 5. 計算資源の運用方針

ラズパイ 4B（**8GB 推奨**）での常時運用が前提（[decisions.md ADR-003](decisions.md)）。

- **クラウド LLM（OpenRouter）なら推論はクラウド側**なので、ラズパイは API を叩くだけで足りる。
- **ML の「学習」は別 PC**で行い、`.pkl` だけをラズパイにコピーして「推論」のみ（[ADR-006](decisions.md)）。
- 全銘柄をメモリ一括展開せず、SQL で必要分だけ読む（OOM 回避）。
- 将来ローカル LLM を使うなら Mac mini（低消費電力・常時起動向き）。

---

## 6. ディレクトリ構成

```
AssetVane/
├── README.md
├── compose.yaml          # Docker Compose（dev/prod の土台・ADR-021）
├── .env.example          # 環境変数テンプレート（J-Quants / LLM のキー等）
├── .gitignore
├── .dockerignore
├── docs/                 # 設計ドキュメント
├── frontend/             # Next.js（App Router）+ Turbopack ※Phase 0で作成（ADR-022）
│   ├── Dockerfile        #   本番は standalone output（ADR-021）
│   ├── biome.json        #   Lint/Format（ADR-023）
│   └── ...
├── backend/              # FastAPI + 夜間バッチ ※Phase 0で作成
│   ├── Dockerfile
│   ├── app/              #   REST API
│   ├── advisor/          #   AI Advisor（2軸・LLMアダプタ・ToolCalling）
│   ├── batch/            #   cron から起動する夜間バッチ
│   ├── adapters/         #   データソース・アダプタ
│   ├── pyproject.toml    #   uv 管理・Ruff/pyright 設定（ADR-023）
│   └── .env              # ローカルのみ（git管理外）
└── data/                 # SQLite ファイル置き場（git管理外・named volume でマウント）
    └── assetvane.db
```

> `frontend/` と `backend/` の実体は Phase 0 で作成する。現時点ではディレクトリ構成の合意のみ。コンテナ構成・ツールチェーンは [ADR-021/022/023](decisions.md) を参照。

---

## 7. 通信・公開・障害・運用

### 7.1 Next ↔ FastAPI の通信

- **API 契約**は [api.md](api.md) に定義。正本は FastAPI 自動生成の OpenAPI（`/docs`・`/openapi.json`）。
- **同一オリジン化**（[ADR-037](decisions.md)）: ブラウザは frontend の**相対パス `/api`** だけを叩き、**Next の rewrites**（`next.config.ts`）が裏で backend へ素通しする。ブラウザの相手は常に frontend(:3000) なので **CORS は不要**、backend のホストを知る必要も無いので **API_URL の焼き込みも不要**。秘密情報のうち **J-Quants/EDINET/Discord は backend の `.env` のみ**、**LLM の API キーは backend の DB**（`llm_providers`・平文・[ADR-058](decisions.md)・将来は暗号化予定）に置く。いずれもフロントには渡さない（GET では LLM キーをマスクして返す）。
- rewrites の転送先は **Next サーバ（frontend コンテナ）から見た backend** なので、Compose 内部 DNS の固定名 `http://backend:8000` を使う（環境変数 `BACKEND_ORIGIN`・既定 `http://backend:8000`、compose 無しのホスト直 dev だけ `http://localhost:8000`）。**ホスト非依存の固定名**なので、ラズパイの IP/mDNS が何であろうと同じイメージ・無設定で動く（これが旧構成の「3 ホスト一致」地雷を根本から消した点）。透過 HTTP プロキシなので Next は DB を触らず REST を素通しするだけ＝[ADR-005](decisions.md) を侵さない。
- FastAPI は backend コンテナ内で **`0.0.0.0:8000` で待ち受け**る（Next サーバから到達するため。:8000 のポート公開は `/docs` を別端末から見るデバッグ用途で残す）。
- 起動は **2 サービス（コンテナ）**: backend（`uvicorn` で FastAPI）＋ frontend（dev は `next dev --turbopack` / 本番は standalone の `node server.js`）。Compose で両方を立ち上げる（[ADR-021](decisions.md)）。起動手順は [README](../README.md) に記載。

### 7.2 障害時の方針（無人運用の前提）

常時無人で夜間バッチが回るため、失敗が**黙って放置されない**ようにする（[decisions.md ADR-018](decisions.md)）。

- **夜間バッチ失敗**: 例外時は `DISCORD_WEBHOOK_URL` へエラー通知（気づけるように）。
- **J-Quants 429/障害**: レート制限を守りつつリトライ。部分失敗は `fetch_meta` で再開可能に（冪等・UPSERT）。
- **LLM 失敗/タイムアウト**: リトライし、それでも失敗ならその日の日記をスキップして記録（signals は前日分が残る）。

### 7.3 バックアップ

`policy`・`advisor_journal`・`transactions`・`holdings`・`cash` は**手入力の一点もの**で再取得できない。SD カード故障で消えないよう、`assetvane.db` を定期バックアップする（[decisions.md ADR-017](decisions.md)）。SQLite は `VACUUM INTO` / `.backup` で安全にコピーできる。バックアップ先を外部に置く場合、DB には保有・資産・方針が**平文**で入る点に注意。

### 7.4 環境変数の検証

起動時に必須/任意キーを検証し、欠落時は**どの Phase で何が要るか**を明示するエラーを出す（[.env.example](../.env.example)）。LLM の provider/キー/model は env ではなく DB（`/settings` で登録・[ADR-058](decisions.md)）なので env 検証の対象外で、面の充足は `GET /llm/faces` の `configured` で見る。**migration 後、`/settings` で provider を登録するまで鍵あり面の LLM は動かない**（codex 面は鍵なしで最初から動く）。

### 7.5 コンテナ構成（開発・本番）

開発・本番とも **Docker Compose** で動かす（[ADR-021](decisions.md)）。

- **サービス**: `backend`（FastAPI/Python）＋ `frontend`（Next.js/Node）。SQLite はファイルなので **DB コンテナは作らず** `data/` を named volume で永続化（DB に触れる OS プロセスは FastAPI 1 つ＝[ADR-002/005](decisions.md)）。
- **ローカル開発**: ソースをバインドマウントして HMR/`--reload` を効かせる。`node_modules` と Python の仮想環境は**マウントから除外**（ホストとコンテナで OS/アーキが違うと壊れるため）。
- **ラズパイ本番**:
  - **USB SSD ブート推奨**（SD の I/O・寿命対策。[ADR-017](decisions.md) のバックアップとも直結）。
  - **イメージは別 PC でクロスビルド → ラズパイは pull のみ**（ARM の LightGBM 等のビルド回避。学習も別 PC ＝[ADR-006](decisions.md)）。
  - Next は **standalone output** でイメージ・常駐メモリを縮小（[ADR-022](decisions.md)）。
  - **メモリ**: FastAPI＋Node 常駐＋夜間バッチ（pandas/最適化）の同時稼働は 8GB でも余裕が大きくない。夜間バッチのピークと常駐を意識する。

### 7.6 ツールチェーン

2 言語のツールを高速・低設定で揃える（[ADR-023](decisions.md)）。

- **frontend（TS）**: **Biome**（lint＋format。ESLint/Prettier 代替）。Next 固有 lint は持たないためレビューで補う。
- **backend（Python）**: **uv**（パッケージ/venv）＋ **Ruff**（lint＋format）＋ **pyright**（型チェック）。
