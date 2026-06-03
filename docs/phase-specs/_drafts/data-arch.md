# data-arch レーン仕様（データ取得・バッチ・cron・スキーマ移行・インフラ・通知）

> 担当: data-arch（タスク #2）。**着工可能な仕様**であってコードではない。
> 設計の真実は `docs/`。本書は Phase 0 実装（`stocks`/`daily_quotes`/`JQuantsAdapter`/`backfill`/Alembic）に**差分で積む**ための実装図面。
> 参照は `path:行` 形式。確定値は理由つき、ユーザー裁定が要る点は `**[OPEN]**`、docs のズレは `**[DOCS要修正]**` で明記する。
> 作成: 2026-06-03（現状コードは読み取りのみ・変更なし）。

---

## 0. 全体方針（全Phase共通の不変条件）

該当 ADR: ADR-002（SQLite WAL・書き手1プロセス）/ ADR-005（DB は FastAPI のみ）/ ADR-010（アダプタ）/ ADR-018（無人障害）/ ADR-021（Compose）/ ADR-023（uv/Ruff/pyright）。

- **DB の書き手は「夜間バッチ 1 プロセス」に限定**（ADR-002）。画面操作（`transactions`/`cash` 等の手入力）も FastAPI 経由の UPSERT だが、これは**昼間・人間主導の単発**で夜間バッチと時間帯が衝突しないため許容する（WAL で読みとは衝突しない。書き×書きの同時実行を避ける運用規律で担保）。
- **すべての書き込みは UPSERT で冪等**（`on_conflict_do_update`・既存 `repo._upsert` を踏襲）。再取得・再実行で壊れない（ADR-002・ADR-018）。
- **外部 API の取得はアダプタ越し**（ADR-010）。「外部キー名→内部列名」の対応はアダプタ内に閉じ込める（既存 `jquants.py` の `_normalize_*` を踏襲）。
- **スキーマ変更は必ず Alembic の別リビジョン**で刻む（`schema.py` の `metadata` が単一の真実・`env.py` が autogenerate 基準）。`init_db()` が起動時に `upgrade head`。
- **レイヤ**: `batch/`（オーケストレーション・進捗ログ・通知）→ `adapters/`（取得・正規化・リトライ）→ `db/repo.py`（UPSERT・クエリ）。`batch/` はビジネスロジックの薄い糊で、計算は quant レーンの `analysis/` 等に委譲する（本レーンは「取得・保存・起動・通知」のみ担当）。

### 0.1 Alembic 通し番号表（`_arbitration.md` 決定1 の正本）

**単線チェーン**。**data-arch が全移行の発行（ファイル作成）を一元管理**するが、テーブルの**定義内容は所属レーンが正本**を持つ（下表「定義レーン」）。各リビジョンは Phase 着手時に書く（順序の予約）。`down_revision` は 1 本に通す。

| revision | down_revision | Phase | テーブル | 定義レーン |
|---|---|---|---|---|
| `0001_baseline` | — | 0（既存） | stocks, daily_quotes | （既存） |
| `0002_fetch_meta` | 0001 | 1 | fetch_meta（+updated_at） | **data-arch** |
| `0003_signals` | 0002 | 1 | signals（+UNIQUE(date,code,signal_type)） | quant |
| `0004_portfolio_and_assets` | 0003 | 2 | portfolios, holdings, transactions, cash, external_assets, index_quotes, asset_snapshots | **data-arch** |
| `0005_financials` | 0004 | 2 | financials | **data-arch**（B-7） |
| `0006_advisor_state` | 0005 | 3 | policy, advisor_journal, proposals（+depends_on） | ai-advisor |
| `0007_dossier` | 0006 | 4 | watchlist, stock_dossiers, dossier_sources | ai-advisor（B-13） |
| `0008_notifications` | 0007 | 6 | notifications（送信冪等ログ） | **data-arch** |

- **発行（ファイル作成）は data-arch が一元管理**＝番号の再衝突を防ぐ。定義レーンが quant/ai-advisor の行（`0003`/`0006`/`0007`）も、ファイル作成は data-arch が代行し、DDL 本体は所属レーンの正本に従う。
- **watchlist は `0007`(Phase 4・ai-advisor) に一本化**（B-13）。data-arch の Phase 2 からは watchlist を**外す**（§2 参照）。
- 本書内の各 Phase 節のリビジョン番号は**すべてこの表に一致**させてある。

---

## Phase 1: 全銘柄バックフィル＋差分取得＋夜間バッチ＋cron

該当: roadmap.md Phase 1 / jquants.md §4 / ADR-002・ADR-018 / data-model.md §6（`fetch_meta`）。

### 1.1 新規/変更ファイル一覧

| 種別 | パス | 内容 |
|---|---|---|
| 新規 | `backend/app/db/schema.py`（変更） | `fetch_meta` テーブルを追記 |
| 新規 | `backend/alembic/versions/0002_fetch_meta.py` | `fetch_meta` の autogenerate 移行 |
| 変更 | `backend/app/db/repo.py` | `get_fetch_meta` / `upsert_fetch_meta` / `get_max_quote_date` / `list_stock_codes` を追加 |
| 新規 | `backend/app/batch/__init__.py`（充填） | バッチの公開エントリ（`run_nightly`）を re-export |
| 新規 | `backend/app/batch/runner.py` | 夜間バッチのオーケストレーション（ジョブ列・ロック・通知・ログ） |
| 新規 | `backend/app/batch/jobs/__init__.py` | ジョブ登録（順序つきリスト） |
| 新規 | `backend/app/batch/jobs/fetch_quotes.py` | 営業日ループの日足取得ジョブ（初回バックフィル＋差分を同一経路で） |
| 新規 | `backend/app/batch/jobs/sync_master.py` | 全銘柄マスタ同期ジョブ（`stocks` の全量更新） |
| 新規 | `backend/app/batch/lock.py` | 書き手 1 プロセスを担保するファイルロック |
| 新規 | `backend/app/batch/calendar.py` | 営業日判定（取引日カレンダー or quotes 由来） |
| 変更 | `backend/app/adapters/jquants.py` | `fetch_master_all`（全件取得・要実機確認 §1.11）。`is_etf` は Phase 7 温存（§1.10・本 Phase は変更なし） |
| 新規 | `backend/app/routers/batch.py` | `POST /batch/run`（手動起動・cron と共用） |
| 変更 | `backend/app/main.py` | `batch_router` を `include_router` |
| 変更 | `backend/app/scripts/backfill.py` | 既存 3 銘柄ループは互換のため残しつつ、`run_nightly` を呼ぶ薄い CLI に寄せる（後述 1.6） |
| 新規 | `backend/app/batch/notify.py` | Discord 通知（Phase 1 ではエラー通知のみ。Phase 6 で本格化） |
| 変更 | `backend/pyproject.toml` | 依存追加（後述 1.10）。cron 方式により最小（APScheduler 推奨案のみ） |
| 新規 | `compose.yaml`（変更・別サービス追記） | `batch` サービス（cron 方式により内容が変わる・後述 1.5） |

### 1.2 DDL: `fetch_meta`（差分取得管理）

data-model.md §6 準拠。`source` を PK にして「データ種別ごとの取得済み最終営業日」を持つ。

```sql
CREATE TABLE fetch_meta (
    source             TEXT NOT NULL,   -- データ種別キー（'daily_quotes' / 'stocks' / 'index_quotes' / 'financials' 等）
    last_fetched_date  TEXT,            -- 取得済みの最終営業日 'YYYY-MM-DD'（未取得なら NULL）
    updated_at         TEXT,            -- この行の更新時刻（ISO8601 UTC）
    PRIMARY KEY (source)
);
```

`schema.py` 追記イメージ（既存の `Table` 定義流儀に合わせる）:

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

> **[DOCS要修正]** data-model.md §6 の `fetch_meta` は 2 列（`source`/`last_fetched_date`）だが、`updated_at` を足す（運用時に「いつ最後にバッチが回ったか」を /health やログで見たい・冪等更新の証跡）。data-model.md にこの列を追記する。

### 1.3 repo に追加する関数（シグネチャ）

`repo.py` の既存流儀（戻り値は素の `dict`・読みは `Connection` 受け／書きは `engine.begin()`）を踏襲。

```python
def upsert_fetch_meta(source: str, last_fetched_date: str) -> None: ...
    # index_elements=["source"]。updated_at は関数内で UTC now を入れる。

def get_fetch_meta(conn: Connection, source: str) -> dict[str, Any] | None: ...
    # 1 行 or None。last_fetched_date が None/未存在なら「初回」。

def get_max_quote_date(conn: Connection) -> str | None: ...
    # SELECT MAX(date) FROM daily_quotes。fetch_meta 不在時のフォールバック自己修復に使う。

def list_stock_codes(conn: Connection) -> list[str]: ...
    # stocks の全 code（sync_master の差分や進捗ログ用）。
```

### 1.4 バッチ本体のシグネチャ・ジョブ構造

**設計**: `runner.run_nightly()` が「ロック取得 → ジョブを順に実行 → 例外は集約して Discord 通知 → ロック解放」。各ジョブは独立関数で**冪等**・**部分失敗から再開可能**（`fetch_meta`）。

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
    # full_backfill=True: fetch_meta を無視して 2 年分（Free 格納期間）を頭から取り直す（初回 or 復旧）。
    # False（既定）: fetch_meta の last_fetched_date+1 から today まで差分。
    # ロックは with batch.lock.acquire(): で囲む。各 JobResult を集約し、ok=False が 1 件でもあれば notify.error()。

# backend/app/batch/jobs/fetch_quotes.py
def run(*, full_backfill: bool) -> JobResult: ...
    # 1) start_date を決める: full_backfill なら today-2年（Free 格納期間に丸める）、
    #    そうでなければ fetch_meta['daily_quotes'].last_fetched_date の翌営業日。
    # 2) calendar.business_days(start, today) を営業日ループ。
    # 3) 各営業日 d で adapter.fetch_daily_quotes_by_date(d) → 空でなければ
    #    upsert_daily_quotes(rows) → upsert_fetch_meta('daily_quotes', d)（1 日進むごとに前進・再開可能）。
    # 4) 例外（JQuantsError 等）は握って JobResult(ok=False) で返す（runner が通知）。

# backend/app/batch/jobs/sync_master.py
def run() -> JobResult: ...
    # stocks の全量同期。詳細は 1.7（全銘柄マスタの取得方法）と [OPEN] を参照。
```

`batch/jobs/__init__.py` でジョブ順序を 1 か所に定義（マスタ → 日足 → （Phase 1 以降で signals 計算ジョブを quant レーンが追記））:

```python
# 実行順序の単一の真実。後続 Phase はここに append する（calc_signals/run_advisor 等）。
NIGHTLY_JOBS = [sync_master.run, fetch_quotes.run]
```

### 1.5 cron 方式の選定（**[OPEN]**・推奨 1 案）

**3 案を比較し、推奨は「APScheduler を backend プロセス内に常駐」**。

| 方式 | 長所 | 短所 | ラズパイ/Compose 適合 |
|---|---|---|---|
| **A. ホスト OS の crontab** | 枯れている・プロセス分離で軽い | コンテナ前提（ADR-021）と相性が悪い。`docker compose exec backend python -m app.batch` をホスト crontab に書くことになり、cron の env/PATH 問題・compose 名の解決でハマりやすい。dev/prod parity が崩れる | △ |
| **B. Docker 内 cron（専用 `batch` サービス＋`cron`/`supercronic`）** | コンテナ内で完結・dev/prod parity を保てる | slim イメージに cron を足す手間。`supercronic`（コンテナ向け cron）の導入が必要。常駐プロセスが 1 つ増える（メモリ） | ○ |
| **C. APScheduler を backend(FastAPI) プロセス内に常駐**（推奨） | 依存 1 つ（`apscheduler`）。追加コンテナ不要＝メモリ最小。スケジュールが Python コードで管理でき型・ログが一貫。`POST /batch/run` と同じ関数を同じプロセスから呼べる | FastAPI と同居するため**バッチのピークメモリが API 常駐と重なる**（ADR-021 §7.5 の懸念）。reload 時に二重起動しないようガードが要る | ◎（メモリ最小・parity 良） |

**推奨理由**: ADR-021 が「DB コンテナを作らない・サービスは backend/frontend の 2 つ」と最小構成を志向しており、cron のためだけに 3 つ目のコンテナや常駐プロセスを増やすのは思想に反する。APScheduler なら**追加コンテナ 0・依存 1 つ**で、`run_nightly()` を「毎晩 cron」「`POST /batch/run` 手動」の両方から**同一プロセス・同一関数**で呼べる（ADR-011 の「1 つの脳・2 つの起動口」とも整合）。メモリ懸念は「バッチ実行中は APScheduler が直列実行（`max_instances=1`）し、API リクエストと重い計算が同時に走らない夜間帯に限定」で緩和する。

**[OPEN] ユーザー裁定が必要**: 「バッチをどのプロセスに同居させるか」はメモリ設計（ADR-021 §7.5）に直結する。**推奨は C（APScheduler 同居）**だが、夜間バッチが pandas/最適化/LLM でメモリを食う Phase 2〜3 で API 常駐と競合するなら **B（専用 batch サービス）に切り替え**られるよう、`run_nightly()` をプロセス非依存（どのプロセスから呼んでも同じ）に保つ設計にしておく。＝**今 C で始め、重くなったら B へ移れる**ように作る。

APScheduler 採用時の実装メモ:
- `backend/app/main.py` の `lifespan` で `AsyncIOScheduler`（または `BackgroundScheduler`）を起動し、`add_job(run_nightly, CronTrigger(hour=..., minute=...))`。
- `--reload`（dev）で二重起動しないよう、`if not settings.batch_scheduler_enabled: return` のフラグ（`.env` の `BATCH_SCHEDULER_ENABLED`、dev 既定 false / prod true）でガード。**[OPEN]** 起動時刻（後述 P6 のスケジュール群で確定）。
- バッチ本体は同期 I/O（SQLite/httpx 同期）なので、`AsyncIOScheduler` から呼ぶ場合は `run_in_executor` でスレッドに逃がす（イベントループを塞がない）。

### 1.6 `POST /batch/run`（手動起動・cron と共用）— **契約の正本（B-11・裁定メモ決定6）**

**この契約の正本は data-arch（バッチ実体側）が持ち、app の §P6-1 はこれに合わせる**。api.md にもこの形で追記（DOC・app と調整）。

- **body**: `{ full_backfill?: bool }`（既定 false。true で 2 年分を頭から取り直す＝初回/復旧）。`tasks[]` のようなジョブ選択は YAGNI（Phase 1 は全ジョブ実行のみ）。
- **成功**: **`202 Accepted` `{ started: bool, job_id?: string }`**（非同期受付。`job_id` は将来の進捗照会用・任意）。
- **ロック競合**（既にバッチ実行中＝flock が取れない）: **`409 Conflict`**。

```python
# backend/app/routers/batch.py
class BatchRunRequest(BaseModel):
    full_backfill: bool = False         # true: 2 年分を頭から取り直す（初回/復旧）

class BatchRunResponse(BaseModel):
    started: bool
    job_id: str | None = None           # 将来の進捗照会用（任意）

@router.post("/batch/run", response_model=BatchRunResponse, status_code=202)
def run_batch(req: BatchRunRequest) -> BatchRunResponse: ...
    # 非同期受付: BackgroundTasks か APScheduler の add_job(run_nightly, next_run_time=now) で起動し即 202。
    # 起動前に lock.acquire() を試し、取れなければ HTTPException(409)。進捗は fetch_meta.last_fetched_date / Discord で追う。
```

**理由（裁定 L-2）**: 初回バックフィルは Free で**約 100〜150 分**（jquants.md §4・後述 1.9）かかり HTTP を長時間ブロックできないため**非同期 202＋409**で確定。差分日次（数日分）は短いが、経路は 1 つに統一する。

`backfill.py` は後方互換のため CLI として残すが、本体は `run_nightly(full_backfill=True)` を呼ぶ薄いラッパに寄せる（既存の「3 銘柄ループ」は `fetch_daily_quotes` を残置・検証用に温存）。

### 1.7 営業日判定（`calendar.py`）

**2 案・推奨 1 案**:

- **案 X（推奨）: quotes の戻りで判定（カレンダー API 不要）**。`fetch_daily_quotes_by_date(d)` が**空配列を返す日＝非営業日**とみなしてスキップ・`fetch_meta` も前進させる。営業日テーブルを持たずに済み、土日祝・臨時休場をデータ自身に語らせる（最も堅牢で実装が薄い）。`calendar.business_days(start, end)` は「start〜end の全カレンダー日を yield し、各日 fetch して空なら捨てる」だけ。
- **案 Y: J-Quants 取引日カレンダー API を引いて営業日だけ回す**。リクエスト数を最小化できる（非営業日に無駄打ちしない）が、**[OPEN]** V2 にカレンダーエンドポイントが存在するか未確認（jquants.md に記載なし・要実機確認）。存在すれば `fetch_trading_calendar(from_, to)` をアダプタに足す。

**推奨は案 X**。理由: 2 年で全カレンダー日は約 730 日、うち営業日約 490。案 X は土日も「空が返るリクエスト」を打つため約 730 req（Free で約 150 分）と案 Y（約 490 req・約 100 分）より遅いが、**カレンダー API の有無に依存せず確実に動く**。**[OPEN]** 速度を詰めたいなら、土日（曜日で機械判定できる）だけはローカルでスキップし祝日のみ「空が返る」で吸収する**ハイブリッド**（約 520 req）を推奨折衷案とする。＝**曜日で土日を除外＋祝日は空レスポンスで吸収**。

```python
# backend/app/batch/calendar.py
def candidate_days(start: str, end: str) -> Iterator[str]: ...
    # start〜end を 'YYYY-MM-DD' で yield。土日は除外（曜日判定）。祝日はバッチ側で空レスポンスを見てスキップ。
```

### 1.8 ロック方式・書き手の系統（B-9・`_arbitration.md` 決定5）

**ADR-002 の解釈（裁定メモ決定5・正本）**: DB に触れる **OS プロセスは FastAPI 1 つだけ**（ADR-005）。夜間バッチは **APScheduler で FastAPI プロセス内に同居**（方式 C）するので、**バッチ書き込みと昼の API 書き込みは同一プロセス内で直列化**され、クロスプロセスの書×書競合は**原理的に起きない**。つまり「書き手」は 3 系統の論理（(a)夜バッチ／(b)昼の手入力 transactions/cash／(c)チャット承認 policy/proposals）だが、**実体は単一プロセス**である。

このため `flock` は SQLite 競合の防御ではなく、**別 OS プロセスで起動されうる手動バッチ**（`python -m app.scripts.backfill` を端末から叩く・将来の保守スクリプト）が、同居スケジューラの夜間ジョブと**同時に走るのを防ぐクロスプロセス相互排他**として置く。

- **`fcntl.flock`（排他・非ブロッキング）**: `data/batch.lock` を `LOCK_EX | LOCK_NB` で掴み、取れなければ `BatchAlreadyRunning`（`POST /batch/run` は 409、cron はログのみでスキップ）。標準ライブラリのみ・追加依存なし。Linux（ラズパイ・Compose）で確実。
- **プロセス内の二重防御**: APScheduler の `max_instances=1`（プロセス内で夜間ジョブを直列化）＋ flock（クロスプロセス）。
- **昼の API 書き込みとバッチの同時刻衝突**: 上記のとおりプロセス同居で直列化されるため通常は起きないが、**稀な競合に備えて SQLite `busy_timeout`（例 5000ms）を設定**し、ロック待ちをリトライで吸収する。`engine.py` の `_set_sqlite_pragma`（接続時 PRAGMA）に `PRAGMA busy_timeout=5000` を 1 行追加する（既存の WAL/foreign_keys 設定と同じ場所）。これが **B-9 で必要なコード上の追加**（flock は既出・追加なし）。
- **運用規律**: 夜間バッチ実行帯（lock 保持中）はユーザーが手入力しない。単一ユーザーゆえ自然に守れる（ADR-001）。

```python
# backend/app/batch/lock.py
class BatchAlreadyRunning(RuntimeError): ...

@contextmanager
def acquire(lock_path: str | None = None) -> Iterator[None]: ...
    # fcntl.flock(fd, LOCK_EX | LOCK_NB)。取れなければ BatchAlreadyRunning。
    # 役割: 別 OS プロセスの手動バッチ × 同居スケジューラの相互排他（クロスプロセス）。
    # プロセス内の直列化は APScheduler max_instances=1 が担う（二重防御）。
```

> SQLite の WAL は「書き×読み」を捌くが「書き×書き」の同時は `database is locked` になり得る。本構成では (1) DB を触る OS プロセスを FastAPI 1 つに固定（ADR-005）、(2) その中で夜間ジョブを `max_instances=1` 直列化、(3) 別プロセスの手動バッチを flock で排他、(4) 稀な競合を busy_timeout で吸収——の 4 段で ADR-002 を担保する。**[DOCS要修正]** ADR-002 or data-model.md に「書き手の 3 系統（夜バッチ／昼手入力／チャット承認）と衝突回避の実際（同一プロセス直列化＋flock＋busy_timeout）」を 1 段落補注（DOC-9）。

### 1.9 確定パラメータ（値＋理由）

| パラメータ | 値 | 理由 |
|---|---|---|
| 初回バックフィル期間 | today − 約 2 年 | Free 格納期間（jquants.md §1）。Light 移行後は env で延ばせるよう `BACKFILL_YEARS` を持つ |
| リクエスト間隔 | 既存 `_MIN_INTERVAL_SECONDS=13.0` を流用 | Free 5 req/分（jquants.md §4・既存実装）。Light は 60 req/分なので env 化を推奨（後述 [OPEN]） |
| 初回所要時間 | 約 100〜150 分 | 営業日ループ約 490〜730 req × 13 秒（jquants.md §4 の見積と整合） |
| `fetch_meta` 前進単位 | 1 営業日ごと | 途中で落ちても翌回は続きから（ADR-018 の部分失敗再開） |
| 429 リトライ | 既存（最大 4 回・指数バックオフ） | `jquants.py` 既存実装を流用 |
| APScheduler `max_instances` | 1 | バッチの直列実行（書き手 1 プロセス・ADR-002） |
| `coalesce` | True | 起動漏れ（ラズパイ停止中）を 1 回にまとめる |

**[OPEN] リクエスト間隔の env 化**: `_MIN_INTERVAL_SECONDS` はハードコード。Light 移行（60 req/分）で初回 100 分が約 8 分に縮む。**推奨**: `JQUANTS_MIN_INTERVAL_SECONDS` を `.env`/`settings` に出し、Free=13.0 / Light=1.0 を切り替えられるようにする。プラン移行時期は **[OPEN]**（ADR-008 は「短期機能の実運用時」とだけ規定）。

### 1.10 `is_etf` 常時 0 ハードコードの是正方針

現状 `jquants.py:171` で `is_etf = 0` 固定（現状マップ §指摘 4）。docs（data-model.md §2・jquants.md §5）は「ETF 判別＝`Mkt` 対応は Phase 7」と整理済み。

- **Phase 1 の方針（推奨）**: **是正しない**（docs どおり Phase 7 に温存）。ただし Phase 1 の全銘柄取得で **ETF/REIT 行が `is_etf=0` で `stocks`/`daily_quotes` に混ざる**点を仕様として明記し、quant レーンの signals 計算で「ETF を含めるか除くか」をフィルタ条件にできるよう、**`market_code` で実質判別できることをコメントで残す**（`Mkt` 値の対応表は Phase 7 で確定）。
- **[OPEN]**: もし Phase 1 のスクリーニングで ETF を除外したい要件があるなら、Phase 7 を待たず「`market_code` の ETF/REIT 区分値だけ判定」の最小対応を Phase 1 に前倒しする選択肢あり。**推奨は docs どおり後回し**（Phase 1 の主目的はモメンタム/出来高で、ETF 混入は score 計算では大きな害にならない。quant レーンと要確認）。

### 1.11 全銘柄マスタの取得方法（`sync_master`・**[OPEN]**）

現状 `fetch_master(codes)` は**コードを 1 件ずつループ**取得（`jquants.py:160`）。全銘柄（約 4000）を 1 件ずつ取ると Free で 4000 req×13 秒 ＝ 14 時間超で**非現実的**。

- **[OPEN] 推奨**: `/v2/equities/master` を **`code` 無しで叩くと全銘柄が返るか**を実機確認する（`bars/daily` の日付一括と同じパターン）。返るなら `fetch_master_all() -> list[dict]` を 1〜数 req で取得できる。**jquants.md に未記載**のため要検証（要再確認リストに追加すべき項目）。
- 代替（確認が取れない場合）: マスタは**初回 1 回＋週次更新**で足りる（新規上場・社名変更は低頻度）。`bars/daily` の `date` 一括取得で得た全 `code` を種に、**マスタが無い code だけ**を都度補完する（`stocks` に無い code を `daily_quotes` 取得時に検出 → 後追いで `fetch_master([code])`）。これなら新規分だけの少 req で済む。

```python
# adapters/jquants.py 追加（実機確認後に確定）
def fetch_master_all(self) -> list[dict[str, Any]]: ...
    # /v2/equities/master を code 無しで全件取得（要実機確認）。返らなければ削除しループ補完案へ。
```

### 1.12 Discord エラー通知（Phase 1 の最小実装）

ADR-018: 夜間バッチ失敗時に `DISCORD_WEBHOOK_URL` へ通知。Phase 1 では**エラー時のみ**（成功サマリ・シグナル通知は Phase 6）。詳細アダプタ設計は P6（§6.1）に記載し、Phase 1 はその最小版を先に置く。

```python
# backend/app/batch/notify.py（P6 で webhook.py に昇格・統合）
def error(title: str, detail: str) -> None: ...
    # DISCORD_WEBHOOK_URL 未設定なら no-op（ログのみ）。httpx POST、失敗しても握りつぶす（通知失敗で本処理を巻き込まない）。
```

### 1.13 追加依存ライブラリ・ARM ビルドゲート（Phase 1・本レーン分）

- `apscheduler>=3.10`（cron 方式 C 採用＝裁定 L-1 で確定）。
- ファイルロック・Discord は標準ライブラリ＋既存 `httpx` で足り、追加なし。
- ※ pandas/TA-Lib/pandas-ta 等の数理依存は **quant レーン（タスク #3）の担当**。本レーンでは追加しない。

**ARM ビルドゲート（B-10・裁定メモ決定6・Phase 1 着工の最初のゲート）**: 責任分界を確定する。

- **依存の追加判断＝quant**（どの数理ライブラリを入れるか・TA-Lib vs pandas-ta の選定）。
- **Docker クロスビルド検証の段取り＝data-arch**（ADR-021「イメージは別 PC でクロスビルド → ラズパイは pull のみ」のインフラ責務）。
- **Phase 1 着工の最初のゲート**: quant が入れる依存（numpy/pandas など）を含めた backend イメージが **aarch64（ラズパイ）で通るか**を、コードを書き始める前に data-arch がクロスビルドで検証する（`_current-state.md` §設計注意 2「数理/ML 依存が backend に皆無・TA-Lib は ARM ビルド難」と整合）。ここが通らないと夜間バッチの数理計算がラズパイで動かないため、**Phase 1 の他作業より先に潰す**。

### 1.14 テスト方針（Phase 1）

- `tests/test_fetch_meta.py`: `upsert_fetch_meta`/`get_fetch_meta` の冪等・前進（一時 SQLite・既存 `conftest` 流儀）。
- `tests/test_batch_calendar.py`: `candidate_days` が土日を除外し範囲を正しく yield する。
- `tests/test_batch_fetch_quotes.py`: `JQuantsAdapter` をスタブ（空配列日・データ日を混ぜる）し、空日スキップ・`fetch_meta` 前進・UPSERT 行数を検証。**実 API は叩かない**（既存 `test_jquants` は HTTP モック流儀）。
- `tests/test_batch_lock.py`: 同一ロックの二重 `acquire` で `BatchAlreadyRunning`。
- `tests/test_migrations.py`（既存）に `0002` 適用後 `fetch_meta` が存在することを追加。
- `tests/test_api.py` に `POST /batch/run`（ロック競合 409・受付 202）を追加。

### 1.15 着工順（Phase 1）

1. `fetch_meta` 追加（schema → autogenerate `0002` → repo 関数）＋テスト。
2. `calendar.candidate_days`＋テスト（純ロジック・依存なし）。
3. `batch/lock.py`＋テスト。
4. `batch/jobs/fetch_quotes.py`（営業日ループ・`fetch_meta` 前進）＋スタブテスト。
5. `sync_master`（**[OPEN] 全件取得の実機確認**を先に解消）。
6. `runner.run_nightly` で糊付け＋`notify.error`。
7. `POST /batch/run`（バックグラウンド・409）＋ api.md 追記（app レーンと調整）。
8. cron 配線（APScheduler を lifespan に・dev はフラグ off）。
9. 初回 `full_backfill=True` を実機で 1 回流して所要時間・行数を実測（jquants.md §4 の見積検証）。

---

## Phase 2: 資産モデルのスキーマ＋IndexAdapter

該当: roadmap.md Phase 2 / data-model.md §2-3,§6 / ADR-001（単一ユーザー・`portfolio_id` は器として持つ）/ ADR-019（保有は transactions から導出）/ ADR-010（IndexAdapter）。

> 本レーンは **DDL と Alembic 移行、IndexAdapter の取得・バッチ配線**を担当。`holdings` 導出ロジック・最適化・API 契約・画面は app/quant レーン（#3/#4）と分担（境界は §2.6）。

### 2.1 新規/変更ファイル一覧

| 種別 | パス | 内容 |
|---|---|---|
| 変更 | `backend/app/db/schema.py` | `portfolios`/`holdings`/`transactions`/`cash`/`external_assets`/`index_quotes`/`asset_snapshots` を追記（**watchlist は除く**＝Phase 4・ai-advisor `0007`）。`financials` は §2.8 で別途 |
| 新規 | `backend/alembic/versions/0004_portfolio_and_assets.py` | 上記の autogenerate 移行（1 リビジョンにまとめる・採番表 §0.1） |
| 新規 | `backend/alembic/versions/0005_financials.py` | `financials` の移行（§2.8） |
| 変更 | `backend/app/db/repo.py` | 各テーブルの UPSERT/クエリ（app レーンが使う読み・本レーンは index_quotes/asset_snapshots/financials の書き） |
| 新規 | `backend/app/adapters/index.py` | `IndexAdapter`（主要指数の軽量取得） |
| 変更 | `backend/app/adapters/jquants.py` | `fetch_financials`（V2 財務取得・§2.8） |
| 新規 | `backend/app/batch/jobs/fetch_index.py` | `index_quotes` 取得ジョブ（NIGHTLY_JOBS に追加） |
| 新規 | `backend/app/batch/jobs/fetch_financials.py` | `financials` 取得ジョブ（§2.8・NIGHTLY_JOBS に追加） |
| 新規 | `backend/app/batch/jobs/snapshot_assets.py` | `asset_snapshots` を日次で焼くジョブ |

### 2.2 DDL（全列・PK・index）

data-model.md §3 準拠。`portfolio_id`/`code` の FK は SQLite で `foreign_keys=ON`（engine.py 既設）。

```sql
CREATE TABLE portfolios (
    portfolio_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    created_at    TEXT                 -- ISO8601
);

CREATE TABLE transactions (              -- 一次データ（ADR-019）。holdings はここから導出
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id  INTEGER NOT NULL REFERENCES portfolios(portfolio_id),
    code          TEXT    NOT NULL REFERENCES stocks(code),
    side          TEXT    NOT NULL,      -- 'buy' / 'sell'
    shares        REAL    NOT NULL,
    price         REAL    NOT NULL,      -- 約定単価
    fee           REAL,                  -- 手数料（任意）
    traded_at     TEXT    NOT NULL       -- 約定日 'YYYY-MM-DD'
);
CREATE INDEX ix_transactions_portfolio ON transactions(portfolio_id);
CREATE INDEX ix_transactions_code      ON transactions(code);

CREATE TABLE holdings (                  -- transactions からの導出値（ADR-019・直接編集しない）
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id  INTEGER NOT NULL REFERENCES portfolios(portfolio_id),
    code          TEXT    NOT NULL REFERENCES stocks(code),
    shares        REAL    NOT NULL,      -- 導出: Σbuy.shares − Σsell.shares
    avg_cost      REAL,                  -- 導出: 移動平均取得単価
    UNIQUE (portfolio_id, code)          -- 1 ポートフォリオ 1 銘柄 1 行（UPSERT キー）
);

-- watchlist はここでは作らない。Phase 4・ai-advisor の `0007_dossier` に一本化（B-13・採番表 §0.1）。
--   理由: watchlist はドシエ（stock_dossiers/dossier_sources）と同時に使われ、画面・API も Phase 4。
--   二重 CREATE で移行が壊れるため、DDL の正本は ai-advisor が持つ。

CREATE TABLE cash (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    balance       REAL NOT NULL,         -- 投資用待機現金（JPY・ADR-010 通貨列は Phase 7 まで持たない）
    updated_at    TEXT
);

CREATE TABLE external_assets (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    name                   TEXT NOT NULL,   -- 「オルカン」等
    category               TEXT,            -- 投信/コモディティ等
    value                  REAL,            -- 評価額（手入力）
    proxy_symbol           TEXT,            -- 概算 proxy（指数等）
    monthly_contribution   REAL,            -- 毎月積立（任意）
    as_of                  TEXT             -- 基準日
);

CREATE TABLE index_quotes (              -- 主要指数の水準（daily_quotes とは別粒度・別出所）
    symbol  TEXT NOT NULL,               -- 'TOPIX' / '^GSPC' 等
    date    TEXT NOT NULL,               -- 'YYYY-MM-DD'
    close   REAL,                        -- 終値（水準）
    PRIMARY KEY (symbol, date)
);
CREATE INDEX ix_index_quotes_symbol ON index_quotes(symbol);

CREATE TABLE asset_snapshots (           -- 日次総資産（夜間バッチが焼く）
    date            TEXT PRIMARY KEY,    -- 'YYYY-MM-DD'（1 日 1 行）
    total_value     REAL,
    stock_value     REAL,
    cash_value      REAL,
    external_value  REAL,
    pnl             REAL
);
```

**[OPEN] FK 制約を SQLAlchemy Core で張るか**: 既存 `daily_quotes` は `stocks` への FK を**張っていない**（schema.py に ForeignKey 未使用）。現状コードの流儀に合わせるなら FK は論理参照に留め物理制約は張らない選択もある。**推奨**: 自分データ（transactions/holdings 等・手入力）は誤入力防止の価値が高いので **`ForeignKey` を張る**（`foreign_keys=ON` は engine.py で既に有効）。生データ間（daily_quotes→stocks）は取得順序の都合で張らない既存方針を維持。＝**自分データは FK あり・生データは FK なし**。adr-guardian と要確認。

### 2.3 `portfolios` の初期行（単一ポートフォリオ運用）

roadmap.md Phase 2「当面は単一 portfolio 固定」。**確定（裁定 L-8）**: `0004_portfolio_and_assets` 移行内で `portfolio_id=1, name='Default'` を**1 行 seed**する（ADR-001 の「器は持つが UI は 1 個前提」を実装で素直に表す）。app レーンは既定ポートフォリオを `GET /portfolios` の先頭で解決する（id 固定にしない・裁定 L-9）。

### 2.4 IndexAdapter（軽量・取得対象）

ADR-010・data-model.md §2。J-Quants 範囲外（日本指数の一部・米国指数）なので別ソース。

- **取得対象（初期）**: TOPIX、日経225、S&P500（`^GSPC`）。マクロ文脈用なので**終値（水準）のみ**で足りる（data-model.md §2 の `index_quotes` は close 1 列）。
- **[OPEN] データソース**: `.env.example` に `US_EQUITY_SOURCE=stooq`（コメントアウト）がある。**推奨**: 無料で日次終値が安定して取れる **Stooq**（`https://stooq.com/q/d/l/?s=...&i=d` の CSV）を `IndexAdapter` の既定実装にする。TOPIX/日経は J-Quants の指数 API があるか **[OPEN]**（jquants.md 未記載・要確認）。無ければ Stooq の該当シンボルで代替。ソース確定は Phase 2 着手時に実機確認。

```python
# backend/app/adapters/index.py
class IndexAdapter:
    def fetch_index_quotes(self, symbol: str, from_: str | None = None, to: str | None = None
                           ) -> list[dict[str, Any]]: ...
        # 戻り: [{"symbol","date","close"}, ...]（内部列名）。ソース固有のキー対応はこのファイルに閉じ込める（ADR-010）。
```

```python
# backend/app/batch/jobs/fetch_index.py
def run() -> JobResult: ...
    # 対象 symbol ごとに fetch_index_quotes（差分は fetch_meta['index_quotes:<symbol>']）→ upsert_index_quotes。
```

### 2.5 `asset_snapshots` ジョブ（日次総資産）

夜間バッチが「保有評価額（最新 `daily_quotes`）＋現金＋外部資産」を集計して 1 日 1 行焼く。**評価額の計算（holdings×最新株価）は app/quant レーンの導出関数を呼ぶ**（本レーンは「焼く・保存する」糊）。Free 12 週間遅延の注記は app レーンの API 表示責務（data-model.md §3 の警告）。

### 2.6 レーン境界（Phase 2）

- **本レーン**: 上記 DDL・`0004_portfolio_and_assets`/`0005_financials` 移行・`index_quotes`/`asset_snapshots`/`financials` の取得＆焼きジョブ・IndexAdapter。
- **app レーン（#4）**: `transactions` 入力 API、`holdings` 導出ロジック（Σ買い−Σ売り・移動平均原価＝ADR-019）、評価額/相関/最適化の API・画面。
- **quant レーン（#3）**: 相関・PyPortfolioOpt・バックテスト計算本体。
- 導出関数の置き場所（`holdings` 再計算が repo か service か）は app レーンと要合意（**[OPEN]**）。

### 2.7 着工順・テスト（Phase 2）

1. DDL を `schema.py` に追記 → `0004_portfolio_and_assets` autogenerate → `test_migrations` に存在確認を追加。
2. `IndexAdapter`（ソース実機確認後）＋ HTTP モックテスト。
3. `fetch_index` ジョブを `NIGHTLY_JOBS` に追加＋スタブテスト。
4. `financials`（§2.8）: `schema.py` 追記 → `0005_financials` → `fetch_financials` アダプタ＋ジョブ＋テスト。
5. `snapshot_assets` ジョブ（app の導出関数が出来てから配線）。

### 2.8 `financials`（財務・決算）の DDL＋取得仕様（B-7・`0005_financials`）

該当: data-model.md §2 / roadmap.md Phase 2・Phase 5 / jquants.md §3。**quant の P5（AI Alpha）と ai-advisor のドシエ（`get_financials`）が前提にする供給仕様**。Phase 2 で器（テーブル＋取得ジョブ）を入れ、量的活用は Phase 5（ML 特徴量）で本格化する。

**DDL**（data-model.md §2 準拠。主キーは `(code, disclosed_date, fiscal_period)`・実フィールドは実機確認）:

```sql
CREATE TABLE financials (
    code             TEXT NOT NULL REFERENCES stocks(code),  -- 銘柄コード
    disclosed_date   TEXT NOT NULL,    -- 開示日 'YYYY-MM-DD'
    fiscal_period    TEXT NOT NULL,    -- 会計期間（例 '2025Q1' / 'FY2024'・実値は実機確認）
    net_sales        REAL,             -- 売上高
    operating_profit REAL,             -- 営業利益
    profit           REAL,             -- 純利益
    eps              REAL,             -- EPS
    bps              REAL,             -- BPS
    PRIMARY KEY (code, disclosed_date, fiscal_period)
);
CREATE INDEX ix_financials_code ON financials(code);
```

`schema.py` 追記イメージ（既存流儀）:

```python
financials = Table(
    "financials",
    metadata,
    Column("code", String, ForeignKey("stocks.code"), nullable=False),
    Column("disclosed_date", String, nullable=False),  # 'YYYY-MM-DD'
    Column("fiscal_period", String, nullable=False),
    Column("net_sales", Float),
    Column("operating_profit", Float),
    Column("profit", Float),
    Column("eps", Float),
    Column("bps", Float),
    PrimaryKeyConstraint("code", "disclosed_date", "fiscal_period", name="pk_financials"),
    Index("ix_financials_code", "code"),
)
```

**取得（J-Quants V2 財務エンドポイント）**: jquants.md §3 は `/v2/fins/summary` を挙げるが、V1→V2 で `/v2/equities/statements` に統合された可能性があり**未確定**。**[DOCS要修正]** jquants.md §6 要再確認リストに「(8) V2 財務エンドポイント（`/v2/fins/summary` か `/v2/equities/statements` か）と実フィールド名」を追加（実機確認後にパスと正規化を確定）。正規化（外部キー→内部列）は `jquants.py` に閉じ込める（既存 `_normalize_*` 流儀）。

```python
# adapters/jquants.py 追加（V2 財務・実機確認後にパス/キー確定）
def fetch_financials(self, code: str | None = None, date: str | None = None
                     ) -> list[dict[str, Any]]: ...
    # code 指定で 1 銘柄、date 指定でその日開示の全銘柄（bars/daily と同じ「日付一括」が効くか実機確認）。
    # 戻り: [{code, disclosed_date, fiscal_period, net_sales, operating_profit, profit, eps, bps}]（内部列名）。

# backend/app/batch/jobs/fetch_financials.py
def run() -> JobResult: ...
    # 差分は fetch_meta['financials'].last_fetched_date。開示は不定期なので「開示日ベースの差分」で日付一括取得を推奨
    # （日付一括が効かなければ watchlist+保有銘柄に絞って銘柄ループ）→ upsert_financials → fetch_meta 前進。
```

- **`fetch_meta` source = `'financials'`**（§1.2 の差分管理を流用）。財務開示は不定期だが「開示日 ≦ last_fetched_date は取得済み」で冪等再開できる。
- **取得対象の絞り込み（[OPEN]）**: Free で全銘柄財務を毎晩取るのは過剰。**推奨**: Phase 2 は**保有＋watchlist 銘柄に限定**して取得し、Phase 5（ML 学習用に全銘柄財務が要る段階）で全銘柄バックフィルへ広げる（ML 学習は別 PC＝ADR-006 なので、全銘柄財務の一括取り込みは別 PC 側のバックフィルでも可）。quant の P5 特徴量設計と取得範囲を要すり合わせ。

---

## Phase 3: 夜の分析AI を載せる cron 基盤＋LLM インフラ＋障害通知

該当: roadmap.md Phase 3 / ADR-011・ADR-012・ADR-014・ADR-018 / advisor.md。

> 本レーンは **「軸1 夜の分析AI を Phase 1 の batch に相乗りさせる配線」「LLM アダプタのインフラ面（.env キー/URL/モデル）」「失敗時の Discord 通知・リトライ・日記スキップ」**を担当。プロンプト構成・Tool 定義・`policy`/`journal` の中身は ai-advisor レーン（#5）。

### 3.1 新規/変更ファイル一覧

| 種別 | パス | 内容 |
|---|---|---|
| 新規 | `backend/app/batch/jobs/run_advisor.py` | 軸1 夜の分析AI ジョブ（NIGHTLY_JOBS の最後に追加） |
| 変更 | `backend/app/config.py` | LLM 設定は既設（`llm_api_key`/`llm_base_url`/`llm_model`）。リトライ秒数・タイムアウトの設定を追記（後述） |
| 変更 | `backend/app/advisor/llm.py` | リトライ/タイムアウトのインフラ的強化（ai-advisor とインターフェース調整） |
| 新規 | `backend/alembic/versions/0006_advisor_state.py` | `policy`/`advisor_journal`/`proposals`（+`depends_on`・裁定メモ決定4）移行。**DDL の正本は ai-advisor、移行ファイル発行は data-arch が代行**（採番表 §0.1） |

### 3.2 cron 相乗り（軸1）

ADR-011「1 つの脳・2 つの起動口」。`run_advisor.run()` を `NIGHTLY_JOBS` の**末尾**に足すだけ（signals 計算ジョブの後 ＝ 当日の事実が揃ってから AI が読む）。

```python
# backend/app/batch/jobs/run_advisor.py
def run() -> JobResult: ...
    # 1) その日の事実（signals/portfolio/asset_snapshots/policy）を quant/app の関数で集約（AI は計算しない＝ADR-014）。
    # 2) ai-advisor の analyze_nightly(facts) を呼ぶ（LLM 呼び出し・リトライ込み）。
    # 3) advisor_journal に 1 件 INSERT（policy_snapshot 同梱）。
    # 4) LLM が最終的に失敗したら：日記をスキップして JobResult(ok=False, detail=...) を返す（signals は残る＝ADR-018）。
    #    runner が Discord にエラー通知。前日までの journal はそのまま。
```

### 3.3 LLM インフラ（ADR-012）

- `.env` キーは既設（`config.py:28-31`・`.env.example`）。OpenRouter 既定 / Ollama 差替。
- **追記する設定（インフラ面）**:
  - `llm_timeout_seconds: float = 60.0`（夜間は長め可）
  - `llm_max_retries: int = 3`（ADR-018 のリトライ）
  - `llm_retry_base_seconds: float = 2.0`（指数バックオフ）
- `advisor/llm.py` の `complete()` にタイムアウト＋リトライを実装（`AsyncOpenAI` の `max_retries`/`timeout` 引数を使う・現状は素の呼び出し）。**[OPEN]** ai-advisor レーンが `complete()` のシグネチャ（Tool/stream 対応）を変える予定なので、リトライ実装はインターフェース確定後に合わせる（インフラ要件＝「3 回リトライ・60 秒タイムアウト・失敗で例外」だけ先に固定）。

### 3.4 障害通知・日記スキップ（ADR-018）

- LLM 失敗時：`run_advisor` が `JobResult(ok=False)` → `runner` が `notify.error("夜の分析AI 失敗", detail)`。**日記は書かない**（signals は前日分が残る）。
- データ取得失敗（Phase 1 の `fetch_quotes` 失敗）時も同様に通知され、その日は AI を回さない（事実が古いまま提案させない）。**[OPEN]** 「取得が部分失敗でも AI を回すか」はユーザー裁定。**推奨**: 当日の `fetch_quotes` が ok なら回す、失敗なら回さず通知のみ（古い材料での提案を避ける・ADR-018 の精神）。

### 3.5 テスト・着工順（Phase 3）

- `run_advisor` は ai-advisor のスタブ（成功/例外）で JobResult・通知呼び出しを検証。LLM 実呼び出しはモック。
- 着工順: ①LLM リトライ/タイムアウト設定（インフラ）→ ②`0006_advisor_state` 移行発行（DDL は ai-advisor 確定後・`depends_on` 含む）→ ③`run_advisor` ジョブ配線 → ④失敗時スキップ＋通知のテスト。

---

## Phase 5: ML 推論の `.pkl` 配置・読込・バージョニング

該当: roadmap.md Phase 5 / ADR-006（学習は別 PC・ラズパイは推論のみ）/ ADR-021（イメージは別 PC でクロスビルド）。

> 本レーンは **`.pkl` の置き場・読込・バージョニング・配布**のインフラ面。特徴量設計・学習・推論ロジックは quant レーン（#3）。

### 5.1 新規/変更ファイル一覧

| 種別 | パス | 内容 |
|---|---|---|
| 新規 | `backend/app/ml/__init__.py` | 推論モジュールの土台 |
| 新規 | `backend/app/ml/model_store.py` | `.pkl` の探索・読込・メタ検証 |
| 新規 | `backend/models/`（ディレクトリ） | `.pkl` 置き場（**git 管理外**・`.gitignore` 追記） |
| 新規 | `backend/app/batch/jobs/score_ai_alpha.py` | 推論ジョブ（NIGHTLY_JOBS に追加・quant の推論関数を呼ぶ） |
| 変更 | `compose.yaml` | `./backend/models:/app/models` を bind mount（named volume でも可） |
| 変更 | `.gitignore` | `backend/models/*.pkl` を除外 |

### 5.2 `.pkl` の置き場・配布

- **置き場**: `backend/models/`（`data/` と同じく git 管理外・bind mount）。SQLite と並ぶ「再生成できる/別 PC 産の成果物」枠。
- **配布**: 別 PC で学習 → `.pkl` を**ラズパイの `backend/models/` に scp/rsync でコピー**（ADR-006）。イメージには焼かない（モデル更新のたびに再ビルドしたくない・ADR-021 はクロスビルドだがモデルは別管理）。
- **[OPEN] 配布手段**: scp 手動か、軽い「モデル取得スクリプト」を用意するか。**推奨**: まず手動 rsync（単一ユーザー・低頻度）。自動化は不要になるまで作らない（YAGNI）。

### 5.3 バージョニング・読込・メタ検証

学習時と推論時の特徴量・前処理の不一致は**静かな事故**（ADR-018 の精神）。`.pkl` 単体ではなく**メタ JSON を併置**する。

```
backend/models/
  ai_alpha-2026-06-01.pkl        # モデル本体（別 PC 産）
  ai_alpha-2026-06-01.json       # メタ: {model_id, trained_at, feature_names[], lgbm_version, target, notes}
  ai_alpha-latest.json           # 現用モデルのポインタ {"active": "ai_alpha-2026-06-01"}
```

```python
# backend/app/ml/model_store.py
@dataclass
class ModelMeta:
    model_id: str
    trained_at: str
    feature_names: list[str]
    lib_version: str          # lightgbm のバージョン（推論側と不一致なら警告/拒否）
    target: str
    notes: str

def load_active(kind: str = "ai_alpha") -> tuple[Any, ModelMeta]: ...
    # *-latest.json の active を読み → 対応 .pkl を joblib/pickle で load → メタ検証。
    # feature_names を推論時の入力列と照合（不一致なら ModelLoadError）。lib_version を実 lightgbm と照合（不一致は警告）。

class ModelLoadError(RuntimeError): ...
```

**確定パラメータ**:
- シリアライズ: **`joblib`**（lightgbm/sklearn の標準・大配列に強い）。**[OPEN]** quant レーンが pickle を選ぶなら合わせる。
- バージョニング: **ファイル名に学習日**（`<kind>-<YYYY-MM-DD>.pkl`）＋`*-latest.json` で現用を指す（ロールバックは latest を旧日付に書き換えるだけ）。
- 推論失敗（モデル無し/メタ不一致）: その夜の `ai_alpha` スコアリングを**スキップ**して通知（前日 signals は残る・ADR-018）。

### 5.4 追加依存・テスト・着工順（Phase 5）

- 追加依存: `joblib`（quant レーンの lightgbm に同梱されることが多いが本レーンで明示）。lightgbm 本体は quant レーン。
- テスト: ダミー `.pkl`＋メタで `load_active` の正常/メタ不一致/欠損を検証（実モデル不要）。
- 着工順: ①`model_store`＋メタ規約 → ②`models/` と `.gitignore`/compose mount → ③`score_ai_alpha` ジョブ配線（quant の推論関数が出来てから）。

---

## Phase 6: cron スケジュール群＋Discord Webhook アダプタ＋通知冪等

該当: roadmap.md Phase 6 / ADR-007（Discord）/ ADR-018（無人障害）。

### 6.1 Discord Webhook アダプタ

Phase 1 の `batch/notify.py`（エラー通知最小版）を**正式なアダプタに昇格・統合**する。

| 種別 | パス | 内容 |
|---|---|---|
| 新規/昇格 | `backend/app/adapters/discord.py`（`batch/notify.py` から移設） | Discord Webhook 送信アダプタ（ADR-010 のアダプタ流儀に合わせ adapters/ へ） |
| 新規 | `backend/app/batch/jobs/notify_digest.py` | 当日サマリ・シグナル・AI 提案を Discord にプッシュ（NIGHTLY_JOBS の最後） |
| 変更 | `backend/app/db/schema.py` | `notifications` テーブル（送信済み記録・二重送信防止）を追記 |
| 新規 | `backend/alembic/versions/0008_notifications.py` | 移行（採番表 §0.1） |

```python
# backend/app/adapters/discord.py
class DiscordAdapter:
    def __init__(self, webhook_url: str | None = None) -> None: ...   # 未設定なら send は no-op
    def send(self, content: str, *, embeds: list[dict] | None = None) -> bool: ...
        # httpx POST。2xx で True。失敗しても例外を投げず False（通知失敗で本処理を巻き込まない＝ADR-018）。
```

`run_nightly` のエラー通知も Phase 1 の `notify.error` からこのアダプタ呼び出しに統一する。

### 6.2 通知の冪等（二重送信防止）

ADR-018 の「冪等」を通知にも適用。cron 再実行・`coalesce` 漏れで同じ通知が二重に飛ばないよう、**送信キー単位で記録**する。

```sql
CREATE TABLE notifications (
    notify_key   TEXT NOT NULL,    -- 'digest:2026-06-01' / 'signal:7203:2026-06-01:volume_spike' 等（送信単位の自然キー）
    channel      TEXT NOT NULL,    -- 'discord'
    sent_at      TEXT,             -- 送信時刻（ISO8601）
    PRIMARY KEY (notify_key, channel)
);
```

送信前に `notify_key` の存在を確認し、無ければ送信 → 記録（同一トランザクションで UPSERT）。**確定**: キーは「種別:対象:日付」で一意に決まる自然キー（連番を使わない＝再実行で同じキーになる）。

### 6.3 cron スケジュール群（裁定 L-1・U-9 で既定確定）

APScheduler（方式 C）で **単一の `run_nightly` 内に全ジョブを順序実行**（複数トリガー分割はしない）＝失敗の集約・ロックが 1 か所で済む（裁定 L-1）。

| ジョブ | 既定時刻 | 理由 |
|---|---|---|
| `run_nightly`（取得→signals→AI→通知） | **毎日 02:00 JST**（裁定 U-9 既定） | J-Quants Free は 12 週間遅延で当日場中に依存しない。深夜帯は API/家庭内 LAN が空く。ラズパイ常時起動前提 |
| リバランス・アラート（⑦） | nightly 内で判定 | 前回見直しから一定期間で通知（`proposals`/`policy.updated_at` を見る）。期間日数は quant/app と調整 |
| ブレイクアウト通知（⑧） | nightly 内（signals 算出後） | 高スコア・出来高異常を検知時に digest へ含める。しきい値は quant と調整 |

- **起動時刻 02:00 JST はユーザーの生活時間に関わる U-9（要ユーザー確認）**。`_open-questions.md` に列挙済みの想定で、env（`CronTrigger`）で後から差し替え可能にする。
- タイムゾーン: ラズパイ/コンテナの TZ を **`TZ=Asia/Tokyo`** に固定（compose の `environment` に追記）。APScheduler の `CronTrigger(timezone="Asia/Tokyo")` も合わせる。

### 6.4 追加依存・テスト・着工順（Phase 6）

- 追加依存なし（`httpx` 既存・APScheduler は Phase 1 で導入済み）。
- テスト: `DiscordAdapter.send` を HTTP モックで（2xx→True・5xx→False・未設定→no-op）。`notifications` の二重送信防止（同一 `notify_key` で 2 回目はスキップ）。
- 着工順: ①`0008_notifications` 移行 → ②`adapters/discord.py`（`batch/notify.py` 統合）→ ③`notify_digest` ジョブ＋冪等 → ④スケジュール時刻（U-9 ユーザー確認後に env 既定値を確定）。

---

## 横断: 確定事項サマリ・[OPEN] 一覧・他レーン依存

### 確定（理由つき）

1. **cron 方式 = APScheduler を backend プロセス内常駐（方式 C）**。追加コンテナ 0・依存 1 つ・`run_nightly` を cron/手動の両入口から同一関数で呼べる（ADR-011/021 と整合）。重くなれば専用 batch サービス（B）へ移れるよう `run_nightly` をプロセス非依存に保つ。
2. **書き手 = FastAPI 単一 OS プロセス**（ADR-005）。プロセス内は APScheduler `max_instances=1` で直列化・別 OS プロセスの手動バッチは `fcntl.flock` で排他・稀な競合は SQLite `busy_timeout=5000` で吸収（ADR-002 を 4 段で担保・§1.8・決定5）。
3. **営業日ループ = 曜日で土日除外＋祝日は空レスポンスで吸収**（カレンダー API の有無に依存しない・約 520 req）。
4. **差分の真実 = `fetch_meta`（`source` PK・`updated_at` 追加）。1 営業日ごとに前進**して部分失敗から再開（ADR-018）。
5. **`.pkl` = `backend/models/`（git 管理外）＋メタ JSON＋`*-latest.json` ポインタ。joblib・ファイル名に学習日**。

### lead 裁定で確定済み（`_arbitration.md` 決定7・F-2）— 元 [OPEN] の収束結果

R2 で挙げた [OPEN] のうち lead 裁量分は推奨値で確定したのだ。本書の各節も確定表記に直してある。

- **L-1** cron 方式 = C（APScheduler 同居）で開始・重くなれば B（§1.5）。
- **L-2** `/batch/run` = 非同期 202＋ロック競合 409（§1.6・契約正本）。
- **L-3** 営業日 = 曜日で土日除外＋祝日は空レス吸収（§1.7）。
- **L-4** `is_etf` 是正は Phase 7 温存（§1.10）。
- **L-6** リクエスト間隔 env 化 `JQUANTS_MIN_INTERVAL_SECONDS`（Free13/Light1）（§1.9）。
- **L-7** 自分データ FK = 張る／生データ = 張らない（§2.2）。
- **L-8/L-9** `portfolios (1,'Default')` を `0004` で seed・既定解決は `GET /portfolios` 先頭（§2.3）。
- **L-10** IndexAdapter = Stooq 既定（§2.4）。
- **L-11** 部分取得失敗日は AI を回さず通知のみ（§3.4）。
- **L-12** `.pkl` = joblib・ファイル名に学習日・`*-latest.json` ポインタ（§5.3）。

### 残る確認事項（実機確認・ユーザー裁定）

- **[実機確認-1]** `/v2/equities/master` を code 無しで全件取得できるか（不可なら daily の code から不足補完・L-5）。→ DOC-3。
- **[実機確認-2]** V2 財務エンドポイント（`/v2/fins/summary` か `/v2/equities/statements` か）と実フィールド名（§2.8）。→ DOC-3(8)。
- **[実機確認-3]** V2 取引日カレンダー API の有無（無ければ §1.7 の曜日+空レス方式で吸収・問題なし）。→ DOC-3。
- **[実機確認-4]** V2 主要指数（TOPIX/日経）API の有無（無ければ Stooq で代替）。→ DOC-3。
- **[ユーザー裁定 U-9]** cron 起動時刻（既定 02:00 JST・`TZ=Asia/Tokyo`）。生活時間に関わるため `_open-questions.md` で確認・env で差替可（§6.3）。
- **[ユーザー裁定]** J-Quants Light プラン移行時期（ADR-008 は「実運用時」とだけ規定・L-6 の間隔切替に直結）。

### [DOCS要修正]（DOC 集約・E 節と対応）

- **DOC-1**: data-model.md §6 `fetch_meta` に `updated_at` 列を追記（運用観測・冪等証跡）。
- **DOC-3**: jquants.md §6 要再確認リストに「(5) `/v2/equities/master` の code 無し全件取得可否」「(6) V2 取引日カレンダー API 有無」「(7) V2 主要指数(TOPIX/日経) API 有無」「**(8) V2 財務(statements/summary) エンドポイントと実フィールド名**」を追加。
- **DOC-9**: ADR-002 or data-model.md に「書き手の 3 系統（夜バッチ／昼手入力／チャット承認）と衝突回避の実際（同一プロセス直列化＋flock＋busy_timeout）」を補注（§1.8）。

### 他レーンへの依存・要確認

- **app（#4）**: `POST /batch/run` 契約は **data-arch 正本**（`{full_backfill?}`/202`{started,job_id?}`/409）＝app §P6-1 が合わせる（B-11）。`holdings` 導出関数の置き場所（repo/service）。`asset_snapshots`/評価額の遅延注記表示。
- **quant（#3）**: 数理依存（pandas/TA-Lib/pandas-ta/PyPortfolioOpt/lightgbm）の追加判断は quant・**ARM クロスビルド検証段取りは data-arch**（B-10・Phase 1 最初のゲート・§1.13）。`0003_signals` の DDL 正本は quant（発行は data-arch 代行）。signals/推論ジョブを `NIGHTLY_JOBS` に追加する際のシグネチャ。`financials` 取得範囲（保有+watchlist→全銘柄）の P5 すり合わせ（§2.8）。ETF 除外要否。
- **ai-advisor（#5）**: `0006_advisor_state`（policy/advisor_journal/proposals+`depends_on`）・`0007_dossier`（**watchlist** をここに一本化・B-13）の DDL 正本は ai-advisor、**移行ファイル発行は data-arch 代行**。`analyze_nightly(facts)` と `llm.complete()` の最終シグネチャ（リトライ/タイムアウトはインフラ要件として先に固定・§3.3）。
- **adr-guardian（#6）**: FK 方針（自分データ=張る）・ロック設計（同一プロセス直列化＋flock＋busy_timeout）が ADR-002 を満たすかのレビュー（B-9 は決定5で収束済み）。
