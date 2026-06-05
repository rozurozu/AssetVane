# Phase 1 着工仕様: Trend Vane（全銘柄バッチ＋短期モメンタム検知）

> 出所: roadmap.md Phase 1 / 関連 ADR（ADR-002・ADR-005・ADR-008・ADR-010・ADR-014・ADR-016・ADR-018・ADR-021・ADR-023）。
> チームレビュー（`_drafts/_review.md`）・lead 裁定（`_drafts/_arbitration.md`）反映済み。各レーンドラフト（data-arch §Phase1・quant §Phase1・app §Phase1）を Phase 1 観点で合成。
> **コード未実装＝これは着工仕様**。設計の真実は `docs/`、本書は Phase 0 実装（`stocks`/`daily_quotes`/`JQuantsAdapter`/`backfill`/Alembic `0001`）に**差分で積む**実装図面。
> 単位・スキーマ・型はすべて `_arbitration.md` の正本に一致させる（`is_delayed`/`as_of`・比率 0..1・signals `UNIQUE(date,code,signal_type)`・採番 `0002_fetch_meta`→`0003_signals`）。
> 作成: 2026-06-03。

---

## 0. 目的と完了条件

**目的**（roadmap.md Phase 1）: 全銘柄バッチとスクリーニングを成立させる。Phase 3 の「夜の分析AI」が前提にする **cron 夜間バッチの最小実装をここで前倒し**で導入する。

- 全銘柄の日足を夜間バッチで取得（初回バックフィル＋差分取得・`fetch_meta`）。
- cron 夜間バッチ起動の最小実装＋手動起動 `POST /batch/run`。
- 短期モメンタム（SMA 上抜け・RSI 反転＝機能①）と出来高急増（機能②）を計算し `signals` に保存。
- スクリーニング結果の一覧画面（`/signals`）。

**完了条件**: cron で夜間バッチが全銘柄を処理して `signals` を更新し、一覧画面で「今日の強い銘柄」が見られる。同じ夜のバッチを再実行しても `signals` が重複しない（冪等 UPSERT）。

**留意（roadmap.md Phase 1）**:
- 初回バックフィルは Free 5 req/分。**銘柄ループではなく日付一括取得（全銘柄×1日）の日数ループ**で回す（`fetch_daily_quotes_by_date` が既に部品として実装・実機確認済み）。所要見積は §3.4。
- TA-Lib は ARM ビルド難 → **自前実装（numpy/pandas）を採用**（§4.1）。
- Free は 12 週間遅延。見えるのは約 3 か月前の「強い株」。ロジックは正しく Light 以上で最新化（ADR-008）。レスポンスには `is_delayed`/`as_of` を必ず載せる。

---

## 1. 全体像（何を作るか・前提する Phase 0 の既存物）

### 前提する Phase 0 既存物
- **DB は `stocks`/`daily_quotes` の 2 表のみ**（`schema.py`）。`daily_quotes` は `open/high/low/close/volume/adj_close`（全 Float・nullable）・複合 PK `(code,date)`・`date` は `'YYYY-MM-DD'` 文字列。`volume` は **Float**。
- `JQuantsAdapter`（V2・`x-api-key`）に **`fetch_daily_quotes_by_date(date) -> list[dict]`** が実装済み（`jquants.py:130`・日付一括・約4400行/日）。`_MIN_INTERVAL_SECONDS=13.0` のスロットル＋429 指数バックオフ（最大4回）＋pagination 全ページ集約内蔵。`fetch_master(codes)` は 1 件ずつループ。`is_etf` は常時 0 ハードコード（`jquants.py:171`・Phase 7 で是正予定）。
- `repo.py`: `_upsert`（`on_conflict_do_update`・冪等）・`upsert_stocks`・`upsert_daily_quotes`・`list_stocks`・`get_stock`・`get_quotes`。読みは Connection 受け／書きは `engine.begin()`。戻り値は素の `dict`。
- `app/batch/__init__.py` は**空**（cron なし）。数理/ML 依存は **backend に皆無**（pandas すら未導入）。
- frontend: `lib/api.ts`（`getStocks`/`getStock`/`getQuotes`・型 `Stock`/`Quote`）・`getJSON`。Sidebar nav の Signals は `phase:"P1"` で非活性。Dashboard は完全モック。

### Phase 1 で新設する要素
- **データ層**: `fetch_meta` テーブル（差分取得管理）＋ `signals` テーブル（シグナル事前計算）。
- **取得・バッチ層**: `app/batch/`（runner・jobs・lock・calendar・notify）＋ APScheduler 同居 cron ＋ `POST /batch/run`。
- **数理層**: `app/quant/`（`momentum.py`・`volume_spike.py`・純関数）。
- **API 層**: `GET /signals`（読むだけ）。
- **frontend**: `/signals` 一覧画面＋ `lib/api.ts` に `getSignals`。

### 不変条件（破らない）
- **DB に触れる OS プロセスは FastAPI 1 つだけ**（ADR-005）。夜間バッチは APScheduler で FastAPI プロセス内に同居（裁定決定5）。Next は UI 専用・REST 経由のみ（Prisma 不採用）。
- **書き込みは UPSERT で冪等**（ADR-002・ADR-018）。
- **AI に計算させない**（ADR-014）— Phase 1 は Tool 未配線だが、signals は Python が事前計算した事実を焼く。
- **手法はテスト済みコードで実装**（ADR-016）— momentum/volume_spike は純関数＋既知系列テスト。
- **外部 API はアダプタ越し**（ADR-010）。

---

## 2. スキーマ変更（Alembic 採番表準拠・DDL 全列＋PK＋index＋UNIQUE）

採番表（`_arbitration.md` 決定1・単線チェーン）: `0001_baseline`(既存) → **`0002_fetch_meta`**(down=0001・定義レーン data-arch) → **`0003_signals`**(down=0002・定義レーン quant)。
**移行ファイルの発行（作成）は data-arch が一元管理**し、`signals` の**定義内容の正本は quant** が持つ。`schema.py` の `metadata` が単一の真実、`init_db()` が起動時に `upgrade head`。

### 2.1 `fetch_meta`（差分取得管理・`0002_fetch_meta`・定義 data-arch）

data-model.md §6 準拠 ＋ `updated_at` を追加（運用時に「いつ最後にバッチが回ったか」を見るため・**[DOCS要修正]** data-model.md §6 に `updated_at` 列追記）。

```sql
CREATE TABLE fetch_meta (
    source             TEXT NOT NULL,   -- データ種別キー（'daily_quotes' / 'stocks' / 将来 'index_quotes' / 'financials'）
    last_fetched_date  TEXT,            -- 取得済みの最終営業日 'YYYY-MM-DD'（未取得なら NULL）
    updated_at         TEXT,            -- この行の更新時刻（ISO8601 UTC）
    PRIMARY KEY (source)
);
```

`schema.py` 追記（既存 `Table` 流儀）:
```python
# 差分取得の進捗管理（data-model.md §6・ADR-018 の部分失敗からの再開）。
fetch_meta = Table(
    "fetch_meta",
    metadata,
    Column("source", String, primary_key=True),
    Column("last_fetched_date", String),  # 'YYYY-MM-DD'
    Column("updated_at", String),         # ISO8601 UTC
)
```

### 2.2 `signals`（シグナル事前計算・`0003_signals`・定義 quant）

data-model.md §4 を確定。**`(date, code, signal_type)` に UNIQUE** を張り冪等 UPSERT を可能にする（**[DOCS要修正]** data-model.md §4 は PK が `id` のみで UNIQUE 未言及 → 追記）。

```python
# backend/app/db/schema.py に追記（data-model.md §4・ADR-002）
signals = Table(
    "signals",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("date", String, nullable=False),          # 算出日 'YYYY-MM-DD'
    Column("code", String, nullable=False),          # 銘柄/業種コード（5桁）
    Column("signal_type", String, nullable=False),   # 'momentum'|'volume_spike'|'ai_alpha'(P5)|'lead_lag'(P7)
    Column("score", Float, nullable=False),          # 0.0〜1.0 のスコア・強度
    Column("payload", String),                       # JSON 文字列（指標値・根拠）
    UniqueConstraint("date", "code", "signal_type", name="uq_signals_date_code_type"),
    Index("ix_signals_date_type", "date", "signal_type"),
    Index("ix_signals_code", "code"),
)
```

- `payload` は **TEXT に JSON 文字列**（SQLite に JSON 型なし・既存方針通り `json.dumps`/`json.loads`）。各 payload に `schema_version`(int) を必ず入れる。
- `label`（短文）・`change_5d`（5 日騰落率・符号付き小数）は **quant が payload に格納**（B-6）。`company_name` は signals に持たず**ルータが `signals JOIN stocks` で補完**（行レベルに名前を焼かない）。
- `code` への FK は張らない（lead_lag では業種コードが入りうる・生データ流儀に合わせる）。
- インデックス: `(date, signal_type)`（一覧・通知の主クエリ）＋ `code`（銘柄詳細横断）。
- **設計思想（ADR-026）**: signals は **AI Advisor に食わせる「材料」**で、`score` は**連続値（0..1）**。閾値は「保存時の破壊的ゲート」ではなく **`notable` フラグ＋読み取り時の既定カットオフ**にすぎない。夜間バッチは**低フロア以上の行を広めに保存**して near-miss を残し、絞り込みは AI（`screen_stocks` の `min_score` 等）と一覧 UI が行う。個別銘柄の素の指標は保存せず `get_indicators(code)` で都度計算（L-13）。**human 向け一覧は副産物**で、主経路は「AI が Tool で読み、根拠つきで推す」。

> **採番予約（本 Phase 外）**: `0004_portfolio_and_assets`(P2・data-arch)・`0005_financials`(P2・data-arch)・`0006_advisor_state`(P3・ai-advisor)・`0007_screening`(P1・ADR-031・後付け割り込み)・`0008_dossier`(P4・ai-advisor・watchlist はここに一本化)・`0009_notifications`(P6・data-arch)。Phase 1 では作らない。

---

## 3. データ取得・バッチ・cron

該当: roadmap.md Phase 1 / jquants.md §4 / ADR-002・ADR-018。

### 3.1 新規/変更ファイル一覧

| 種別 | パス | 内容 |
|---|---|---|
| 変更 | `backend/app/db/schema.py` | `fetch_meta`・`signals` を追記（§2） |
| 新規 | `backend/alembic/versions/0002_fetch_meta.py` | `fetch_meta` の autogenerate 移行（発行 data-arch） |
| 新規 | `backend/alembic/versions/0003_signals.py` | `signals` の autogenerate 移行（発行 data-arch・定義 quant） |
| 変更 | `backend/app/db/repo.py` | `upsert_fetch_meta`/`get_fetch_meta`/`get_max_quote_date`/`list_stock_codes`／`upsert_signals`/`get_signals`/`get_latest_signal_date` 追加 |
| 変更（充填） | `backend/app/batch/__init__.py` | `run_nightly` を re-export |
| 新規 | `backend/app/batch/runner.py` | 夜間バッチのオーケストレーション（ロック→ジョブ列→集約→通知） |
| 新規 | `backend/app/batch/jobs/__init__.py` | `NIGHTLY_JOBS`（実行順の単一の真実） |
| 新規 | `backend/app/batch/jobs/fetch_quotes.py` | 営業日ループの日足取得（初回＋差分を同一経路） |
| 新規 | `backend/app/batch/jobs/sync_master.py` | 全銘柄マスタ同期（§3.6・[OPEN]） |
| 新規 | `backend/app/batch/jobs/calc_signals.py` | quant 純関数を全銘柄ループで呼び `upsert_signals`（data-arch×quant 境界） |
| 新規 | `backend/app/batch/lock.py` | `fcntl.flock` による書き手相互排他 |
| 新規 | `backend/app/batch/calendar.py` | 営業日候補生成（土日除外＋祝日は空レスで吸収） |
| 新規 | `backend/app/batch/notify.py` | Discord エラー通知（Phase 1 はエラーのみ） |
| 新規 | `backend/app/routers/batch.py` | `POST /batch/run`（手動起動・cron と共用） |
| 変更 | `backend/app/main.py` | `batch_router` を `include_router`・lifespan に APScheduler 起動 |
| 変更 | `backend/app/config.py` | `BATCH_SCHEDULER_ENABLED`・`BATCH_CRON_HOUR/MINUTE`・`BATCH_TZ`・`BACKFILL_YEARS`・`JQUANTS_MIN_INTERVAL_SECONDS` 追加 |
| 変更 | `backend/app/db/engine.py` | `_set_sqlite_pragma` に `PRAGMA busy_timeout=5000` を 1 行追加 |
| 変更 | `backend/app/scripts/backfill.py` | 既存 3 銘柄ループは互換温存しつつ `run_nightly(full_backfill=True)` を呼ぶ薄い CLI に寄せる |
| 変更 | `backend/pyproject.toml` | 依存追加（§7） |
| 変更 | `compose.yaml` | cron 方式 C のため追加サービス不要（APScheduler 同居）。`.env` に上記設定を追記する程度 |

### 3.2 repo に追加する関数（型注釈つきシグネチャ）

```python
# fetch_meta（data-arch）
def upsert_fetch_meta(source: str, last_fetched_date: str) -> None: ...
    # index_elements=["source"]。updated_at は関数内で UTC now を入れる。

def get_fetch_meta(conn: Connection, source: str) -> dict[str, Any] | None: ...
    # 1 行 or None。last_fetched_date が None/未存在なら「初回」。

def get_max_quote_date(conn: Connection) -> str | None: ...
    # SELECT MAX(date) FROM daily_quotes。fetch_meta 不在時の自己修復フォールバック。

def list_stock_codes(conn: Connection) -> list[str]: ...
    # stocks の全 code（calc_signals/進捗ログ用）。

# signals（quant 定義・data-arch/app と契約）
def upsert_signals(rows: list[dict]) -> int: ...
    # index_elements=["date","code","signal_type"]。冪等 UPSERT。

def get_signals(conn: Connection, date: str | None, signal_type: str | None,
                code: str | None = None, limit: int = 100) -> list[dict]: ...
    # date 降順・score 降順。date 省略時は get_latest_signal_date で最新算出日を自動採用。

def get_latest_signal_date(conn: Connection, signal_type: str | None = None) -> str | None: ...
```

### 3.3 バッチ本体のシグネチャ・ジョブ構造

`runner.run_nightly()` が「ロック取得 → ジョブを順に実行 → 例外を集約して Discord 通知 → ロック解放」。各ジョブは独立・冪等・部分失敗から再開可能（`fetch_meta`）。

```python
# backend/app/batch/runner.py
from dataclasses import dataclass

@dataclass
class JobResult:
    name: str
    ok: bool
    rows: int          # 取得/UPSERT 行数（観測用）
    detail: str        # 進捗・エラーメッセージ

def run_nightly(*, full_backfill: bool = False) -> list[JobResult]: ...
    # full_backfill=True: fetch_meta を無視して BACKFILL_YEARS 分を頭から取り直す（初回/復旧）。
    # False（既定）: fetch_meta['daily_quotes'].last_fetched_date の翌営業日から today まで差分。
    # with batch.lock.acquire(): で囲む。JobResult を集約し ok=False が1件でもあれば notify.error()。

# backend/app/batch/jobs/fetch_quotes.py
def run(*, full_backfill: bool) -> JobResult: ...
    # 1) start_date: full_backfill なら today-BACKFILL_YEARS、そうでなければ fetch_meta 翌営業日。
    # 2) calendar.candidate_days(start, today) を営業日ループ。
    # 3) 各日 d で adapter.fetch_daily_quotes_by_date(d) → 空でなければ
    #    repo.upsert_daily_quotes(rows) → repo.upsert_fetch_meta('daily_quotes', d)（1 日進むごとに前進・再開可能）。
    # 4) 空配列の日（非営業日）はスキップしつつ fetch_meta も前進。
    # 5) 例外（JQuantsError 等）は握って JobResult(ok=False) で返す（runner が通知）。

# backend/app/batch/jobs/sync_master.py
def run() -> JobResult: ...  # stocks の全量同期（§3.6・[OPEN]）。

# backend/app/batch/jobs/calc_signals.py（data-arch が糊・quant 関数を呼ぶ）
def run() -> JobResult: ...
    # 1) list_stock_codes で全銘柄。各 code の日足を get_quotes で DataFrame 化（adj_close/volume）。
    # 2) quant.momentum.compute_momentum / quant.volume_spike.compute_volume_spike を呼ぶ（純関数）。
    # 3) None でなければ signals 行を作り upsert_signals にまとめて渡す（冪等）。
    # 4) quant モジュールは DB を知らない。signals 書き込みはこのジョブ側。
```

`batch/jobs/__init__.py`（実行順の単一の真実・後続 Phase はここに append）:
```python
# マスタ → 日足取得 → シグナル計算（当日の事実が揃ってから算出）。
NIGHTLY_JOBS = [sync_master.run, fetch_quotes.run, calc_signals.run]
```

### 3.4 営業日ループ × 日付一括取得・所要見積

- **方式 X（推奨折衷）**: `calendar.candidate_days(start, end)` が **土日を曜日判定で除外**、祝日・臨時休場は `fetch_daily_quotes_by_date(d)` の**空配列で吸収**（カレンダー API に依存しない）。営業日テーブルを持たず堅牢（B-3／裁定 L-3）。
- 2 年で全カレンダー日 約730・営業日約490。土日除外で **約520 req**。`_MIN_INTERVAL_SECONDS=13.0` で **約 100〜150 分**（Free・jquants.md §4 の見積と整合）。
- Light 移行（60 req/分）なら `JQUANTS_MIN_INTERVAL_SECONDS=1.0` で約 8 分。
- `fetch_meta` 前進単位は **1 営業日ごと**（途中で落ちても翌回は続きから＝ADR-018 部分失敗再開）。429 リトライは既存（最大4回・指数バックオフ）を流用。

```python
# backend/app/batch/calendar.py
def candidate_days(start: str, end: str) -> Iterator[str]: ...
    # start〜end を 'YYYY-MM-DD' で yield。土日は除外（曜日判定）。祝日はバッチ側が空レスで吸収。
```

### 3.5 fetch_meta 差分・ロック・busy_timeout

**差分**: `fetch_meta['daily_quotes'].last_fetched_date` を起点に翌営業日から today まで。NULL/未存在なら初回扱い（`full_backfill` を促す or `get_max_quote_date` で自己修復）。

**ロック（裁定決定5・B-9）**: DB に触れる OS プロセスは FastAPI 1 つ。夜間バッチは APScheduler で同居 → バッチ書き込みと昼の API 書き込みは**同一プロセス内で直列化**され書×書競合は原理的に起きない。
- **`fcntl.flock`（`LOCK_EX | LOCK_NB`・`data/batch.lock`）**: 別 OS プロセスで起動されうる手動バッチ（`python -m app.scripts.backfill`）と同居スケジューラの相互排他。取れなければ `BatchAlreadyRunning`（`/batch/run` は 409・cron はログのみスキップ）。標準ライブラリのみ。
- **APScheduler `max_instances=1`**: プロセス内の夜間ジョブ直列化（二重防御）。
- **`PRAGMA busy_timeout=5000`**: `engine.py` の `_set_sqlite_pragma` に 1 行追加し稀な競合をリトライ吸収（**B-9 で必要なコード上の追加はこれだけ**）。

```python
# backend/app/batch/lock.py
class BatchAlreadyRunning(RuntimeError): ...

@contextmanager
def acquire(lock_path: str | None = None) -> Iterator[None]: ...
    # fcntl.flock(fd, LOCK_EX | LOCK_NB)。取れなければ BatchAlreadyRunning。
```

**[DOCS要修正]** ADR-002 / data-model.md に「書き手の系統（夜バッチ／昼手入力／チャット承認）と衝突回避の実際（同一プロセス直列化＋flock＋busy_timeout）」を 1 段落補注（DOC-9）。

### 3.6 全銘柄マスタの取得（`sync_master`・[OPEN]）

現状 `fetch_master(codes)` は 1 件ずつループ → 全銘柄（約4000）×13 秒 ＝ 14 時間超で非現実的（裁定 L-5）。
- **推奨**: `/v2/equities/master` を **`code` 無しで叩くと全銘柄が返るか実機確認**（`bars/daily` の日付一括と同じパターン）。返れば `fetch_master_all() -> list[dict[str, Any]]` を 1〜数 req で取得。**jquants.md 未記載 → 要再確認**。
- **代替（確認不可なら）**: マスタは初回＋週次で足りる。`bars/daily` で得た全 `code` を種に、`stocks` に無い code だけ `fetch_master([code])` で後追い補完（新規分だけの少 req）。
- `is_etf` は **Phase 1 では是正しない**（docs どおり Phase 7 温存・裁定 L-4）。全銘柄取得で ETF/REIT 行が `is_etf=0` で混ざるが、`market_code` で実質判別できることをコメントで残す。signals の ETF 除外要否は quant と要確認（推奨は後回し）。

### 3.7 cron（APScheduler 同居・方式 C・裁定 L-1）

ADR-021 の最小構成（DB コンテナなし・サービスは backend/frontend の 2 つ）に整合させ、**追加コンテナ 0・依存 1 つ（apscheduler）** で `run_nightly()` を「毎晩 cron」「`POST /batch/run` 手動」の両口から同一プロセス・同一関数で呼ぶ（ADR-011「1つの脳・2つの起動口」）。

- `main.py` lifespan で `AsyncIOScheduler`（または `BackgroundScheduler`）を起動し `add_job(run_nightly, CronTrigger(hour=BATCH_CRON_HOUR, minute=BATCH_CRON_MINUTE, timezone=BATCH_TZ), max_instances=1, coalesce=True)`。
- **dev の `--reload` 二重起動ガード**: `BATCH_SCHEDULER_ENABLED`（dev 既定 false / prod true）でガード。
- バッチ本体は同期 I/O（SQLite/httpx 同期）なので `AsyncIOScheduler` から呼ぶ場合は `run_in_executor` でスレッドに逃がす。
- 既定起動時刻 **02:00 JST（TZ=Asia/Tokyo）**（U-9・推奨値）。
- **将来 B（専用 batch サービス）へ移れるよう** `run_nightly()` はプロセス非依存に保つ（Phase 2〜3 で重くなったら切替）。

### 3.8 `POST /batch/run` 契約（正本 = data-arch・B-11・裁定決定6）

- **body**: `{ full_backfill?: bool }`（既定 false。true で BACKFILL_YEARS 分を頭から取り直す＝初回/復旧）。`tasks[]` は YAGNI（Phase 1 は全ジョブ実行のみ）。
- **成功**: **`202 Accepted` `{ started: bool, job_id?: string }`**（非同期受付。初回バックフィルは約100〜150分で HTTP をブロックできないため・裁定 L-2）。
- **ロック競合**: **`409 Conflict`**（flock が取れない＝既にバッチ実行中）。

```python
# backend/app/routers/batch.py（HTTP 入出力のみ・ロジックは batch/ に委譲）
class BatchRunRequest(BaseModel):
    full_backfill: bool = False

class BatchRunResponse(BaseModel):
    started: bool
    job_id: str | None = None

@router.post("/batch/run", response_model=BatchRunResponse, status_code=202)
def run_batch(req: BatchRunRequest) -> BatchRunResponse: ...
    # 起動前に lock.acquire() を試し、取れなければ HTTPException(409)。
    # 取れたら APScheduler の add_job(run_nightly, next_run_time=now) か BackgroundTasks で起動し即 202。
    # 進捗は fetch_meta.last_fetched_date / Discord で追う。
```

### 3.9 Discord エラー通知（Phase 1 最小）

ADR-018: 夜間バッチ失敗時に `DISCORD_WEBHOOK_URL` へ通知。Phase 1 は**エラー時のみ**（成功サマリ・シグナル通知は Phase 6）。

```python
# backend/app/batch/notify.py（P6 で webhook.py に昇格）
def error(title: str, detail: str) -> None: ...
    # DISCORD_WEBHOOK_URL 未設定なら no-op（ログのみ）。httpx POST、失敗しても握りつぶす。
```

---

## 4. シグナル計算（quant）

該当: ADR-014/016（手法はコード・AI は計算しない）・ADR-008（12週遅延）・data-model.md §4。**比率・score はすべて 0..1**（`change_5d` のみ符号付き騰落率）。

### 4.1 指標ライブラリ選定の結論 — 自前実装（numpy/pandas）

| 候補 | ARM/Docker | numpy2 | 保守 | 評価 |
|---|---|---|---|---|
| TA-Lib（C 拡張） | ✕ ARM ビルド難（ADR-021） | 要ラッパ | 重い | 不採用 |
| pandas-ta（純Python） | ○ | △ 本家 numpy2 非対応・fork 頼み | ⚠ 2026-07-01 までにアーカイブ予定 | 不採用 |
| **自前実装（numpy/pandas）** | ◎ C 依存なし | ◎ | ◎ 自分でテスト可 | **★採用** |

**結論: 自前実装。** P1 で必要なのは **SMA・Wilder RSI(14)・出来高移動平均の 3 つだけ**で 10〜20 行で正確に書け、ADR-016「テスト済みコードで実装」と完全整合。ARM ビルド難・pandas-ta アーカイブリスクを両方回避。
> 責任分界（B-10・決定6）: **依存選定＝quant**／**Docker クロスビルド検証の段取り＝data-arch**。numpy/pandas を入れたイメージが aarch64 で通るかが **Phase 1 着手の最初のゲート**。

### 4.2 価格系列・adj_close 固定・再現性

- **トレンド/モメンタム/リターン系はすべて `adj_close`（調整後終値）で計算**（分割の段差を除去。未調整 `close` で SMA/RSI を計算すると分割日に偽シグナル）。
- **出来高は未調整 `volume` のまま**使う（spike は比率なので区間内で係数が揃えば概ね保たれる）。分割をまたぐ窓は `adj_warning` を立てる。
- **high/low を直接使う指標（ATR・ストキャス等）は P1 では実装しない**（未調整 high/low と調整 close の混在を避ける）。RSI は `adj_close` の差分で計算（終値ベースが標準）。
- **adj_close 欠損 = skip**（裁定 L-26）: 計算窓内に null があればその銘柄・その日のシグナルを生成しない。前方補完・補間しない（数字を作らない＝ADR-014）。
- **再現性（ADR-016）**: 各手法は「入力 DataFrame → 出力 dict/None」の純関数（DB I/O を持たない）。既知系列テスト＋backtest 再計算が可能。quant モジュールは `backend/app/quant/` 配下、バッチ（`calc_signals.py`）から呼ばれる。

### 4.3 momentum シグナル（`backend/app/quant/momentum.py`）

```python
def compute_momentum(quotes: pd.DataFrame) -> dict | None:
    """1 銘柄の日足から momentum シグナルを 1 件算出（最新日基準）。
    quotes: columns=[date, adj_close]（date 昇順）。戻り値は signals payload 候補 dict、
    不成立/データ不足なら None。（ADR-016: 純関数・DB 非依存・docs/data-model.md §4）"""
```

確定パラメータ（すべて `adj_close`）:

| 要素 | 確定値 | 式 |
|---|---|---|
| 短期 SMA | **25 日** | `sma25 = adj_close.rolling(25).mean()` |
| 長期 SMA | **75 日** | `sma75 = adj_close.rolling(75).mean()` |
| ゴールデンクロス | 当日 sma25>sma75 かつ前日 sma25<=sma75 | `gc = (sma25.shift(1) <= sma75.shift(1)) & (sma25 > sma75)` |
| RSI | **Wilder RSI(14)** | §4.3.1 |
| RSI 反転（買い） | 前日 RSI<30 → 当日 >=30 | `rsi_rev = (rsi.shift(1) < 30) & (rsi >= 30)` |
| 最低データ長 | **76 行以上** | 不足なら `None` |

**スコア定義（連続 0..1・ADR-026 / 開始既定 U-1）**:

momentum は「今日クロスしたか(0/1)」のイベント値ではなく、**連続の上昇トレンド強度**にする。こうすると「ゴールデンクロス目前(near-miss)」が濃淡で表現でき、AI が判断材料に使える（**閾値で材料を捨てない**＝ADR-026）。
```
gap      = (sma25 - sma75) / sma75                       # トレンドの向き・強さ（連続）
trend    = clip(0.5 + gap / (2*TREND_BAND), 0, 1)        # gap=0(クロス点)→0.5 / +TREND_BAND→1 / -TREND_BAND→0
rsi_norm = clip((rsi14 - RSI_LOW) / (RSI_HIGH - RSI_LOW), 0, 1)
score    = clip(W_TREND*trend + W_RSI*rsi_norm + GC_BOOST*golden_cross + REV_BOOST*rsi_reversal, 0, 1)
```
- 「クロス目前で上向き」は中スコア、「今日クロス＋oversold からの反転」は高スコア。`golden_cross`/`rsi_reversal`（上表の bool）は**加点ブースター**。
- **開始既定の名前付き定数**（`momentum.py` のモジュール定数・**env 不可**・将来 `method_settings` へ＝ADR-027）: `TREND_BAND=0.05`・`RSI_LOW=30`・`RSI_HIGH=70`・`W_TREND=0.6`・`W_RSI=0.4`（U-1 の 0.6/0.4 を継承）・`GC_BOOST=0.15`・`REV_BOOST=0.15`。
- **保存は低フロア** `score >= MOMENTUM_FLOOR`（既定 0.3）の行のみ UPSERT — near-miss を残しつつ全 4000 銘柄保存は避ける。これは破壊的ゲートではなく**保存量の足切り**で、絞り込みは読み取り時/AI が `screen_stocks` で動かす（ADR-026）。データ不足(76 行未満)・`adj_close` 欠損は `None`。`notable`（強い目印）は `golden_cross or score>=0.6` を payload に格納。

#### 4.3.1 Wilder RSI(14) 確定式
```
delta    = adj_close.diff()
gain     = delta.clip(lower=0)
loss     = (-delta).clip(lower=0)
avg_gain = gain.ewm(alpha=1/14, adjust=False, min_periods=14).mean()  # Wilder 平滑
avg_loss = loss.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
rs  = avg_gain / avg_loss
rsi = 100 - 100 / (1 + rs)
# avg_loss==0 のとき rsi=100（ゼロ割回避）
```
単純移動平均版ではなく Wilder 平滑（TA-Lib・各証券会社チャートの既定と一致）。テストもこの定義で固定。

**signals payload（`signal_type='momentum'`）**:
```json
{
  "date": "2025-12-15", "code": "72030", "signal_type": "momentum", "score": 0.52,
  "payload": {
    "trend": 0.58, "gap": 0.008, "golden_cross": true, "rsi_reversal": false, "notable": true,
    "sma25": 2850.4, "sma75": 2790.1, "rsi14": 41.2, "adj_close": 2901.0,
    "label": "SMA25/75 ゴールデンクロス", "change_5d": 0.034, "schema_version": 1
  }
}
```

### 4.4 volume_spike シグナル（`backend/app/quant/volume_spike.py`）

```python
def compute_volume_spike(quotes: pd.DataFrame) -> dict | None:
    """1 銘柄の日足から出来高急増シグナルを 1 件算出（最新日基準）。
    quotes: columns=[date, volume, adj_close]（date 昇順）。不成立/不足なら None。"""
```

確定パラメータ（U-2・既定）:

| 要素 | 確定値 | 定義 |
|---|---|---|
| 基準平均 | 過去 20 営業日 volume 単純平均（当日除く） | `vol_ma20 = volume.shift(1).rolling(20).mean()` |
| spike 比率 | `ratio = volume / vol_ma20` | — |
| **notable 閾値** | **ratio >= 3.0**（`notable` フラグ・既定の表示カットオフ） | 平常の 3 倍を「急増」と目印（**破壊的ゲートではない**＝ADR-026） |
| **保存フロア** | **ratio >= VOLUME_FLOOR（既定 1.5）** | near-miss を残しつつ全保存は避ける |
| 出来高フロア | **vol_ma20 >= 50,000 株** | 低流動性除外 |
| 最低データ長 | **21 行以上** | 不足なら `None` |
| 分割警告 | 窓内 `adj_close` の段差比が大きい → `adj_warning=true` | §4.2（未調整 volume 段差の自衛） |

**スコア定義（連続 0..1 にクリップ）**:
```
score = min(ratio / 10.0, 1.0)   # ratio=3 → 0.3、ratio>=10 → 1.0
```
`ratio >= VOLUME_FLOOR(1.5)` の行を保存し、`notable = ratio >= 3.0` を payload に持つ。絞り込み・カットオフは読み取り時/AI 側（ADR-026）。`VOLUME_FLOOR`/notable 閾値は `volume_spike.py` のモジュール定数（**env 不可**・将来 `method_settings`＝ADR-027）。

**signals payload（`signal_type='volume_spike'`）**:
```json
{
  "date": "2025-12-15", "code": "68570", "signal_type": "volume_spike", "score": 0.42,
  "payload": {
    "volume": 4200000.0, "vol_ma20": 1000000.0, "ratio": 4.2, "notable": true, "adj_warning": false,
    "label": "出来高 平常の4.2倍", "change_5d": -0.012, "schema_version": 1
  }
}
```

---

## 5. REST API 契約（`GET /signals`）

該当: app §Phase1・screens.md #4・正本 = `get_signals`（`_arbitration.md` 決定2）。**シグナルは夜間バッチが事前計算済み・API は読むだけ**。

### 5.1 `GET /signals?date=&type=&limit=`
- **query**:
  - `date`（任意・`YYYY-MM-DD`）: 省略時は**最新算出日**（backend が `get_latest_signal_date` で解決）。
  - `type`（任意）: `momentum` | `volume_spike` | `ai_alpha`(P5) | `lead_lag`(P7)。省略時は全 type。
  - `limit`（任意・既定 **100**）: スコア降順の上限（OPEN-B・quant と最終確認）。
- **response**: トップに `date`/`is_delayed`、行は名前を持たない（`company_name` はルータ JOIN）。**行レベル `date` は持たない**（トップのみ・B-6）。

```ts
// lib/api.ts に追加（backend Pydantic と 1:1）
export type SignalType = "momentum" | "volume_spike" | "ai_alpha" | "lead_lag";
export interface SignalPayload {
  label?: string;                 // 一覧の「シグナル」列の短文（quant が格納）
  change_5d?: number | null;      // 5日騰落率（符号付き小数・quant が格納）
  [k: string]: unknown;           // momentum/volume_spike の type 固有指標（quant 確定）
}
export interface Signal {
  code: string;
  company_name: string | null;    // signals JOIN stocks（ルータ補完・B-6）
  signal_type: SignalType;
  score: number;                  // 0..1
  payload: SignalPayload;
}
export interface SignalsResponse {
  date: string;                   // 実際に返した算出日（最新解決後）
  is_delayed: boolean;            // 遅延フラグ（横断・正本）
  signals: Signal[];              // score 降順
}
export function getSignals(opts?: { date?: string; type?: SignalType; limit?: number }): Promise<SignalsResponse> {
  const p = new URLSearchParams();
  if (opts?.date) p.set("date", opts.date);
  if (opts?.type) p.set("type", opts.type);
  if (opts?.limit != null) p.set("limit", String(opts.limit));
  const qs = p.toString();
  return getJSON<SignalsResponse>(`/signals${qs ? `?${qs}` : ""}`);
}
```

- **backend Pydantic（案）**: `SignalOut`（= `Signal`）・`SignalsResponse`。`payload` は `dict[str, Any]`（DB は TEXT/JSON）。`company_name` はルータで `signals JOIN stocks` 補完。`is_delayed` は鮮度判定（Free=true）、`date` は最新解決後の値をトップに。
- ルータは HTTP 入出力のみ（ロジックは持たない）・例外は境界で `HTTPException` 翻訳。供給源は `repo.get_signals`/`get_latest_signal_date`。

> **Tool 契約（Phase 3 で配線・正本は `_arbitration.md` 決定2）**: `get_signals(date?, type?)` の返却は上記 `SignalsResponse` と同形。`get_indicators(code)` は P1 オンザフライ再計算（裁定 L-13・平坦 `{code, as_of, adj_close, sma25, sma75, rsi14, vol_ma20, is_delayed}`・sma5 は計算しない）。`screen_stocks(criteria)` は各 item が `indicators`（payload ではない）。Phase 1 では Tool 未配線だが、`repo.get_signals` と quant 純関数がその供給源になるよう型を揃えておく。

### 5.2 `GET /indicators/{code}?from=&to=`（チャート overlay 用・都度計算）

該当: チャート表示（ユーザー要望）・L-13（指標は保存せず都度計算）。**1 銘柄ぶんを `daily_quotes` から再計算して返す**（保存しない・数ミリ秒）。Tool `get_indicators`（Phase 3）と計算ロジック（§4.3.1 等）を共有する、チャート用の「期間の時系列」版。

```ts
// lib/api.ts に追加
export interface IndicatorPoint {
  date: string; adj_close: number;
  sma25: number | null; sma75: number | null; rsi14: number | null; vol_ma20: number | null;
}
export interface IndicatorMarker {            // signals 履歴からの発火印
  date: string; signal_type: SignalType; score: number; label?: string;
}
export interface IndicatorsResponse {
  code: string; is_delayed: boolean;
  points: IndicatorPoint[];                   // ライン用の時系列（都度計算）
  markers: IndicatorMarker[];                 // signals テーブル履歴（保存ゼロの追加）
}
export function getIndicators(code: string, opts?: { from?: string; to?: string }): Promise<IndicatorsResponse> {
  const p = new URLSearchParams();
  if (opts?.from) p.set("from", opts.from);
  if (opts?.to) p.set("to", opts.to);
  const qs = p.toString();
  return getJSON<IndicatorsResponse>(`/indicators/${code}${qs ? `?${qs}` : ""}`);
}
```
- ライン（SMA/RSI）は都度計算、`markers` は `signals` テーブル履歴を読む（新規ストレージ不要）。
- Tool `get_indicators(code)`（Phase 3・正本）は「最新日の素の指標値」を返す版。本エンドポイントは期間時系列を返すチャート版で、式を共有する。

---

## 6. frontend（シグナル一覧画面＋チャート overlay）

該当: app §P1-2/P1-3・screens.md #4・DESIGN.md トークン。

### 6.1 新規/変更ファイル
| 種別 | パス | 内容 |
|---|---|---|
| 新規 | `frontend/src/app/signals/page.tsx` | 「今日の強い銘柄」一覧（`"use client"`） |
| 変更 | `frontend/src/lib/api.ts` | `SignalType`/`Signal`/`SignalsResponse`/`SignalPayload` 型・`getSignals` 追加 |
| 変更 | `frontend/src/lib/mock-data.ts` | nav の Signals を `{phase:"P1"}` → `{href:"/signals"}` 化（非活性解除） |

> Dashboard の signals 実配線（`getSignals({limit:5})`）と `mock-data.signals` 削除は **Phase 2 着手時**（Dashboard は今はモックのまま＝CLAUDE.md）。Phase 1 では Signals 専用ページのみ実配線する。

### 6.2 `app/signals/page.tsx`
- 内部 state: `data: SignalsResponse | null` / `error` / `type フィルタ`。
- 構成: ヘッダー（タイトル「Signals（Trend Vane）」＋ `data.date` を「<date> 算出」表示・`is_delayed` 時は「12週遅延・<date>基準」注記）→ **type 切替タブ**（全 / momentum / volume_spike）→ **テーブル**（コード/銘柄・スコア（バー＋数値）・5日（`change_5d`）・シグナル（`payload.label`））。
- 行クリックで `/stocks/{code}` へ（`Link`）。
- **DESIGN.md トークン**: コンテナ `surface-1`/区切り `hairline`・`hairline-soft`／スコアバー `bg-accent`／5日は `text-up`/`text-down`／シグナルバッジ `bg-surface-2 text-ink-muted`／数値は `num`(tnum)。空/エラー/読み込み中は Stocks 一覧と同じ 3 状態文言の流儀。
- **SignalsTable 抽出は当面しない**（推奨: まず Signals ページにインライン、Dashboard 実配線時=P2 に共通化）。

### 6.3 チャート指標 overlay（基盤・着工順の最後・後回し可）

該当: ユーザー要望（チャートに指標とシグナルを重ねたい）。**Phase 1 では「重ねられる土台」まで**（チャート自体は暫定・Phase 0 の `CandleChart` 拡張）。無理なら後回し可。

| 種別 | パス | 内容 |
|---|---|---|
| 変更 | `frontend/src/components/CandleChart.tsx`（または併設コンポーネント） | SMA25/75 ライン・RSI サブパネル・シグナル発火マーカーの重ね描き |
| 変更 | `frontend/src/lib/api.ts` | `getIndicators`/`IndicatorsResponse`/`IndicatorPoint`/`IndicatorMarker` 追加（§5.2） |

- **指標は表示/非表示トグル**（ユーザー要望）: SMA25・SMA75・RSI・シグナル印を各々チェックで on/off。状態は当面ローカル（`useState`／必要なら `localStorage`）。
- データは `getIndicators(code, {from,to})`。ライン=都度計算・マーカー=signals 履歴。
- DESIGN.md トークンで配色。**暫定実装なので深追いしない**（チャート基盤が固まったら作り込む）。

---

## 7. 追加依存ライブラリ（ARM ビルドゲート）

backend `pyproject.toml`（uv 管理・ADR-023）に追加:
```
numpy>=2.0      # quant 自前指標（§4.1）
pandas>=2.2     # 時系列
apscheduler>=3.10  # cron 方式 C（§3.7・裁定 L-1）
```
- `scipy`/`PyPortfolioOpt`/`lightgbm` は Phase 1 不要（P2/P5）。ファイルロック（fcntl）・Discord（httpx 既存）は標準ライブラリで追加なし。
- **ARM ビルドゲート（Phase 1 着手の最初のゲート・B-10・決定6）**: numpy/pandas を入れた backend イメージが **aarch64（ラズパイ）で通るか**を、コードを書き始める前に **data-arch がクロスビルドで検証**する（ADR-021「別 PC でクロスビルド → ラズパイは pull のみ」）。numpy/pandas は manylinux aarch64 wheel が配布済みのため pip で入る見込みだが実機確認必須。ここが通らないと夜間バッチの数理計算がラズパイで動かない＝**他作業より先に潰す**。

---

## 8. テスト計画（pytest・一時 SQLite・既存 conftest 流儀）

数理は実 API を叩かず純関数テスト・DB は触らず一時 SQLite。

**quant（既知系列 → 既知シグナル・ADR-016）** — `tests/test_quant_momentum.py` / `test_quant_volume_spike.py`:
- GC 成立: sma25 が sma75 を当日上抜けする手組み系列 → `golden_cross=True`・`score>=0.6`。
- GC 非成立（既に上）: ずっと sma25>sma75 → `golden_cross=False`（瞬間のみ拾う）。
- RSI 既知値: Wilder RSI(14) を公開定番系列（Wilder 原典 14 日例題等）で既知値 ±0.1 一致。
- RSI 反転: 28→32 跨ぎ系列 → `rsi_reversal=True`。
- データ不足: 50 行 → `None`（76 行未満）。
- volume spike 成立: 平常 100 万株 ×20 日 ＋当日 400 万株 → `ratio=4.0`・`score=0.4`。
- volume フロア除外: vol_ma20=3 万株 → `None`。
- adj_close null: 窓内 null → `None`（§4.2）。

**batch/repo** — `tests/test_fetch_meta.py` / `test_batch_calendar.py` / `test_batch_fetch_quotes.py` / `test_batch_lock.py`:
- `upsert_fetch_meta`/`get_fetch_meta` の**冪等・前進**（一時 SQLite）。
- `candidate_days` が**土日を除外**し範囲を正しく yield。
- `JQuantsAdapter` をスタブ（空配列日・データ日を混ぜる）し、**空日スキップ・fetch_meta 前進・UPSERT 行数**を検証。実 API は叩かない（既存 `test_jquants` の HTTP モック流儀）。
- 同一ロックの二重 `acquire` で `BatchAlreadyRunning`。
- `upsert_signals` の**冪等 UPSERT**（同 `(date,code,signal_type)` 再投入で重複しない）。

**migration / API** — 既存 `tests/test_migrations.py` / `test_api.py` に追加:
- `0002` 適用後 `fetch_meta`・`0003` 適用後 `signals` が存在。
- `GET /signals`（date 解決・score 降順・JOIN company_name）。`POST /batch/run`（受付 **202**・ロック競合 **409**）。

---

## 9. 着工順（チェックリスト）

ARM ゲートを最初に潰し、純関数＋テストを先に固める（計算の真実を最初に確定＝ADR-016）。

- [ ] **0. ARM ビルドゲート**（最優先）: numpy/pandas/apscheduler を入れた backend イメージを別 PC でクロスビルドし aarch64 起動確認（data-arch・§7）。
- [ ] **1. データ層**: `schema.py` に `fetch_meta`・`signals` 追記 → autogenerate `0002_fetch_meta`・`0003_signals`（発行 data-arch）→ `test_migrations` に存在確認。
- [ ] **2. repo**: `upsert_fetch_meta`/`get_fetch_meta`/`get_max_quote_date`/`list_stock_codes`／`upsert_signals`/`get_signals`/`get_latest_signal_date` ＋ `test_fetch_meta`。
- [ ] **3. quant 純関数（テスト駆動）**: `quant/momentum.py`・`volume_spike.py` ＋ `test_quant_*`（§8 を先に書く）。
- [ ] **4. calendar**: `candidate_days`（純ロジック）＋テスト。
- [ ] **5. lock**: `batch/lock.py`＋テスト。`engine.py` に `busy_timeout` 追加。
- [ ] **6. fetch_quotes ジョブ**: 営業日ループ・fetch_meta 前進 ＋ スタブテスト。
- [ ] **7. sync_master**: **[OPEN] 全件取得の実機確認を先に解消**（§3.6）。
- [ ] **8. calc_signals ジョブ**: 全銘柄ループで quant 関数 → `upsert_signals`。
- [ ] **9. runner.run_nightly**: 糊付け＋`notify.error`＋`NIGHTLY_JOBS`。
- [ ] **10. `POST /batch/run`**: 非同期 202・ロック 409 ＋ api.md 追記（app と調整）。
- [ ] **11. cron**: APScheduler を lifespan に（dev はフラグ off・02:00 JST 既定）。
- [ ] **12. frontend**: `lib/api.ts`（型＋`getSignals`）→ `app/signals/page.tsx` → nav href 化。
- [ ] **（任意・最後）チャート overlay 基盤**: `GET /indicators/{code}`（§5.2）＋ `CandleChart` に SMA/RSI/シグナル印を**表示/非表示トグル**付きで重ねる（§6.3）。無理なら後回し可。
- [ ] **13. 実機検証**: 初回 `full_backfill=True` を 1 回流し所要時間・行数を実測（jquants.md §4 の見積検証）。

---

## 10. このフェーズの [OPEN]（既定値で着工可・`_open-questions.md` 参照）

Phase 1 に効くユーザー裁定 3 件。**推奨値を開始既定として採用済み**。U-1/U-2 は grill 済みで**設計方針も確定**（連続スコア・閾値は破壊的ゲートにしない＝**ADR-026**／パラメータは Phase 1 はコード定数・env 不可・将来 `method_settings`＝**ADR-027**）。値そのものは後でツマミ調整する前提なので開始値でよい。詳細は `docs/phase-specs/_open-questions.md`。

| # | 論点 | 開始既定（採用） | 差替の道筋 |
|---|---|---|---|
| **U-1** | momentum の重み（連続スコア） | `W_TREND=0.6 / W_RSI=0.4` ＋ GC/反転は加点 | コード定数 → 将来 `method_settings`（ADR-027） |
| **U-2** | volume_spike の notable 閾値・保存フロア | notable `ratio≥3.0`・保存 `ratio≥1.5`・`score=min(ratio/10,1)` | 同上 |
| **U-9** | 夜間 cron の起動時刻 | **02:00 JST**（TZ=Asia/Tokyo） | cron 設定（env） |

**裁定済み（ユーザー判断不要・参考）**: adj_close 欠損=skip（L-26）／`get_indicators`=P1 オンザフライ（L-13）／cron=APScheduler 同居 C（L-1）／`/batch/run`=非同期 202+409（L-2）／営業日=曜日除外＋空レス吸収（L-3）／`is_etf` 是正=Phase7 温存（L-4）／master 全件=実機確認・不可なら daily の code 補完（L-5）／`JQUANTS_MIN_INTERVAL_SECONDS`（Free 13/Light 1・L-6）。

**実機確認（ユーザー裁定ではない技術リスク・着工前ゲート）**:
1. numpy/pandas の **aarch64 ビルド可否**（§7・ADR-021）。
2. `/v2/equities/master` の **全件取得可否**（§3.6・jquants.md 要再確認）。
3. （参考）J-Quants 取引日カレンダー API の有無 — 無くても方式 X（空レス吸収）で着工可。
