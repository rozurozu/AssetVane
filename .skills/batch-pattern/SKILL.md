---
name: batch-pattern
description: 夜間バッチ（batch/ 配下の runner・jobs・lock・notify・calendar・state）を新規作成・修正するときに必ず使う。1つの脳・2つの起動口(cron と POST /batch/run が同一関数を呼ぶ・ADR-011)・ロックで多重起動を防ぐ・各ジョブは独立/冪等/部分失敗から再開可能・個別ジョブ失敗は握って後続を止めず Discord 通知(ADR-007/018)・JobResult で結果集約・実行状態(running等)はメモリ singleton／停止フラグはファイル data/batch.stop でクロスプロセス協調キャンセル・長尺ジョブは stop_aware で最内ループ停止(ADR-036/070)、を規定する。
---

# 夜間バッチ規約

`batch/` は「ロック取得 → ジョブを順に実行 → 結果集約 → 失敗あれば Discord 通知 → ロック解放」。プロセス非依存に保ち、将来の専用 batch サービスへ移せるようにする（ADR-011・spec §3）。

## 1 つの脳・2 つの起動口

- 「毎晩 cron」と「`POST /batch/run` 手動」の**両口が同一プロセス・同一関数**（`run_nightly()` 等）を呼ぶ（ADR-011）。起動口ごとにロジックを分岐させない。
- 起動口の違いは**呼び出し側で吸収**する: ロック競合（`BatchAlreadyRunning`）は cron ではログ、`POST /batch/run` では 409 に翻訳（router 境界＝[[backend-router-pattern]]）。

## ロックで多重起動を防ぐ

- バッチ全体を `with lock.acquire():` で囲む。既に走っていれば専用例外（`BatchAlreadyRunning`）を送出する（握らず上へ）。
- 書き手を 1 つに寄せる方針（ADR-002）と整合。夜間バッチと昼 API の稀な書×書競合は SQLite の `busy_timeout` で吸収する。

## ジョブは独立・冪等・再開可能

- 各ジョブは独立した関数（`jobs/` 配下）。`NIGHTLY_JOBS` のような並びで runner が順に呼ぶ。
- **冪等**: 何度流しても壊れない（書き込みは UPSERT＝[[backend-repo-pattern]] の W1）。
- **部分失敗から再開可能**: 差分取得は `fetch_meta`（最後に取得した日付）を見て続きから取る。`full_backfill` フラグで頭から取り直す経路も用意する。ジョブのシグネチャに応じて `full_backfill` を渡し分ける（受けないジョブには渡さない）。
- **差分開始日の同型計算は純関数に寄せる**: 「`fetch_meta` の `last_fetched_date` から鮮度プローブ分だけ重ねて開始日を決める」計算が複数ジョブで同型になる場合、その**計算だけ**を `app/batch/jobs/_cursor.py` の純関数（DB を知らない・ADR-018）に寄せる。ジョブ側は `fetch_meta` の読み出し（DB アクセスはジョブ側に閉じる）と初期窓（`backfill_start`）を渡す責務を持つ。粒度（銘柄毎／全銘柄共通／単一ペア／ISIN 毎の `fetch_meta` キー）・初期窓の作り方（`BACKFILL_YEARS` 年前 か全履歴の番兵）・空取得時の前進可否はジョブごとに意味が違うので共通化しない（初期値差は `backfill_start` 引数で吸収）。
- **ほぼ同型のジョブ本体も `_`接頭の共通モジュールへ寄せる**: 2 ジョブが大半同一なら、共通本体を `app/batch/jobs/_xxx.py`（`_cursor.py` と同じ `_`接頭・内部モジュール）の関数に切り出し、各ジョブは**モジュール docstring（NIGHTLY 順序の根拠等）と `run()` を残して委譲**する。ジョブ固有差は引数で押し込む（実例＝`_theme_tagging.run_theme_tagging`＝US/JP の差を cap・選定クエリ・タガー・bump 最適化フラグの 4 引数に集約）。**LLM タガー等の差し替え対象は引数で受け取り、各ジョブが自分の名前空間から渡す**。これでテストの `monkeypatch.setattr(tag_us_themes, "tag_stock_themes", fake)`（ジョブモジュール属性の patch）が委譲後も効く（共通モジュール側に名前を持たせるとテストの patch seam が壊れる）。

## 個別ジョブ失敗は握って後続を止めない

- ジョブ単位で `try/except Exception` し、失敗を `JobResult(ok=False, ...)` に畳んで**後続ジョブを続行**する。1 ジョブの失敗で夜間バッチ全体を止めない。
- **各ジョブの `except Exception` には必ず理由コメント付き noqa を添える**。記法は統一する:

```python
except Exception as exc:  # noqa: BLE001 — ジョブ境界で握り runner に返す
```

  補足: `BLE001`（blind-except）は現状の ruff `select`（`E/F/I/UP/B`）には含まれないため実際には抑止対象ではないが、**「ここは意図的にジョブ境界で握っている」と読み手に示す統一記法**として必ず付ける（[[backend-foundations]]: `noqa`/`ignore` には理由コメント）。
- `JobResult`（`name`/`ok`/`rows`/`detail`）で結果を集約する。ログにも 1 ジョブ 1 行で残す。

```python
@dataclass
class JobResult:
    name: str
    ok: bool
    rows: int
    detail: str

def run_nightly(*, full_backfill: bool = False) -> list[JobResult]:
    results: list[JobResult] = []
    with lock.acquire():
        for job in NIGHTLY_JOBS:
            try:
                results.append(_invoke(job, full_backfill=full_backfill))
            except Exception as exc:  # noqa: BLE001 — ジョブ境界で握り runner に返す
                logger.exception("ジョブ %s 失敗", job.__name__)
                results.append(JobResult(name=job.__name__, ok=False, rows=0, detail=f"未捕捉例外: {exc}"))
    failed = [r for r in results if not r.ok]
    if failed:
        notify.error("夜間バッチでジョブが失敗", "\n".join(f"- {r.name}: {r.detail}" for r in failed))
    return results
```

## 実行状態と停止（status=メモリ・停止=ファイル・協調キャンセル・ADR-036/070）

2 つの関心を**別の器**に置く（ADR-070 が ADR-036 の保存方式を改訂）。

- **status（running / current_job / started_at / full_backfill）＝メモリ singleton**（`batch/state.py`）。WebUI に「動いているか・今どのジョブか」を見せる用。バッチは BackgroundTask（`/batch/run`）・APScheduler（cron）・CLI（`--nightly`）の**いずれも同一プロセス内**で走る（[[backend-foundations]]・ADR-005）ため、プロセスが死ねば表示も消えて整合する best-effort。**更新は `run_jobs()`（脳）の中だけ**（`state.begin()` → 各ジョブ前に `state.set_current_job(name)` → `finally: state.end()`）。`GET /batch/status` がそのまま返す（[[backend-router-pattern]]）。
- **停止フラグ（stop_requested）＝ファイル `data/batch.stop`（`batch.lock` の兄弟・ADR-070）**。相互排他はもう flock＝クロスプロセスなのに停止だけメモリに閉じていて、dev の `--reload` で分裂した別プロセスや CLU 起動から届かなかった（走行中バッチが古プロセスに残り、`POST /batch/stop` は前面プロセスのメモリに立つ）。ロックと同じ土俵にファイルで出すと reload/編集/CLI のどれでも届く。
  - `request_stop()`＝**running ゲートを撤廃**し常にファイルを touch（前面プロセスの running=false でも受理）。`should_stop()`＝ファイルの存在を見る（真実源）。
  - **ライフサイクルの不変条件**＝touch はロック外の `request_stop()` から・**unlink は flock 保持中の `begin()`/`end()` だけ**（`with lock.acquire()` 内）。これで走行中には必ず届き、idle 中の stray な要求は次 begin が回収する。起動時クリアはしない（orphan 宛の停止を誤消去しないため）。
- **停止は協調キャンセル**: `run_jobs` が**各ジョブの境界で** `state.should_stop()` を見て break（**今のジョブを終えてから止まる**＝強制 kill しない＝UPSERT 途中で切らず冪等性を壊さない）。
- **長尺ジョブは `state.stop_aware(iterable)` で最内ループでも見る（ADR-036 追補・ADR-070）**: `1 ジョブが数十分〜数時間`かかるもの（全ユニバース走査・営業日ループ・LLM/embed）は、ジョブ境界停止だけだと長すぎる（例＝3〜4 時間の `fetch_quotes` は 1 ジョブ）。最内ループを `for x in state.stop_aware(items):` で包む（should_stop が立つまで yield・立ったら打ち切り）。各反復で UPSERT＋`fetch_meta` 前進が済んでいれば「取れた分まで」永続化＋冪等再開でき歴史に穴は空かない。**helper 化で 1 行になりコストがほぼゼロなので、cap 付き LLM/embed/巡回系にも一律で被せる**（旧版の「cap で短いものには足さない＝過剰」は撤回・ADR-070。`while True` 再取得ループなど iterable でない所は先頭で `if state.should_stop(): break`）。「停止で打ち切ったか」を detail に出したいジョブは**ループ後**に `state.should_stop()` を見て「停止により中断」を添える（実例＝`fetch_quotes`/`fetch_financials`）。なお走行中ジョブを**今すぐ**止めたいときはプロセス再起動（`docker compose restart backend`）も確実（UPSERT 冪等で DB は壊れない）。
- **中断は「正常終了」扱い**: 停止で残ジョブを飛ばしたときは `notify.error` を**鳴らさない**（ユーザー操作は失敗ではない）。失敗通知は通常完了時のみ。
- **dev の reload-orphan に注意**: バッチ稼働中にソース編集や `uv run` をすると `--reload` で走行中バッチが古プロセスに取り残される（stop はファイルなので効くが、status は前面プロセスで running=false に見え UI に停止ボタンが出ない＝直 API で止める）。**編集したければ stop→編集→再開**が安全（ADR-070）。

```python
stopped = False
with lock.acquire():
    state.begin(full_backfill=full_backfill)
    try:
        for job in NIGHTLY_JOBS:
            if state.should_stop():          # ジョブ境界で停止を確認（今のジョブ完了後に止まる）
                stopped = True
                break
            state.set_current_job(name)
            ...
    finally:
        state.end()
if not stopped:                              # 停止は失敗ではないので通知しない
    ... notify.error(...) ...
```

## 通知は Discord（無人バッチのみ）

- 失敗があれば **Discord Webhook** で 1 度だけ通知する（ADR-007: LINE Notify は終了済みなので使わない）。
- 通知は**無人バッチの失敗**に限る。対話的なチャットの失敗は通知しない（ADR-018）。

## 重い処理の置き場所・夜の制約

- ML 学習は別 PC（ラズパイは `.pkl` 推論のみ・ADR-006）。LLM 推論は OpenRouter（ADR-012）。
- MCP のニュース取得は昼チャットでは使えても**無人 cron では使えないことがある**ので、夜のジョブは軽め・外部依存少なめに保つ（ADR-020）。

## チェックリスト

- [ ] cron と `POST /batch/run` が同一関数を呼ぶ（起動口で分岐していない）。競合は呼び出し側で翻訳（cron=ログ / API=409）
- [ ] バッチ全体を `lock.acquire()` で囲み、多重起動は専用例外で弾く
- [ ] 各ジョブは独立・冪等（UPSERT）・`fetch_meta`/`full_backfill` で再開可能
- [ ] 差分開始日の同型計算は `_cursor.py` の純関数へ（DB を知らない）。粒度・初期窓の作り方・空取得時の前進可否はジョブ固有に残した
- [ ] ほぼ同型の 2 ジョブ本体は `_`接頭の共通モジュール（例 `_theme_tagging.py`）へ寄せ、各ジョブは docstring＋`run()` を残して委譲した。差し替え対象（タガー等）は引数で受け各ジョブの名前空間から渡し、テストの patch seam を保った
- [ ] 個別ジョブ失敗を握って後続を止めず `JobResult(ok=False)` に集約。`except Exception` に統一記法の理由コメント付き noqa（`# noqa: BLE001 — ジョブ境界で握り runner に返す`）を添えた
- [ ] 失敗時のみ Discord 通知（無人バッチに限る・対話チャットは通知しない）。停止（協調キャンセル）での中断は「正常終了」扱いで通知しない（ADR-036）
- [ ] status（running 等）はメモリ singleton（`batch/state.py`）に持ち、更新は `run_jobs` の中だけ（DB スキーマを増やさない・ADR-005/036）。停止フラグは**ファイル `data/batch.stop`**（`request_stop` は running ゲートなしで touch・`should_stop` は存在を見る・unlink は flock 内の begin/end だけ）でクロスプロセスに効かせる（ADR-070）。ジョブ境界で `should_stop` を見て break（強制 kill しない）
- [ ] 長尺ジョブ（全ユニバース走査・営業日ループ・LLM/embed で数十分〜数時間）は**最内ループを `state.stop_aware(items)` で包む**（`while` 再取得は先頭で `if should_stop(): break`）。helper 化でコストほぼゼロなので cap 付き系にも一律で足す（ADR-036 追補・ADR-070）。detail に出すジョブはループ後に `should_stop` を見て「停止により中断」を添えた
- [ ] 夜のジョブは軽め（無人で使えない外部依存に頼らない）
- [ ] docstring 冒頭に ADR-002/007/011/018・spec 参照
