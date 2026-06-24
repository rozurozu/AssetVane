---
name: testing-strategy
description: backend のテスト（pytest）を新規作成・修正するときに必ず使う。本物の DB に触れず一時 SQLite で回す・conftest の temp_db/client フィクスチャの使い分け・quant 純関数は DataFrame 直書きで検証・ネットに出ない（外部 API は呼ばずサンプル dict で正規化を検証）・テスト配置と命名、を規定する。frontend に自動テストは現状なし。
---

# テスト規約（pytest）

backend は `pytest`。**本物の DB（`data/assetvane.db`）に触れず、ネットにも出ない**。`uv run pytest -q` で回る（`pythonpath = ["."]` で `app` を解決）。

> frontend には現状 自動テストを置いていない（型は `tsc --noEmit`、lint は Biome で担保）。テストを足す場合はこのスキルを拡張する。

## 本物の DB に触れない（一時 SQLite）

DB を触るテストは `tmp_path` の使い捨て SQLite を使う。`settings.database_path` を monkeypatch で差し替え、engine キャッシュをリセットする。2 つのフィクスチャを使い分ける:

- **`temp_db`**: `settings.database_path` を一時ファイルに差し替え、`create_schema()`（`metadata.create_all`）で**速く**空スキーマを用意する。repo/service の単体テスト向け。
- **`client`**: `TestClient(app)` を lifespan 付きで起こす。lifespan の `init_db()`（**alembic upgrade**）がスキーマを作るので、`create_schema` と**併用しない**（二重に作ると `op.create_table` が "table already exists" で落ちる）。エンドポイントの結合テスト向け＝本番と同じ alembic 経路。

```python
@pytest.fixture
def temp_db(tmp_path, monkeypatch) -> Iterator[None]:
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(settings, "database_path", str(db_file))
    db_engine.reset_engine()
    db_engine.create_schema()
    yield
    db_engine.reset_engine()
```

- テストの前後で `reset_engine()` を呼び、エンジンキャッシュ（プロセス内シングルトン）が他テストに漏れないようにする。
- alembic 経路そのもの（マイグレーションの整合）は専用テストで別途検証する。それ以外は `temp_db`（create_schema）で速く回す。

## ネットに出ない（外部 API を呼ばない）

- J-Quants 等の取得は**呼ばない**。アダプタの正規化（外部キー→内部列）は**サンプル dict を渡して検証**する（[[backend-adapter-pattern]] の `_first`/`_norm_*` の入出力）。HTTP は実際に飛ばさない。
- 取得→保存の結合を見たい場合はアダプタを差し替える（モック/フェイク）か、正規化済み行を直接 repo に渡す。
- **LLM も同じ。`client`/`temp_db` フィクスチャは `seed_llm_config()` で openai provider 1 行＋4 面（chat/nightly/dossier/tagger）を一時 DB に seed** し、LLM 経路を openai（モック可能）に固定する（ADR-058・面別設定は env ではなく DB なので・旧 `settings.llm_provider_*=openai` monkeypatch の後継）。これがないと `resolve_face` が `FaceNotConfiguredError` で落ち `/chat` は 503、あるいは codex を割り当てると実 `codex app-server` subprocess＋MCP が起動してモック素通り・teardown で `Event loop is closed` になる（ADR-012/032/058）。LLM 応答は `app.advisor.service.complete`（`get_client` を fake に差し替え or `service.complete` を monkeypatch）でモックする。面解決の振り分けは `test_llm_config.py`（resolve_face）/`test_engine_dispatch.py`/`test_codex_engine.py` がモックで別途検証する。

## quant 純関数は DataFrame 直書き

quant の純関数（[[backend-service-quant-pattern]]）は DB も FastAPI も要らない。**小さな入力 DataFrame と期待値を直書き**して検証する。

```python
def test_momentum_basic():
    panel = pd.DataFrame({"7203": [100, 110, 121]}, index=["2024-01-01", "2024-01-02", "2024-01-03"])
    result = compute_momentum(panel)
    assert result[...] == pytest.approx(...)
```

- 比較は**戻り値の型に応じて使い分ける**。現状 AssetVane の quant は **dict を返す契約**（[[backend-service-quant-pattern]]）なので、基本は次で足りる:
  - **dict の場合**: dict として比較し、**数値は `pytest.approx`**（`np.testing.assert_allclose` も可）。`assert_series_equal` は不要。
  - **戻り値が Series / DataFrame のときに限り**: pandas の `assert_series_equal` / `assert_frame_equal` を使う。
- 境界（1 銘柄・履歴不足・NaN 混在）が**安全な既定**を返すことを必ずテストする（quant の規約と対）。
- テストの **docstring 冒頭に「何を担保するか＋関連 ADR/spec 参照」を日本語で書く**（[[batch-pattern]] 等 他スキルと統一。意図の出所を辿れるように）。

## エンドポイントテスト

- `client` フィクスチャで `client.get("/...")` / `client.post(...)`。ステータスとレスポンス JSON を検証。
- 接続 dep を差し替えたい単体寄りのケースは `app.dependency_overrides[get_conn] = ...` でも可（本番 engine を触らない）。基本は `client`（一時 SQLite ＋ alembic）で十分。

## 配置・命名

- テストは `backend/tests/` に `test_*.py`。対象に対応する名前（`test_quant_momentum.py`・`test_portfolio_api.py`・`test_repo.py` 等）。
- 1 テスト 1 観点。docstring/名前で「何を担保するか」を日本語で表す。

## チェックリスト

- [ ] 本物の DB に触れていない（`tmp_path` の一時 SQLite ＋ monkeypatch ＋ `reset_engine`）
- [ ] スキーマ用意は `temp_db`（create_schema）か `client`（alembic lifespan）のどちらか。併用していない
- [ ] ネットに出ていない（外部 API はサンプル dict で正規化を検証）
- [ ] LLM は openai 固定（`client` が provider を pin）＋ `service.complete` をモック。codex 実 subprocess に逸れていない
- [ ] quant は DataFrame 直書き。dict 戻り値は dict 比較＋数値 `approx`／Series・DataFrame 戻り値のときのみ `assert_series_equal`/`assert_frame_equal`。境界（不足/NaN）の既定もテスト
- [ ] テストの docstring 冒頭に担保内容＋関連 ADR/spec 参照を日本語で書いた
- [ ] エンドポイントは `client` でステータス＋JSON 検証
- [ ] `tests/test_*.py` に配置、名前で対象が分かる、観点ごとに分割
- [ ] `uv run pytest -q` が緑
