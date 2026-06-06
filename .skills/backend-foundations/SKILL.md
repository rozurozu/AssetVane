---
name: backend-foundations
description: backend(FastAPI/Python 3.12) のあらゆるモジュールを新規作成・修正するときに必ず使う横断作法。型注釈・docstring の ADR/docs 参照・例外と境界での HTTPException 翻訳・同期/async の使い分け・ruff/pyright・ログ・レイヤ分離の全体像を規定する。レイヤ個別の規約は backend-router/repo/service-quant/adapter の各スキルへ。
---

# backend 共通作法（foundations）

FastAPI / Python 3.12 / SQLAlchemy Core。**DB に触れるのは FastAPI だけ**（ADR-005）。このスキルは全 backend モジュール共通の地の作法をまとめる。レイヤ固有は [[backend-router-pattern]] / [[backend-repo-pattern]] / [[backend-service-quant-pattern]] / [[backend-adapter-pattern]] を見る。

## レイヤ分離（全体像）

薄く分ける。各層は下の層だけに依存し、逆流しない。

```
routers/   HTTP 入出力のみ。Pydantic で受け/返し、ロジックを持たない        → [[backend-router-pattern]]
services/  下ごしらえ・オーケストレーション（quant 呼び出し前の整形 等）   → [[backend-service-quant-pattern]]
quant/     純関数の数理計算（DB を知らない・pandas/numpy/scipy）            → [[backend-service-quant-pattern]]
db/repo.py SQLAlchemy Core のクエリ。戻り値は素の dict                       → [[backend-repo-pattern]]
db/schema.py  Table 定義（スキーマの一元管理）                               → [[backend-repo-pattern]]
adapters/  外部 API。外部キー名→内部列名の対応を閉じ込める                  → [[backend-adapter-pattern]]
batch/     夜間バッチのジョブ・ロック・通知                                  → [[batch-pattern]]
```

不変条件（ADR・破らない）:
- **AI も router も数値計算しない**。計算は quant の純関数（ADR-014/016）。
- **DB に触るのは FastAPI のみ**（ADR-005）。
- **単一ユーザー・認証なし**（ADR-001）。`user_id` を足さない。

## モジュールの地の作法

- **全モジュール先頭に `from __future__ import annotations`**。
- **型注釈を省略しない**（pyright standard 前提）。関数の引数・戻り値・公開属性に型を付ける。`Any` は外部 JSON の境界など本当に必要な所だけ。
- **docstring 冒頭で意図の出所を引く**。モジュール/重要関数の docstring 先頭に**該当 ADR 番号や `docs/` 参照**を書く（例: `（ADR-005）`・`（docs/api.md §1）`・`（phase2-spec.md §5）`）。設計判断を勝手に作らない。迷ったら `docs/` が真実。
- **コメント・docstring はすべて日本語**。

```python
"""シグナル一覧の REST ルータ（Phase 1／docs/api.md §1）。

API は事前計算済みの事実を読むだけ（ADR-014: AI/API は計算しない）。
"""

from __future__ import annotations
```

## 同期 `def` を使う（async にしない）

このプロジェクトは**同期ドライバ（sqlite3）＋ SQLAlchemy Core**。FastAPI のルート・依存（dep）は **同期 `def`** で書く。FastAPI が blocking I/O を別スレッドプールで捌くため、イベントループを止めない。`async def` 内で同期 DB を呼ぶのはイベントループをブロックする誤り。

例外: 外部 LLM 呼び出しのように `await` する I/O を内部に持つ口（相談チャット等）は `async def` でよい。その場合 DB 読み取りは dep に頼らず `with get_engine().connect() as conn:` を関数内で短く開閉する。

逆向きの口として、**同期バッチ層から `asyncio.run(async_fn())` で async を駆動するパターンも許容する**（例: 夜間バッチが内部で LLM を await する nightly 系）。FastAPI のルート層（イベントループ上で同期 `def` を保つ）とバッチ層（自前でループを起こして async を駆動する）は役割が違うので、層ごとにこの使い分けを守る。

## 例外と境界での翻訳

- **用途別の独自例外**を定義する（例 `JQuantsError(RuntimeError)`・`CostGuardError`）。`raise Exception(...)` で済ませない。
- **独自例外は router 境界で `HTTPException` に翻訳**する。翻訳先のステータスは意味に合わせる（未取得=404・上流失敗=502・コスト上限=429・競合=409）。`from exc` で連鎖を残す。
- グローバルに翻訳したい横断例外は `@app.exception_handler(MyError)` でまとめてもよい。router 内の局所的なものは `raise HTTPException(...)` で個別に。
- **バッチの個別ジョブ失敗**は握って後続を止めない（[[batch-pattern]]）。それ以外で握りつぶさない。

## ログ方針（ADR-038）

- ログは標準 `logging`（`logger = logging.getLogger(__name__)`）。
- **フォーマットは人間可読のテキスト**＝`%(asctime)s %(levelname)s %(name)s: %(message)s`（JSON 集約は将来 Mac mini 導入時に再検討）。
- **レベルは `LOG_LEVEL` env で root を可変**（既定 `INFO`・`config.py` の `log_level`）。障害解析時は `DEBUG` に上げる。
- **設定の所在は `app/logging_config.py:setup_logging()` に一元化**し、**`app/main.py` が import 時に呼ぶ**（`dictConfig`・uvicorn ロガー整合）。各モジュールで `basicConfig` やハンドラ設定をしない。
- **出力は stdout/stderr に寄せる**。**アプリ側 FileHandler は使わない**（ファイルを持つのは FastAPI が扱う DB だけ＝ADR-005 と二重管理を避ける）。Pi での永続化は docker json-file ローテーション（`compose*.yaml` の `logging:` 10m × 5）が担う。
- **`/health` の access ログは抑制**（定期ヘルスチェックで本当に見たいログが埋もれるのを防ぐ）。`setup_logging()` で uvicorn.access のフィルタとして設定する。
- 失敗・警告は**コンテキストを最も持つ層で 1 度だけ**出す。二重ログ（log-and-rethrow を各層で）はしない。
- **`except` での握り潰しは禁止**（frontend の `.catch` も同様）。握って後続を進めてよいのは [[batch-pattern]]（ジョブ単位で握り後続を止めない）など**意図を明記した箇所だけ**。それ以外は最もコンテキストを持つ層で 1 度だけ出す。
- 無人バッチの失敗は Discord 通知（ADR-007/018）。ログとは**別経路**（ログを読みに行かなくても気づける）。対話的なチャットの失敗は通知しない（HTTPException で返すだけ）。

## lint / format / 型

- **Ruff**（`select = ["E","F","I","UP","B"]`・line-length 100）。`uv run ruff check . && uv run ruff format .` で差分を出さない。
- **pyright standard**。型エラーを残さない。
- `noqa` / `ignore` する時は**理由をコメントで添える**（例: `# noqa: BLE001 — ジョブ単位で握り後続を止めない`）。
- 依存は **uv 管理**（`pip`/`requirements.txt` を使わない・ADR-023）。

## チェックリスト

- [ ] 先頭に `from __future__ import annotations`
- [ ] 引数・戻り値・公開属性に型注釈（pyright standard で緑）
- [ ] docstring 冒頭に ADR 番号 / `docs/` 参照（日本語）
- [ ] ルート・dep は同期 `def`（`await` I/O を持つ口だけ `async def`）
- [ ] 例外は用途別の独自例外 → 境界で `HTTPException`（`from exc` で連鎖）
- [ ] 数値計算を router/AI でしていない（quant の純関数に置いた）
- [ ] `ruff check` / `ruff format` / pyright が緑。`noqa`/`ignore` には理由コメント
