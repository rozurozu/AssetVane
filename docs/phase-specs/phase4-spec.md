# Phase 4 着工仕様: Stock Dossier（個別銘柄の定性ファンダ調査）
> 出所: roadmap.md Phase 4 / ADR-020 / advisor.md。レビュー・裁定反映済み。コード未実装＝着工仕様。

> 合成元（裁定反映済みドラフト）: `_arbitration.md`（正本・最優先）／ `ai-advisor.md` §11（P4 主担当・調査パイプライン）／ `app.md` Phase 4（REST・画面）／ `data-arch.md` §0.1・§1.12・§3.2（夜間バッチ相乗り・news 取得インフラ）／ `_current-state.md`（接地）／ `_open-questions.md`（U-8）。
> ADR: ADR-020（ドシエ＝DB 保存・本文捨てる・1 本パイプライン）／ ADR-014（数値は Tool 経由・AI に計算させない）／ ADR-011（1 つの脳・2 つの起動口）／ ADR-002・ADR-005（書き手は FastAPI 1 プロセス・UPSERT 冪等）／ ADR-010（取得はアダプタ越し）／ ADR-024/025（チャット常駐・画面 context 軽量ヒント）。
> 採番: `0008_dossier`（down_revision=`0007_screening`）。当初計画は `0007_dossier`（down=`0006_advisor_state`）だったが、先行の `0007_screening`（ADR-031・後付け割り込み）が 0007 を占有したため 0008 に繰り下げ・親も screening に。watchlist/stock_dossiers/dossier_sources の 3 テーブルは **ai-advisor が正本定義**（_arbitration 決定1・B-13）。移行ファイルの発行（作成）は data-arch が代行、DDL 本体は本仕様が正本。

---

## 0. 目的と完了条件

**目的**（roadmap.md Phase 4）: ニュース・財務（将来は適時開示）を読み、個別銘柄の**定性的な調査レポート（ドシエ）**を作って更新し続ける。数理・ML（数字）を補う「物語」担当。AI は数値を計算せず（ADR-014）、定性要約に徹する。

**完了条件**（roadmap.md Phase 4・実装で満たすべき受け入れ条件）:
- watchlist 銘柄が夜間に軽く調査され、`stock_dossiers` が更新される（`last_investigated_at` 前進）。
- チャットで「この銘柄調査して」と言うと、**同じパイプライン**でリッチなレポートが生成・表示される（ADR-020・ADR-011）。
- watchlist 一覧ページに「最終調査日」（`last_investigated_at`）が出て、しきい値超過（stale=21 日）は再調査を促す警告表示になる。
- 取り込みは発行 1 週間以内の新着のみ・URL で重複排除（`dossier_sources.url` UNIQUE）。**記事全文は保存しない**（要約＋URL のみ＝ADR-020）。
- 夜は MCP 非依存（無人 cron でヘッドレスが使えないことがある）、昼チャットは MCP（playwright/fetch）でリッチ。

---

## 1. 全体像（1 つの調査パイプライン・2 つの起動口）

ADR-020・ADR-011「1 つの脳・2 つの起動口」をドシエに適用する。**`investigate_stock(conn, code, mode)` という 1 本のパイプラインを、2 つの起動口から呼ぶ**。

```
                         ┌──────────────────────────────────────┐
  起動口①: 夜間 watchlist 巡回 ──(mode="nightly")──▶│                                      │
  （APScheduler 同居・data-arch  │  investigate_stock(conn, code, mode)  │──▶ stock_dossiers(UPSERT・summary_md 更新)
    NIGHTLY_JOBS 末尾・軽め）    │  取得 → 要約 → 保存（§3）           │──▶ dossier_sources(URL UNIQUE・本文非保存)
                         │                                      │
  起動口②: チャット Tool ────(mode="chat")────▶│  fetch_news の mode で取得手段を切替    │
  （POST /chat の Tool ループ・   └──────────────────────────────────────┘
    POST /dossiers/{code}/investigate・リッチ）
```

- **共用が核心**: 夜とチャットで**段取り（取得→要約→保存）は同一**。違うのは `fetch_news` の取得手段（`mode="chat"` は MCP リッチ／`mode="nightly"` は MCP 非依存・軽め）と、要約の濃さだけ（ADR-020）。
- **書き手は FastAPI 1 プロセス**（ADR-005・_arbitration 決定5）。夜間バッチは APScheduler で同居（data-arch 方式C）するため、`POST /dossiers/{code}/investigate`（昼）と夜間巡回の書き込みは同一プロセス内で直列化される。稀な競合は SQLite `busy_timeout` で吸収。
- **AI は数値を作らない**（ADR-014）: 財務の数値は `get_financials`（data レーン・`financials` テーブル `0005`）から取り、ドシエは「定性要約（物語）」を担う。`key_facts` に PER/成長率等を載せる場合も、出所は Tool の事実であって LLM の記憶ではない。

---

## 2. スキーマ変更（`0008_dossier`）

**Alembic**: `revision="0008_dossier"`・`down_revision="0007_screening"`（先行の `0007_screening`〔ADR-031・後付け割り込み〕が 0007 を占有したため、計画の 0007→0008 へ繰り下げ・親も screening へ）。ファイル発行は data-arch が代行、DDL 正本は本仕様（B-13）。`schema.py` の同一 `metadata` に 3 テーブルを追記（_current-state.md §1 の Table 定義流儀）。**watchlist は Phase 2（旧 data-arch 案）から外し、ここに一本化**（二重 CREATE 防止）。

### 2.1 `watchlist`（夜の巡回対象・最終調査日の起点）

```sql
CREATE TABLE watchlist (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL REFERENCES stocks(code),   -- 自分データなので FK を張る（裁定 L-7）
    note        TEXT,                                     -- メモ（任意）
    added_at    TEXT,                                     -- 追加時刻 ISO8601
    UNIQUE (code)                                         -- 重複監視防止（UPSERT キー）
);
CREATE INDEX ix_watchlist_code ON watchlist(code);
```
- `last_investigated_at` は watchlist 列としては**持たない**。`stock_dossiers` を JOIN して一覧に出す（最終調査日は調査側の真実＝1 か所）。
- stale 判定（21 日超）は backend が `last_investigated_at` と現在日から算出（列に持たない・L-22）。

### 2.2 `stock_dossiers`（1 銘柄 1 行・living document）

```sql
CREATE TABLE stock_dossiers (
    code                  TEXT PRIMARY KEY REFERENCES stocks(code),  -- 1 銘柄 1 行
    summary_md            TEXT,        -- AI 生成の調査要約（markdown・ずっと更新する living document）
    key_facts             TEXT,        -- JSON: PER/成長率/直近トピック等の構造化（出所は Tool の事実）
    last_investigated_at  TEXT,        -- 最終調査時刻 ISO8601（watchlist 一覧の「最終調査日」・stale 起点）
    updated_at            TEXT         -- 行更新時刻 ISO8601
);
```

### 2.3 `dossier_sources`（ソース台帳・本文非保存）

```sql
CREATE TABLE dossier_sources (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    code          TEXT NOT NULL REFERENCES stocks(code),   -- 銘柄 FK（この銘柄のソース一覧）
    source_type   TEXT,                -- 'news' / 'disclosure' / 'twitter' 等（ADR-020・将来拡張）
    url           TEXT NOT NULL,       -- 取り込み元 URL（本文は保存しない＝要約＋URL のみ）
    title         TEXT,
    summary       TEXT,                -- 短い要約（記事全文は捨てる＝ADR-020）
    published_at  TEXT,                -- 発行日 'YYYY-MM-DD'（発行 1 週間以内のみ取り込む）
    processed_at  TEXT,                -- 取り込み・要約した時刻 ISO8601
    UNIQUE (url)                       -- URL 重複排除（再調査で同じ記事を二重取り込みしない）
);
CREATE INDEX ix_dossier_sources_code ON dossier_sources(code);
-- ix on url は UNIQUE 制約が暗黙の索引を張る（重複チェックの存在確認に使う）。
```

- **本文非保存の規律**（ADR-020）: 取得 → 要約 → **本文は捨て**、`summary` と `url` だけ残す。ストレージ・著作権の両面で全文保持は不採用。
- **UPSERT 冪等**（ADR-002）: `dossier_sources` は `url` を `on_conflict_do_update`（既存 url は title/summary を更新するか無視・実装は「既存なら skip」を既定）。`stock_dossiers` は `code` を `on_conflict_do_update`。

---

## 3. 調査パイプライン（`investigate_stock(code, mode)`）

ファイル: `backend/app/advisor/dossier.py`（新規）。ai-advisor.md §11 が正本。

```python
async def investigate_stock(conn: Connection, code: str, *, mode: Literal["nightly", "chat"]) -> dict:
    """個別銘柄を調査しドシエを生成・更新する（ADR-020）。夜間バッチ・チャット Tool で共用。

    段階（取得 → 要約 → 保存）:
      1. financials = data.get_financials(code)            # 財務（J-Quants Free・data レーン・financials 0005）
      2. articles = await fetch_news(code, since=today-7d, mode=mode)
           # mode="chat":    昼 MCP（playwright/fetch）でリッチ
           # mode="nightly": MCP 非依存で軽め（無人 cron でヘッドレス不可のことがある＝ADR-020）
           # 発行 1 週間以内のみ・取得手段の切替は data レーンが mode で実装
      3. new_articles = [a for a in articles if not repo.dossier_source_exists(conn, a.url)]  # URL UNIQUE で重複排除
      4. for a in new_articles:
             repo.upsert_dossier_source(conn, code=code, url=a.url, title=a.title,
                 summary=a.summary, published_at=a.published_at, source_type=a.source_type)
             # 本文は保存しない（要約＋URL のみ・ADR-020）
      5. existing = repo.get_dossier(conn, code)            # 既存 summary_md（living document）
      6. summary_md, key_facts = await summarize_dossier(   # LLM 単発 complete で要約更新（Tool ループ不要）
             existing, financials, new_articles)            # ※記事全文ではなく「短い要約」を渡す
      7. repo.upsert_dossier(conn, code=code, summary_md=summary_md, key_facts=key_facts,
             last_investigated_at=now, updated_at=now)
      8. return {code, summary_md, key_facts, last_investigated_at, n_sources_added: len(new_articles)}
    """
```

- **`summarize_dossier`**: LLM 呼び出し（CORE の規律を継ぐ・定性要約なので Tool ループ不要の単発 `complete()`）。**記事全文は渡さず**、ソースの**短い要約**を渡して既存ドシエを更新（living document の積み上げ）。数値は財務 Tool の事実に紐づける（ADR-014）。
- **夜（軽め）／チャット（リッチ）の共用**: 段取り（取得→要約→保存）は同一関数。`mode` が `fetch_news` の取得手段と要約の濃さだけを分ける。
- **発行 1 週間以内**: `since=today-7d` で取得側を絞る。`published_at` が範囲外のソースは取り込まない。
- **URL 重複排除**: `repo.dossier_source_exists(conn, url)` で既存判定 → 新着のみ `upsert_dossier_source`。再調査で同じ記事を二重に積まない。

---

## 4. Tool（返却スキーマ＝正本・夜間巡回の頻度/上限）

`backend/app/advisor/tools/handlers.py`（変更・investigate_stock / get_dossier / fetch_news を追加）・`tools/registry.py` に `min_phase=4` で登録（Phase 3 までは LLM に非露出）。返却は素の `dict`（registry が `json.dumps` して `tool` ロールへ）。**返却スキーマは _arbitration 決定2 の正本に完全一致**。

| Tool | 引数 | 返却 JSON スキーマ（正本・キー） | 計算/取得供給 | min_phase |
|---|---|---|---|---|
| `get_dossier` | `code: str` | `{code, summary_md, key_facts, last_investigated_at, sources: [{url, title, summary, published_at, source_type}]}` | このレーン（§3・§2 から読む） | 4 |
| `investigate_stock` | `code: str` | `{code, summary_md, key_facts, last_investigated_at, n_sources_added}` | このレーン（§3 パイプライン） | 4 |
| `fetch_news` | `code: str, since?: str` | `{code, articles: [{url, title, summary, published_at, source_type}]}` | data（昼 MCP／夜軽め） | 4 |

- **`investigate_stock` Tool の引数は `code` のみ**（正本）。`mode` は呼び出し文脈で決まる（チャット Tool 経由=リッチ、夜間巡回=軽め）。handler はチャット経路なら `mode="chat"` を内部で渡す。
- **handler は薄い橋渡しのみ**（ADR-014・レイヤ分離）。例外は握って `{error: "…"}` を返し Tool ループを落とさない。`fetch_news` の実体（昼 MCP／夜軽め）は data レーン #2 が `mode` で実装。
- **登録（Phase ゲート）**: registry の `min_phase=4`。`openai_tools(available_phase)` が `available_phase>=4` のときだけ 3 Tool を LLM に露出する。

### 4.1 夜間巡回の頻度・上限（U-8・既定値）

> ⚠️ **後日改訂（ADR-033）**: 下記の「毎晩 N=3 件」固定枠は**廃止**され、**銘柄ごとの `interval_days`＋夜あたり天井 `DOSSIER_NIGHTLY_MAX`** へ作り替えられた（実装は ADR-033 準拠）。以下は旧仕様の記録。

- **確定（L-22 / 決定7 / U-8 裁定済み）**: watchlist を `last_investigated_at` が**古い順**に並べ、**毎晩 N=3 件**だけ `investigate_stock(code, mode="nightly")` を回す。`N` は **env 既定＋設定 UI ツマミ**（U-5/ADR-028 のコスト設定 UI と同居・易に変更可）。
- **stale しきい値 = 21 日**（backend 算出）。`last_investigated_at` が 21 日より古い（または未調査）の銘柄を優先的に巡回対象に選ぶ。
- **watchlist に硬い上限は設けない**: 古い順 N 件/晩がコストを頭打ちにするので、リストが大きくなっても**コストは増えず古くなるだけ**（21 日警告で気づく）。stale ゼロを保つ目安は `N ≧ watchlist件数 ÷ 21`（N=3 なら 63 銘柄まで）。
- **夜間の起動**: data-arch の `NIGHTLY_JOBS` 末尾付近に巡回ジョブを相乗り（APScheduler 同居・方式C）。cron 起動時刻は **02:00 JST**（U-9・確定）。MCP 非依存で軽く回す（ADR-020）。
- **コスト**: 夜間でも LLM 要約コストが乗る（N=3 × 毎晩＝月 90 要約・LLM 単発 complete）。U-5 の $50/月枠から見れば誤差。LLM コスト計上は ADR-028 の `llm_usage` で夜間分も合算される。

---

## 5. REST API 契約

app.md Phase 4（P4-1・P4-2）が正本。型は `lib/api.ts` に集約し backend Pydantic と 1:1（ADR-005）。エラーは FastAPI `{detail}`。HTTP 入出力は app レーン、サーバ側ロジック（パイプライン・状態）は ai-advisor（§3）。

### 5.1 `GET/POST/DELETE /watchlist`

```ts
export interface WatchlistItem {
  id: number;
  code: string;
  company_name: string | null;          // stocks JOIN
  note: string | null;
  added_at: string;
  last_investigated_at: string | null;  // stock_dossiers JOIN（一覧の「最終調査日」）
  stale: boolean;                        // last_investigated_at が古い（21 日超 or 未調査）→ 再調査促す
}
export interface WatchlistResponse { items: WatchlistItem[]; }
export interface WatchlistInput { code: string; note?: string | null; }
```
- `GET /watchlist` → `WatchlistResponse`。`last_investigated_at` は `stock_dossiers` JOIN、`stale` は backend がしきい値（21 日・L-22）で算出。
- `POST /watchlist` body `WatchlistInput` → 追加行（`WatchlistItem`）。UNIQUE(code) 違反は重複として扱う。
- `DELETE /watchlist/{id}` → `{ ok: true }`。
- backend Pydantic（案）: `WatchlistItemOut` / `WatchlistResponse` / `WatchlistIn`。
- lib/api.ts: `getWatchlist()` / `addWatchlist(input)` / `removeWatchlist(id)`。

### 5.2 `GET /dossiers/{code}`・`POST /dossiers/{code}/investigate`

```ts
export interface Dossier {
  code: string;
  summary_md: string;                          // markdown（UI でそのまま描画）
  key_facts: Record<string, unknown> | null;   // PER/成長率/直近トピック等（構造化・出所は Tool の事実）
  last_investigated_at: string | null;
  updated_at: string | null;
  sources: DossierSource[];                     // ソース台帳（要約＋URL・本文なし）
}
export interface DossierSource {
  id: number;
  source_type: "news" | "disclosure" | "twitter" | string;
  url: string;
  title: string | null;
  summary: string | null;                       // 短い要約（本文は保存しない＝ADR-020）
  published_at: string | null;
}
export interface InvestigateResult { dossier: Dossier; }  // 調査後の最新ドシエ
```
- `GET /dossiers/{code}` → `Dossier`（未調査なら 404 または `summary_md: ""`＋空 `sources`）。⚠️ **要確認（実装ドリフト）**: 実装は frontend が常に `DossierSection` を描画する都合で **404 を返さず常に 200＋空ドシエ**（`summary_md: ""`＋空 `sources`）を返す（`routers/dossier.py` に意図コメントあり）。契約として 404 を残すか 200 固定にするかは要判断。
- `POST /dossiers/{code}/investigate` → `InvestigateResult`（`investigate_stock(code, mode="chat")` 起動。チャットの「この銘柄調査して」と共用パイプライン＝ADR-020）。
- **同期（L-23・確定）**: 処理完了まで待って最新ドシエを返す（着工は同期）。遅ければ後で「ジョブ ID → ポーリング」へ移れるよう設計（パイプラインはプロセス非依存）。
- backend Pydantic（案）: `DossierOut` / `DossierSourceOut` / `InvestigateResult`。
- lib/api.ts: `getDossier(code)` / `investigateStock(code)`。

---

## 6. frontend

app.md P4-3 が正本。`"use client"`・`lib/api.ts` 経由（ADR-005）。DESIGN.md トークン（density-first）。

| パス | 種別 | 内容 |
|---|---|---|
| `frontend/src/app/watchlist/page.tsx` | 新規 | watchlist 一覧（screens.md #11）。最終調査日表示・stale 警告色・「調査/再調査」ボタン（`investigateStock`）。Dashboard モック watchlist の実配線版 |
| `frontend/src/components/dossier/DossierSection.tsx` | 新規 | 銘柄詳細内のドシエセクション。props `{ code: string }`。`getDossier(code)` → markdown 描画＋ソース一覧（URL リンク・要約・`source_type` バッジ）＋「調査する」ボタン（`investigateStock`）＋ watchlist 追加ボタン |
| `frontend/src/app/stocks/[code]/page.tsx` | 変更 | 既存チャートの下に `DossierSection` を追加（screens.md #3 注記: ドシエは銘柄詳細内のセクション/タブ） |
| `frontend/src/lib/api.ts` | 変更 | Watchlist/Dossier 型・関数（`getWatchlist`/`addWatchlist`/`removeWatchlist`/`getDossier`/`investigateStock`）。`postJSON`/`del` ヘルパは P2 で既存 |
| `frontend/src/lib/mock-data.ts` | 変更 | nav の `Watchlist`（P4）を `href: "/watchlist"` 化・watchlist mock 削除 |
| `frontend/src/app/page.tsx` | 変更 | Dashboard の watchlist カードを `getWatchlist()` に差し替え |

- **markdown 描画（L-24・確定）**: `react-markdown` ＋ `rehype-sanitize`（AI 生成 markdown の XSS 対策。LAN 単一ユーザーだが衛生のため sanitize 併用）。本文は `text-ink leading-[1.55]`・見出しは色階層・ソースは `hairline-soft` 区切りリスト。
- **最終調査日表示**: `last_investigated_at` を一覧に出し、`stale` なら警告色（screens.md §3）。
- **最終調査日が古い/未調査**は `?? "—"` で描く（null の流儀）。

---

## 7. 追加依存

- **frontend**: `react-markdown` ＋ `rehype-sanitize`（L-24・OPEN-L 確定）。ドシエ markdown の描画と AI 生成 markdown の sanitize。
- **backend**: ニュース取得（`fetch_news`）の昼 MCP は外部 MCP サーバ（playwright/fetch）に依存するが、**夜は MCP 非依存**（既存 `httpx` で軽く取得＝_current-state.md §8 の現有依存で足りる）。Python 側に Phase 4 固有の新規ライブラリ追加は原則なし（LLM 要約は既存 `openai>=2.40` アダプタを流用）。data レーンが `fetch_news` の取得手段（昼 MCP の接続方法）を Phase 4 着手時に確定。

---

## 8. テスト計画

DB は既存方針どおり**一時 SQLite**（_current-state.md・conftest）。LLM・`fetch_news`・MCP は必ずモック（ネットを叩かない）。

- **dossier パイプライン**（`investigate_stock`）:
  - URL 重複排除: 既存 url のソースは `upsert_dossier_source` されない（`dossier_source_exists` が True を返すケース）。
  - **本文を保存しない**: 取り込み後、保存されるのは `summary`/`url` のみで全文列が存在しないこと（スキーマ上 body 列なし）。
  - `last_investigated_at`/`updated_at` が更新されること。
  - `mode="nightly"` で `fetch_news` が MCP 非依存経路、`mode="chat"` で MCP 経路を呼ぶこと（`fetch_news` モックで mode 受け渡しを検証）。
  - `summarize_dossier` には記事**全文ではなく要約**が渡ること（LLM モックで入力検証）。
- **Tool registry/dispatch**: `get_dossier`/`investigate_stock`/`fetch_news` が `min_phase=4` で、`openai_tools(phase<4)` では露出しないこと。handler は data の `get_financials`・`fetch_news` をモックして橋渡しのみ検証。例外時に `{error}` を返しループが落ちないこと。
- **repo**: `upsert_dossier`（code conflict で更新）・`upsert_dossier_source`（url conflict）・`dossier_source_exists`・`get_dossier`（sources JOIN）・watchlist の get/add/remove・watchlist JOIN stock_dossiers の `last_investigated_at` 解決。
- **REST**: `GET /watchlist`（stale 算出 21 日境界）・`POST/DELETE /watchlist`・`GET /dossiers/{code}`（未調査時：spec は 404 or 空だが**実装は常に 200＋空**＝要確認・上記参照）・`POST /dossiers/{code}/investigate`（同期で最新ドシエ返却）。
- **migration**: `test_migrations` に `0008_dossier` 適用後 `watchlist`/`stock_dossiers`/`dossier_sources` が存在することを追加。watchlist が二重 CREATE されない（`0004` に無い）こと。
- **夜間巡回**: `last_investigated_at` 古い順に N 件選ぶこと・stale 21 日でフィルタすること（時刻は固定してテスト）。

---

## 9. 着工順（チェックリスト）

1. [ ] `0008_dossier` 移行発行（data-arch 代行）＋ `schema.py` に `watchlist`/`stock_dossiers`/`dossier_sources` を追記（DDL 正本=本仕様）。`test_migrations` に存在確認を追加。
2. [ ] `repo.py` に upsert/get/exists・watchlist の get/add/remove・JOIN（`last_investigated_at`）を追加＋テスト（一時 SQLite）。
3. [ ] `advisor/dossier.py` の `investigate_stock(code, mode)` ＋ `summarize_dossier`（LLM 単発・URL 重複排除・本文非保存）＋テスト（LLM/fetch_news モック）。
4. [ ] `fetch_news`（data レーン・昼 MCP／夜軽め・mode 切替）の実体確定＋スタブ。
5. [ ] Tool 登録: `tools/handlers.py` に `get_dossier`/`investigate_stock`/`fetch_news`・`registry.py` に `min_phase=4` ＋ Phase ゲートテスト。
6. [ ] REST: `GET/POST/DELETE /watchlist`・`GET /dossiers/{code}`・`POST /dossiers/{code}/investigate`（同期・L-23）＋ api テスト。
7. [ ] 夜間巡回ジョブを `NIGHTLY_JOBS` に相乗り（古い順 N 件・stale 21 日・MCP 非依存・02:00 JST）＋テスト。
8. [ ] frontend: `lib/api.ts` 型/関数 → `watchlist/page.tsx`＋Dashboard 配線 → `stocks/[code]` に `DossierSection`（react-markdown+rehype-sanitize）→ investigate 接続。nav の Watchlist を href 化。

---

## 10. このPhaseの[OPEN]

> R3 で大半は `_arbitration.md` で確定済み。残るユーザー裁定（U-8）は `_open-questions.md` 行きで、spec は推奨値を既定として書く（後から設定値で差し替え可）。

| # | 論点 | R3 後の状態 / 既定値 |
|---|---|---|
| **U-8** ✅裁定済み | 夜間ドシエの調査頻度・watchlist 上限 N・stale しきい値 | **確定（※「毎晩 N=3 件」固定枠は後に [ADR-033](../decisions.md) で廃止＝`interval_days`＋夜あたり天井 `DOSSIER_NIGHTLY_MAX` に改訂。以下は旧記録）**: `last_investigated_at` が古い順に **毎晩 N=3 件**／**stale しきい値 = 21 日**（backend 算出）／夜間 cron **02:00 JST**（U-9）。**watchlist に硬い上限は設けない**（古い順 N 件/晩がコストを頭打ちにし、リストが大きくなるとコストは増えず古くなるだけ＝21 日警告で気づく）。`N≧watchlist件数÷21` なら stale ゼロを保てる（N=3 で 63 銘柄まで）。**N は env 既定＋設定 UI ツマミ**（U-5/ADR-028 のコスト設定 UI と同居）。 |
| OPEN-J（確定） | watchlist `stale` 閾値 | **21 日・backend 算出**（L-22・app.md 付録C で確定）。Dashboard モックの「23 日前=stale」と整合。 |
| OPEN-K（確定） | investigate の同期/非同期 | **同期で着工**（処理完了まで待って最新ドシエ返却）。遅ければ「ジョブ ID → ポーリング」へ移行可（L-23）。 |
| OPEN-L（確定） | ドシエ markdown レンダラ | **`react-markdown` ＋ `rehype-sanitize`**（L-24・OPEN-L）。AI 生成 markdown の XSS 対策。 |
| 実機確認（U ではない） | V2 財務エンドポイント（`get_financials` の供給元 `financials` 0005） | data レーン §2.8 の宿題（`/v2/fins/summary` か `/v2/equities/statements` か・実フィールド名）。Phase 2 で器を入れ Phase 4 はそれを読む。 |
