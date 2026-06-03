# Phase 6 着工仕様: Signal Beacon（通知）
> 出所: roadmap.md Phase 6 / ADR-007(Discord)/ADR-018(失敗を放置しない)。レビュー・裁定反映済み。コード未実装＝着工仕様。
> 合成元: `_drafts/_arbitration.md`（正本: 採番 `0008_notifications`(送信冪等ログ)・Discord）/ `_drafts/data-arch.md` §6（cron・DiscordAdapter・冪等・notifications）/ `_drafts/app.md` §P6（通知設定/履歴 UI・.env 固定）/ `_drafts/quant.md` §1.3-1.4（アラート条件＝高スコア/出来高異常）/ `_drafts/ai-advisor.md` §7（夜の分析AI の当日提案）/ `_drafts/_current-state.md`（現状）。作成: 2026-06-03。
> 参照は `path:行` 形式。確定値は理由つき、ユーザー裁定が要る点は `**[OPEN]**`、docs のズレは `**[DOCS要修正]**`。

---

## 0. 目的と完了条件

**目的**（roadmap.md:107）: 画面を開かなくても、重要な変化と AI の提案を Discord で受け取れるようにする。無人運用で「静かな失敗」も「重要な好機」も見逃さない（ADR-018）。

**完了条件**（roadmap.md:114）: 条件合致時・毎朝、Discord に通知（AI の提案要約を含む）が届く。同じ通知が二重に飛ばない（冪等＝ADR-002/018）。

**前提 Phase**:
- **Phase 1（Trend Vane）**: `signals` テーブル（`momentum`/`volume_spike`・`score`・`payload`）と `NIGHTLY_JOBS`・`run_nightly`・`fcntl.flock`・APScheduler 配線が稼働していること（data-arch §1）。アラート条件はここの `signals` を読む。
- **Phase 3（夜の分析AI）**: `run_advisor` ジョブが `advisor_journal`（当日の `proposal`/`observations`）を書いていること（ai-advisor §7）。当日提案プッシュはこれを読む。
- Phase 1 で `batch/notify.py` のエラー通知（最小版）、Phase 3 でその DiscordAdapter 利用が既に入っている（data-arch:275-281・ai-advisor:381）。本 Phase はそれを**正式アダプタへ昇格・統合**し、成功サマリ/シグナル/提案の**本格通知**を足す。

**スコープ外**: 通知チャンネルの複数化・通知テンプレートの DB 管理・LINE 等（ADR-007 で Discord 単独）。Phase 7（lead_lag）のシグナル通知は signals に乗れば本ジョブが自動で拾う（型を汎用に作る）。

---

## 1. 全体像（機能⑦⑧＋夜AI当日提案プッシュ）

夜間バッチ `run_nightly`（毎日 02:00 JST・APScheduler 方式 C）の**末尾**に通知ジョブ `notify_digest` を 1 つ足す。取得→signals 算出→夜の分析AI→**通知**の順で、当日の事実と AI 提案が出そろってから 1 通の digest に束ねて送る（data-arch:641,672）。

| 機能 | 内容 | 条件・データ源 |
|---|---|---|
| **⑦ 定期リバランス・アラート** | 前回見直しから一定期間経過で「方針を見直す時期」を通知 | `policy.updated_at` / 直近 `advisor_journal.date` / `proposals` を見て、最終見直しから `REBALANCE_ALERT_DAYS`（既定 14 日）超で 1 件（roadmap.md:109・data-arch:677） |
| **⑧ 急変動・ブレイクアウト通知** | 高スコア銘柄・出来高異常を検知時にプッシュ | 当日 `signals` の `score >= ALERT_SCORE_MIN`（既定 0.6）または `signal_type='volume_spike'` の `payload.ratio >= 3.0`。`signals JOIN stocks` で社名を補完（roadmap.md:110・quant.md:165,176-192・data-arch:678） |
| **夜AI 当日提案プッシュ** | 夜の分析AI の当日提案を Discord へ | 当日 `advisor_journal` の `proposal`（短文要約）＋ `proposed_policy_change` があれば併記（roadmap.md:111・ai-advisor:364-369） |
| **失敗エラー通知（統合）** | バッチ/AI 失敗時のエラー通知 | `run_nightly` の `JobResult(ok=False)` 集約。Phase 1/3 の `notify.error` を本 Phase の DiscordAdapter 呼び出しに統一（data-arch:653・ADR-018） |

- これら⑦⑧＋提案は **1 通の digest（毎朝通知）に束ねる**。⑧の検知が無い日も、当日サマリ（取得行数・signals 件数・AI 提案有無）は送る（完了条件「毎朝届く」を満たす）。**[OPEN-N]** 「検知ゼロの日も毎朝送るか／好機がある日だけ送るか」はユーザーの好み（通知過多回避）。**推奨**: 毎朝サマリ＋検知時に詳細を厚くする（後述 §3 で `ALWAYS_DAILY_DIGEST` env で切替可能に）。

---

## 2. スキーマ変更（`0008_notifications`: 送信冪等ログ）

該当: `_arbitration.md` 決定1 採番表（0008_notifications・Phase 6・定義レーン=data-arch）/ data-arch:33,642-668 / ADR-002（冪等）/ ADR-018。

**採番**: `revision='0008_notifications'`・`down_revision='0007_dossier'`（単線チェーン・採番表 §0.1）。移行ファイルの発行は data-arch が一元管理。

```sql
CREATE TABLE notifications (
    notify_key   TEXT NOT NULL,    -- 送信単位の自然キー（連番ではない・再実行で同じキーになる＝冪等）
    channel      TEXT NOT NULL,    -- 'discord'（ADR-007・将来の多チャンネル余地）
    sent_at      TEXT,             -- 送信時刻（ISO8601 UTC）
    PRIMARY KEY (notify_key, channel)
);
```

`schema.py` 追記イメージ（既存 `Table` 定義流儀・data-arch:80-91 と同型）:

```python
# 通知の送信済み記録＝二重送信防止（ADR-002/018・cron 再実行・coalesce 漏れで二重に飛ばさない）。
notifications = Table(
    "notifications",
    metadata,
    Column("notify_key", String, primary_key=True),  # '種別:対象:日付' の自然キー
    Column("channel", String, primary_key=True),      # 'discord'
    Column("sent_at", String),                         # ISO8601 UTC
)
```

**二重送信防止キー（`notify_key`）の規約**（自然キー＝再実行で同じ値・data-arch:661,668）:

| 種別 | `notify_key` 形 | 例 |
|---|---|---|
| 当日 digest（毎朝サマリ・⑦提案含む） | `digest:<YYYY-MM-DD>` | `digest:2026-06-01` |
| ⑧ シグナル個別アラート | `signal:<code>:<YYYY-MM-DD>:<signal_type>` | `signal:7203:2026-06-01:volume_spike` |
| ⑦ リバランス・アラート | `rebalance:<YYYY-MM-DD>` | `rebalance:2026-06-01` |
| 失敗エラー通知 | `error:<job_name>:<YYYY-MM-DD>` | `error:fetch_quotes:2026-06-01` |

> **[DOCS要修正]** data-model.md に `notifications` テーブルが未記載。本 DDL（`(notify_key, channel)` 複合 PK・自然キー）を data-model.md に追記する。

---

## 3. 通知ロジック（アラート条件＋冪等で二重送信防止）

新規ジョブ `backend/app/batch/jobs/notify_digest.py`。`NIGHTLY_JOBS` の**最後**に append（取得・signals・run_advisor の後＝事実と提案が揃ってから）。

```python
# backend/app/batch/jobs/notify_digest.py
from __future__ import annotations

def run() -> JobResult: ...
    # （roadmap.md Phase 6・ADR-007/018）当日の事実と AI 提案を 1 通の Discord digest に束ねて送る。
    # 1) 当日(date)の signals を repo.list_signals_for_alert(conn, date) で取得（signals JOIN stocks で company_name 補完）。
    # 2) ⑧ アラート抽出: score >= ALERT_SCORE_MIN(既定0.6) または volume_spike の payload.ratio >= 3.0。
    # 3) ⑦ リバランス判定: 最終見直し(policy.updated_at or 直近 journal.date)から REBALANCE_ALERT_DAYS(既定14)超なら 1 件。
    # 4) 夜AI 当日提案: repo.get_journal_for_date(conn, date) の proposal / proposed_policy_change を要約に含める。
    # 5) digest 本文を組み立て、notify_key='digest:<date>' で送信（§4 のアダプタ）。冪等チェック後に送る。
    # 6) ALWAYS_DAILY_DIGEST=False かつ ⑦⑧・提案すべて無し なら送らない（[OPEN-N]・既定 True=毎朝送る）。
    # 7) 例外は握って JobResult(ok=False) を返す（runner が error 通知）。通知失敗で本処理は巻き込まない。
```

**冪等な送信ヘルパ**（送信前に `notify_key` 存在確認 → 無ければ送信 → 記録を同一トランザクションで UPSERT・data-arch:668）:

```python
# backend/app/batch/notify.py（Phase 1 の error() を残しつつ冪等送信を追加）
def send_once(notify_key: str, content: str, *, embeds: list[dict] | None = None,
              channel: str = "discord") -> bool: ...
    # 1) repo.notification_exists(conn, notify_key, channel) が True なら送らず False（既送・冪等）。
    # 2) DiscordAdapter.send(content, embeds=...) を呼ぶ。
    # 3) 2xx なら repo.record_notification(conn, notify_key, channel, sent_at=utcnow) で記録。
    # 注: 送信成功→記録の間で落ちると稀に再送するが、digest は同日同キーなので翌実行で重複しない（at-least-once 受容）。
```

repo に追加する関数（既存流儀＝読みは `Connection`・書きは `engine.begin()`・戻り値は素の dict／bool）:

```python
def notification_exists(conn: Connection, notify_key: str, channel: str) -> bool: ...
def record_notification(notify_key: str, channel: str, sent_at: str) -> None: ...
    # index_elements=["notify_key","channel"] の UPSERT（再記録は no-op 相当）。
def list_signals_for_alert(conn: Connection, date: str) -> list[dict[str, Any]]: ...
    # signals JOIN stocks。score / signal_type / payload(JSON文字列) / company_name を返す。
def get_journal_for_date(conn: Connection, date: str) -> dict[str, Any] | None: ...
    # advisor_journal の当日 1 行（proposal / proposed_policy_change）。Phase 3 のテーブル。
```

**確定パラメータ（env・後から差替可）**:

| パラメータ | 既定 | 理由・出所 |
|---|---|---|
| `ALERT_SCORE_MIN` | `0.6` | 高スコア銘柄の閾値。signals.score は 0..1（quant.md:170-172）。0.6 は「momentum/volume の上位」目安 |
| volume_spike 比率 | `3.0`（payload.ratio を流用） | quant.md:165 の spike 閾値と一致（独自の値を持たない＝計算は quant が真実・ADR-016） |
| `REBALANCE_ALERT_DAYS` | `14` | 「前回見直しから一定期間」（roadmap.md:109）。隔週見直しの目安。data-arch:677 |
| `ALWAYS_DAILY_DIGEST` | `True` | 完了条件「毎朝届く」を満たす。False で「好機がある日だけ」に切替（[OPEN-N]） |

- **AI に数値を計算させない（ADR-014/016）**: アラート条件の判定は Python（signals.score / payload.ratio の比較）で行う。Discord 本文に「事実（社名・スコア・倍率・提案要約）」を整形して載せるだけで、LLM にその場で判定・計算はさせない。提案文は Phase 3 で生成済みの `advisor_journal.proposal` をそのまま要約引用する。

---

## 4. Discord アダプタ（`DiscordAdapter`）

該当: ADR-007（Discord Webhook）/ ADR-010（アダプタ流儀）/ ADR-018（通知失敗で本処理を壊さない）/ data-arch:636-653。

Phase 1 の `backend/app/batch/notify.py`（エラー通知最小版・data-arch:277-281）を**正式アダプタへ昇格**し `adapters/` へ移設・統合する。

| 種別 | パス | 内容 |
|---|---|---|
| 新規/昇格 | `backend/app/adapters/discord.py` | Discord Webhook 送信アダプタ（`batch/notify.py` の送信実体を移設） |
| 変更 | `backend/app/batch/notify.py` | `error()` と新規 `send_once()`（§3）は残し、送信実体は `DiscordAdapter` を呼ぶ薄い糊に |
| 変更 | `backend/app/db/schema.py` | `notifications` テーブル追記（§2） |
| 新規 | `backend/alembic/versions/0008_notifications.py` | autogenerate 移行（採番表 §0.1） |
| 新規 | `backend/app/batch/jobs/notify_digest.py` | digest ジョブ（§3・`NIGHTLY_JOBS` 末尾） |
| 変更 | `backend/app/batch/jobs/__init__.py` | `NIGHTLY_JOBS` に `notify_digest.run` を append |

```python
# backend/app/adapters/discord.py
from __future__ import annotations

class DiscordAdapter:
    """Discord Webhook 送信（ADR-007）。Webhook URL は .env 固定（settings.discord_webhook_url）。
    未設定なら send は no-op（Phase 0〜5 でも import で壊れない）。"""

    def __init__(self, webhook_url: str | None = None) -> None: ...
        # 既定は settings.discord_webhook_url（config.py:39・既設）。空なら無効化フラグ。

    def send(self, content: str, *, embeds: list[dict[str, Any]] | None = None) -> bool: ...
        # httpx POST {content, embeds}。2xx で True。
        # 失敗（4xx/5xx/接続エラー）でも例外を投げず False（通知失敗で本処理を巻き込まない＝ADR-018）。
        # webhook_url 未設定なら送らず False（ログのみ）。
```

- **Webhook URL は `.env` 固定**（`DISCORD_WEBHOOK_URL`・既に `config.py:39` `discord_webhook_url: str = ""` と `env_status` に `required_from_phase: 6` が用意済み）。**秘密情報は backend のみ・frontend に渡さない**（CLAUDE.md・app.md:568）。UI から URL を編集しない（L-25=通知設定は UI 最小・.env 固定）。
- **送信失敗時の扱い（ADR-018）**: アダプタは握りつぶして `False` を返す。digest ジョブ側は送信失敗を `JobResult.detail` に残すが、本処理（取得・signals・日記）は既に終わっているので巻き込まない。エラー通知自体が失敗しても同様（多重には騒がない）。
- `run_nightly` のエラー通知も Phase 1 の `notify.error` 直書きからこのアダプタ呼び出しに統一する（data-arch:653）。

---

## 5. cron スケジュール（既存 batch へ相乗り）

該当: data-arch:670-687（cron スケジュール群）/ `_arbitration.md` 決定7 L-1・U-9 / ADR-011（1 つの脳・2 つの起動口）/ ADR-021。

- **方式 C（APScheduler を FastAPI プロセス内常駐）を踏襲**（Phase 1 で導入済み・新規依存なし＝`httpx`/`apscheduler` 既存・data-arch:685）。**通知のための別 cron・別トリガーは作らない**。`run_nightly` 内に全ジョブを順序実行し、その末尾に `notify_digest` を足すだけ（失敗集約・ロックが 1 か所で済む・data-arch:672）。

| ジョブ列（`NIGHTLY_JOBS` 順） | 既定時刻 | 理由 |
|---|---|---|
| `run_nightly` = sync_master → fetch_quotes → (fetch_index/financials) → calc_signals → run_advisor → **notify_digest** | **毎日 02:00 JST**（U-9 既定） | Free は 12 週間遅延で当日場中に依存しない。深夜帯は API/家庭内 LAN が空く。ラズパイ常時起動前提（data-arch:676） |
| ⑦ リバランス・アラート | `notify_digest` 内で判定 | `policy.updated_at`/直近 journal を見て期間超なら digest に含める（data-arch:677） |
| ⑧ ブレイクアウト通知 | `notify_digest` 内（signals 算出後） | 高スコア・出来高異常を検知時に digest へ含める（data-arch:678） |

- **タイムゾーン**: コンテナ/ラズパイの TZ を **`TZ=Asia/Tokyo`** に固定（compose の `environment` に追記）。APScheduler の `CronTrigger(timezone="Asia/Tokyo")` も合わせる（data-arch:681）。
- **二重送信の防御の重なり**: APScheduler `coalesce=True`（起動漏れを 1 回にまとめる・data-arch:249）＋ `max_instances=1`（直列）＋ `notify_key` 冪等（§2）。`coalesce` 漏れや `POST /batch/run` 手動再実行で `run_nightly` が同日 2 回走っても、`notify_key='digest:<date>'` が既存なので 2 回目は送らない。
- **起動時刻 02:00 JST は U-9（ユーザーの生活時間に関わる）**。`_open-questions.md` 列挙済みの想定で、env（`CronTrigger`）で後から差し替え可能にする（data-arch:680・722）。

---

## 6. REST API / frontend（通知設定 UI 最小・履歴・.env 固定）

該当: app.md §P6（553-574）/ ADR-005（DB は FastAPI のみ・Next は REST 経由）/ ADR-007 / L-25（通知設定 UI 最小・.env 固定）。

**方針**: 通知は backend（Discord）が送る。**UI は設定確認＋手動バッチ起動が中心で、新規画面は最小**（app.md:555）。Webhook URL は `.env` 固定で UI から編集しない（app.md:568）。

### 6.1 手動バッチ起動（既存契約・data-arch 正本）
- `POST /batch/run`（正本 = data-arch・`_arbitration` 決定6・B-11）。Phase 1 で実装済み。Phase 6 では Settings 画面から起動できるようにするだけ（新規エンドポイントなし）。
  - body `{ full_backfill?: boolean }`（既定 false）→ **202** `{ started: boolean, job_id?: string }`。ロック競合 **409**（app.md:560-565・data-arch:181-194）。

### 6.2 通知設定 UI（最小）
- **`frontend/src/app/settings/page.tsx`**（新規・screens.md #14・app.md:569）: `/health` 詳細表示（既存 Topbar バッジの拡張・`discord_webhook_url` の `set` 状況を含む＝`env_status` config.py:54-57）＋「夜間バッチ手動起動」ボタン（`runBatch`）。
- Sidebar の `Settings` を `href: "/settings"` 化（app.md:570）。
- 通知 ON/OFF・しきい値（`ALERT_SCORE_MIN`/`REBALANCE_ALERT_DAYS`）は**当面 env 固定**。UI 化（`GET/PUT /settings/notifications`）は **OPEN-M=必要になってから**（app.md:568・推奨「Webhook は .env・UI は最小」）。本 Phase は health 表示＋バッチ起動で十分。
- **通知履歴 UI**: `notifications` テーブルを読む最小の履歴表示は任意（**[OPEN-O]**）。**推奨**: 本 Phase は Discord 自体が履歴を持つので **UI 履歴は作らない**（YAGNI）。必要になれば `GET /notifications?limit=` を後付け。

### 6.3 lib/api.ts（frontend・ADR-005）
- 変更: `lib/api.ts` に `runBatch(opts?: { full_backfill?: boolean }): Promise<BatchRunResponse>`（202/409 を `detail` 拾いで扱う・409 は「実行中」表示）・`health` 型拡張（app.md:564,574）。
- 変更: `lib/mock-data.ts`（Settings nav の href 化）・`app/page.tsx`（Dashboard の「バッチを今すぐ実行」を `runBatch` に接続・app.md:574）。
- **DB に触れない**（ADR-005）。通知の送信・記録はすべて backend 側。frontend は health とバッチ起動の REST のみ。

---

## 7. テスト計画（冪等＝同条件で二重送信しない）

該当: data-arch:686（テスト方針）。実 API・実 Webhook は叩かない（HTTP モック・一時 SQLite＝既存 `conftest` 流儀）。

- `tests/test_discord_adapter.py`: `DiscordAdapter.send` を HTTP モックで検証 — 2xx→`True`／4xx・5xx・接続エラー→`False`（例外を投げない・ADR-018）／`webhook_url` 未設定→no-op で `False`。
- `tests/test_notifications_idempotent.py`（**冪等の中核**）: 同一 `notify_key`（例 `digest:2026-06-01`）で `send_once` を 2 回呼ぶ — 1 回目は送信＋記録、**2 回目は `notification_exists` が True で送信しない**（Discord モックの呼び出し回数が 1 回であることを assert）。`record_notification` の UPSERT 再記録が壊れない。
- `tests/test_notify_digest.py`: `signals`/`advisor_journal` をスタブした一時 DB で `notify_digest.run` を実行 — ⑧高スコア(`score>=0.6`)/volume_spike(`ratio>=3.0`)の抽出、⑦ `REBALANCE_ALERT_DAYS` 超の判定、当日提案の要約取り込み、`ALWAYS_DAILY_DIGEST=False` かつ全無しで送信スキップ、例外時 `JobResult(ok=False)`。
- `tests/test_migrations.py`（既存）に `0008` 適用後 `notifications` テーブルが存在し PK が `(notify_key, channel)` であることを追加。
- `run_nightly` のエラー通知統合: `notify.error`/`send_once` が DiscordAdapter を呼ぶことをモックで確認（`JobResult(ok=False)` が 1 件でもあれば error 通知が飛ぶ）。

---

## 8. 着工順（チェックリスト）

data-arch:687 の順序を踏襲し、依存（Phase 1 の `signals`/`run_nightly`、Phase 3 の `advisor_journal`）が揃っていることを前提に進める。

1. [ ] `notifications` を `schema.py` に追記 → autogenerate `0008_notifications`（down_revision=`0007_dossier`）→ `test_migrations` に存在・PK 確認を追加。
2. [ ] `repo.py`: `notification_exists` / `record_notification` / `list_signals_for_alert` / `get_journal_for_date` を追加（既存流儀）＋単体テスト。
3. [ ] `adapters/discord.py`（`DiscordAdapter`）を新規 → `batch/notify.py` の送信実体を移設・`error()` をアダプタ経由に統一 ＋ `test_discord_adapter.py`。
4. [ ] `batch/notify.py` に冪等 `send_once`（§3）＋ `test_notifications_idempotent.py`（**冪等の中核テスト**）。
5. [ ] `batch/jobs/notify_digest.py`（⑦⑧＋当日提案・env パラメータ）＋ `test_notify_digest.py` → `NIGHTLY_JOBS` 末尾に append。
6. [ ] cron 確認: TZ=Asia/Tokyo を compose に追記・`CronTrigger(timezone=...)` 確認（時刻 U-9 裁定済み＝**02:00 JST** を env 既定値に）。
7. [ ] frontend: `app/settings/page.tsx`（health 詳細＋ `runBatch`）・Sidebar の Settings href 化・`lib/api.ts` の `runBatch`／health 型・`app/page.tsx` のバッチ起動接続。
8. [ ] env パラメータ（`ALERT_SCORE_MIN`/`REBALANCE_ALERT_DAYS`/`ALWAYS_DAILY_DIGEST`）を `config.py`・`.env.example` に追記（`discord_webhook_url` は既設・config.py:39）。
9. [ ] 実機で `run_nightly`（または `POST /batch/run`）を 1 回流し、digest が Discord に届くこと・**2 回目で二重送信されないこと**を確認。

---

## 9. このPhaseの[OPEN]

- **[OPEN-N]（ユーザー裁定）** 検知ゼロの日も毎朝サマリを送るか／好機がある日だけ送るか。**推奨**: 毎朝サマリ＋検知時に詳細を厚く（`ALWAYS_DAILY_DIGEST=True` 既定）。通知過多の好みに関わるため `_open-questions.md` で確認・env で差替可。
- **[OPEN-O]** 通知履歴 UI（`GET /notifications`）を作るか。**推奨**: 作らない（Discord 自体が履歴・YAGNI）。必要になれば後付け。
- **[継承 U-9（ユーザー裁定）]** cron 起動時刻（既定 02:00 JST・`TZ=Asia/Tokyo`）。生活時間に関わるため `_open-questions.md` で確認・env で差替可（data-arch:722）。
- **[継承 OPEN-M]** 通知設定の API 化 or .env 固定。**確定（推奨）**: Webhook URL は `.env` 固定・しきい値も当面 env。UI 化は必要になってから（app.md:627・L-25）。
- **[OPEN-P]** ⑦ リバランス判定の「最終見直し日」の正本（`policy.updated_at` か直近 `advisor_journal.date` か）。**推奨**: `policy.updated_at`（方針更新が見直しの実体）。ai-advisor レーンの `policy` 更新タイミングと要すり合わせ。
- **[DOCS要修正]** data-model.md に `notifications` テーブル（`(notify_key, channel)` 複合 PK・自然キー）を追記（§2）。
