# 現状マップ（実装の実際）— Phase 0 完了時点

> 後続設計チーム向けの「コードの実際」棚卸し。設計の真実は `docs/`、ここは**現に動いている実装**の索引。
> 参照は `path:行` 形式。コードは load-bearing な箇所だけ短く引用する。
> 作成: 2026-06-03（コードは読み取りのみ・変更なし）。

---

## 1. backend ディレクトリ構成

```
backend/
├── pyproject.toml            # 依存・ruff/pyright/pytest 設定（uv 管理）
├── uv.lock
├── alembic.ini
├── Dockerfile / .dockerignore / .env / .python-version
├── data/assetvane.db         # SQLite 実 DB（WAL）
├── alembic/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/0001_baseline.py
├── app/
│   ├── main.py               # FastAPI 本体・lifespan(init_db)・CORS・/health
│   ├── config.py             # pydantic-settings（.env）
│   ├── adapters/jquants.py   # JQuantsAdapter（V2・x-api-key）
│   ├── advisor/
│   │   ├── router.py         # POST /chat（CORE 差し込みのみ・Tool 未接続）
│   │   ├── llm.py            # OpenAI 互換アダプタ（complete()）
│   │   └── core_prompt.md    # 不変 CORE プロンプト
│   ├── batch/__init__.py     # ★空（docstring のみ）
│   ├── db/
│   │   ├── engine.py         # Engine・init_db(alembic upgrade)・get_conn・healthcheck
│   │   ├── repo.py           # クエリ（Core）・UPSERT
│   │   └── schema.py         # Table 定義（stocks / daily_quotes のみ）
│   ├── routers/stocks.py     # GET /stocks・/stocks/{code}・/quotes/{code}
│   └── scripts/backfill.py   # 手動バックフィル（数銘柄）
└── tests/                    # conftest / test_api / test_jquants / test_repo / test_migrations
```

---

## 2. 既存テーブルと実列（`backend/app/db/schema.py`）

**Phase 0 は 2 テーブルのみ。** `financials`/`signals`/`policy`/`advisor_journal`/`fetch_meta` 等は**未定義**（使う Phase で同じ `metadata` に足す方針）。

- `stocks`（PK `code`）: `code`(str,5桁) / `company_name` / `sector33_code` / `sector17_code` / `market_code` / `is_etf`(int 0/1) / `updated_at`(ISO文字列)
- `daily_quotes`（複合PK `(code,date)`・`schema.py:42`）: `code`(str,NOT NULL) / `date`(str 'YYYY-MM-DD',NOT NULL) / `open` `high` `low` `close` `volume` `adj_close`（すべて Float・nullable）
  - インデックス: `ix_daily_quotes_code` / `ix_daily_quotes_date`

> 型注: `volume` は **Float**（Integer ではない）。`is_etf` は Integer フラグ。

---

## 3. 既存リポジトリ関数（`backend/app/db/repo.py`）

```python
_upsert(table, rows, index_elements) -> int          # sqlite_insert + on_conflict_do_update（冪等）
upsert_stocks(rows: list[dict]) -> int                # index_elements=["code"]
upsert_daily_quotes(rows: list[dict]) -> int          # index_elements=["code","date"]
list_stocks(conn, q: str | None = None) -> list[dict] # q は code|company_name の LIKE 部分一致・code 昇順
get_stock(conn, code: str) -> dict | None
get_quotes(conn, code, from_=None, to=None) -> list[dict]  # date 範囲フィルタ・date 昇順
```

- 書き込み（UPSERT）は `repo` 内で `engine.begin()`、読み取りは**ルータが `get_conn` 依存で渡す Connection** を受ける（書き/読みで接続取得が非対称）。
- 戻り値は素の `dict`（Pydantic 変換はルータ側）。

---

## 4. 既存 API ルートと入出力モデル

| メソッド/パス | 入力 | 出力 | 定義 |
|---|---|---|---|
| `GET /health` | — | `{status, service, version, phase:0, db, env}` | `main.py:50` |
| `GET /stocks` | `q`(部分一致,任意) | `list[Stock]` | `routers/stocks.py:38` |
| `GET /stocks/{code}` | path `code` | `Stock`（無ければ 404） | `routers/stocks.py:46` |
| `GET /quotes/{code}` | path `code` / `from`(alias) / `to` | `list[Quote]`（date 昇順） | `routers/stocks.py:54` |
| `POST /chat` | `{messages:[{role,content}]}` | `{reply}`（失敗時 502） | `advisor/router.py:44` |

Pydantic モデル（`routers/stocks.py:19-35`）:
- `Stock`: `code` / `company_name?` / `sector33_code?` / `sector17_code?` / `market_code?` / `is_etf?`（**`updated_at` は返さない**）
- `Quote`: `date` / `open?` / `high?` / `low?` / `close?` / `volume?` / `adj_close?`（**`code` は返さない**）
- `Message`: `role: Literal["user","assistant"]`（**`system` 不可**） / `content`

---

## 5. JQuantsAdapter の既存メソッド（`backend/app/adapters/jquants.py`）

V2・`x-api-key` ヘッダ・base `https://api.jquants.com`。Free 5req/分対策で `_MIN_INTERVAL_SECONDS=13.0` のスロットル＋429 指数バックオフ（最大4回）＋`pagination_key` 全ページ集約を内蔵。

公開メソッド:
- `fetch_daily_quotes(code, from_=None, to=None) -> list[dict]` … 1 銘柄の日足。`/v2/equities/bars/daily?code=...`
- **`fetch_daily_quotes_by_date(date) -> list[dict]` … 日付一括取得（実装済み・`jquants.py:130`）。** `code` 無し `date` のみで**その日の全銘柄**（約4400行）を返す。Phase 1 初回バックフィルの入口。「銘柄ループ」ではなく「営業日ループ」を回せる根拠（docs/jquants.md §4・実機確認済み）。
- `fetch_master(codes: list[str]) -> list[dict]` … 銘柄マスタ。codes を**1件ずつループ**取得（`/v2/equities/master?code=...`）。

正規化（外部キー→内部列・`jquants.py:141-180`）:
- 日足 `_normalize_quote`: `Code|code→code` / `Date|date→date`(YYYY-MM-DD化) / `O→open` `H→high` `L→low` `C→close` `Vo→volume` `AdjC→adj_close`（V2 略記/V1 フルネーム両対応の `_first` フォールバック）
- マスタ `_normalize_stock`: `CoName→company_name` / `S33→sector33_code` / `S17→sector17_code` / `Mkt→market_code`。**`is_etf` は常に 0 ハードコード**（Mkt→is_etf 対応は Phase 7 で実装予定）。`updated_at` は取得時刻 UTC ISO。
- `_to_jq_code`: 4桁→5桁（`7203→72030`）。`_norm_date` / `_extract_rows`（`data` 配列＋pagination_key）。
- 例外は `JQuantsError`（ルータ境界での HTTPException 翻訳は **/chat のみ**実装、stocks ルータ側にはバックフィルが CLI なので翻訳経路なし）。

---

## 6. チャット/Advisor 実装の状態

- **CORE プロンプト**: `backend/app/advisor/core_prompt.md`（リポジトリ内ファイル・ADR-015）。`router.py:26` で**起動時 1 回読み込み**。内容は「Tool 未接続＝数値にアクセスできない・捏造禁止・一般論で論点整理せよ」と明記した暫定版。
- **LLM アダプタ**: `backend/app/advisor/llm.py`。`AsyncOpenAI`（OpenAI 互換）。`.env` の `LLM_BASE_URL`/`LLM_MODEL`/`LLM_API_KEY` 差替で OpenRouter（既定）↔ Ollama 切替。`complete(messages)->str` の運搬役のみ。
- **組み立て**: `router.py:50` で `[{system: CORE}] + 会話履歴` だけ。**サーバはステートレス**（履歴は frontend 保持・毎ターン全 messages 送信）。
- **Tool 未接続**（確認済み）: Tool Calling・POLICY・手法カード・画面コンテキスト・投資日記の差し込みはすべて **TODO コメントのみ**（`router.py:36,48`）。`tools=` 引数なし・非ストリーミング（`stream` も TODO）。

---

## 7. 空 / 未実装の領域

- **`backend/app/batch/__init__.py` は空**（docstring のみ・夜間 cron バッチは Phase 1）。`batch/` 配下に実体ファイルなし。
- **cron 設定なし**（Docker/compose レベルでも未確認の範囲）。
- **signals/financials/policy/journal/proposals 等のテーブル・API・取得ロジックは未実装**（schema.py に無い）。
- **全銘柄バッチ・差分取得（`fetch_meta`）は未実装**。`fetch_daily_quotes_by_date` という**部品だけ**ある（呼び出し元はまだ無い＝backfill.py は銘柄ループのまま `fetch_daily_quotes` を使う）。
- **テクニカル指標・最適化・ML は未実装**（依存も未導入＝§8）。
- **Discord 通知未実装**（`config` にキーだけ・Phase 6）。

---

## 8. 依存ライブラリの現状

**backend**（`pyproject.toml`・`uv.lock` で確認）— 数理/ML/可視化系は**一切入っていない**:
- 入っている: `fastapi` / `uvicorn[standard]` / `pydantic-settings` / `openai>=2.40` / `sqlalchemy>=2.0` / `httpx` / `alembic`。dev: `ruff` / `pyright` / `pytest`。
- **未導入（要追加）**: `pandas` / `numpy` / `scipy` / **TA-Lib** / **pandas-ta** / **PyPortfolioOpt** / **lightgbm`。docs では将来使う前提（architecture.md・roadmap.md・decisions.md ADR-006）だが、Phase 1 で TA-Lib は ARM ビルド難のため `pandas-ta` 等の代替を要検証（roadmap.md §38）。

**frontend**（`frontend/package.json`）:
- dependencies: `next@^15.1.6` / `react@19` / `react-dom@19` / **`lightweight-charts@^5.2.0`**（チャート）。
- devDependencies: `@biomejs/biome` / `tailwindcss@4` + `@tailwindcss/postcss` / `typescript@5.7` / 型定義。
- `dev` のみ `--turbopack`、`build` は webpack（ADR-022 フォールバック状態）。lint/format は Biome。

---

## 9. frontend 構成・型・既存画面

構成（`frontend/src/`）:
```
app/layout.tsx        # アプリシェル（Sidebar 220px + Topbar + main）＋ AdvisorChat 常駐（ADR-024）
app/page.tsx          # Dashboard（★全面モック・mock-data 配線・REST 未配線）
app/stocks/page.tsx   # 銘柄一覧（getStocks・実 API）
app/stocks/[code]/page.tsx  # 銘柄詳細（getStock+getQuotes→CandleChart・実 API）
components/chart/CandleChart.tsx   # lightweight-charts v5 でローソク足＋出来高
components/shell/Sidebar.tsx       # nav（mock-data.nav）・usePathname でアクティブ判定
components/shell/Topbar.tsx        # /health 疎通バッジ・検索/鮮度は静的表示
components/advisor/AdvisorChat.tsx # /chat 配線済みフローティングチャット（実 LLM・ドラッグ可）
lib/api.ts            # REST クライアント＋型
lib/mock-data.ts      # Dashboard/Sidebar 用ダミーデータ
```

`lib/api.ts` の型・関数:
- 型 `Stock`（api 由来・`is_etf:number|null`）/ `Quote`（`date:string` ほか `number|null`）— backend Pydantic と一致。
- `getStocks(q?)` / `getStock(code)` / `getQuotes(code,from?,to?)`。`API_BASE = NEXT_PUBLIC_API_BASE_URL ?? http://localhost:8000`。
- `/chat` は AdvisorChat 内で**直接 fetch**（api.ts には未集約）。

実 API 配線済み: Stocks 一覧・銘柄詳細（チャート）・AdvisorChat・Topbar health。
**モックのまま**: Dashboard（`page.tsx` は `mock-data.ts` の kpis/allocation/proposals/policy/signals/watchlist/journal を表示）。Sidebar の `nav` も mock-data に定義（Signals=P1 など未投入 Phase は非活性ボタン）。

---

## 設計で特に注意すべき「実装の実際 vs docs のズレ」

後続設計が踏みやすい接地ポイント（最大10件）:

1. **DB に存在するのは `stocks`/`daily_quotes` の2表だけ**。data-model.md が描く `signals`/`financials`/`policy`/`fetch_meta` 等は schema.py に未定義。Phase 1 は「テーブル新設＋autogenerate マイグレーション」から始まる。
2. **数理/ML/可視化の依存が backend に皆無**（pandas すら無い）。Phase 1 着手の最初の一歩は依存追加＋ARM ビルド検証（TA-Lib vs pandas-ta）。docs の前提イメージと実環境にギャップ。
3. **日付一括取得は「部品だけ」存在**: `fetch_daily_quotes_by_date()` は実装・実機確認済みだが、**呼び出すバックフィル経路が未実装**。backfill.py は今も銘柄ループ（`fetch_daily_quotes`）。Phase 1 で営業日ループの新バックフィル/バッチを書く必要。
4. **`is_etf` が常に 0 ハードコード**（`jquants.py:171`）。ETF/REIT 判別は Mkt→is_etf 対応が未実装（docs は Phase 7 と整理済みだが、Phase 1 の全銘柄取得で ETF/REIT 行が `is_etf=0` で混ざる点に注意）。
5. **バッチの唯一の書き手（ADR-002）が未実装**。`app/batch/` は空。cron も無い。「DB 書き手はバッチ1つ」という不変条件はまだコードで担保されていない（現状の書き手は手動 CLI backfill）。
6. **Advisor は CORE 差し込みのみで Tool 完全未接続**。`/chat` は `tools=` 無し・POLICY/手法/文脈なし。CORE プロンプトも「数値出せない」と自己申告する暫定版。Tool Calling は Phase 3 だが、現状プロンプトの差し替え前提で設計すること。
7. **`/chat` がステートレスで履歴を全送信**。会話保持は frontend のメモリのみ（localStorage/DB 永続化なし＝リロードで消える）。`advisor_journal` のスナップショット設計（ADR-013）とまだ繋がっていない。
8. **画面コンテキスト（ADR-025）が未配線**。AdvisorChat の「見ているページ: Dashboard」は**ハードコード静的表示**で、`/chat` body に page/focus を送っていない（`ChatRequest` に context フィールドも無い）。
9. **Dashboard は完全モック**で REST 由来データゼロ。`mock-data.ts` の構造（kpis/proposals/policy/signals…）は将来 API の暫定スキーマであって**契約ではない**。api.md と突き合わせて確定が要る。
10. **`volume` が Float**・`Stock` レスポンスに `updated_at` 非含・`Quote` に `code` 非含・`Message.role` は user/assistant のみ（system 不可）。型契約の細部は frontend `lib/api.ts` と一致しているので、変更時は両側同時に。
