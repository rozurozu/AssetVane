---
name: batch-pattern
description: 夜間バッチ（batch/ 配下の runner・jobs・lock・notify・calendar）を新規作成・修正するときに必ず使う。1つの脳・2つの起動口(cron と POST /batch/run が同一関数を呼ぶ・ADR-011)・ロックで多重起動を防ぐ・各ジョブは独立/冪等/部分失敗から再開可能・個別ジョブ失敗は握って後続を止めず Discord 通知(ADR-007/018)・JobResult で結果集約、を規定する。
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
- [ ] 個別ジョブ失敗を握って後続を止めず `JobResult(ok=False)` に集約。`except Exception` に統一記法の理由コメント付き noqa（`# noqa: BLE001 — ジョブ境界で握り runner に返す`）を添えた
- [ ] 失敗時のみ Discord 通知（無人バッチに限る・対話チャットは通知しない）
- [ ] 夜のジョブは軽め（無人で使えない外部依存に頼らない）
- [ ] docstring 冒頭に ADR-002/007/011/018・spec 参照
