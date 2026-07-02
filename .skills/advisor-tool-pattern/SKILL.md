---
name: advisor-tool-pattern
description: AI Advisor の Tool（app/advisor/tools/ の registry/schemas/handlers）を新規追加・修正するとき必ず使う。Tool の正体（LLM が呼び出しを要求し Python が事実を返す）・3 ファイル分業と追加手順・handler の返り値は JSON-safe（Decimal/date/numpy を生で返さない）・例外を握って {error} に倒す・min_phase ゲート・結果値を tool_runs に載せない、を規定する。
---

# AI Advisor Tool 追加の作法

`app/advisor/tools/`（`registry.py` / `schemas.py` / `handlers.py`）。AI Advisor（LLM）が Tool Calling で呼べる関数を足す・直すときの規約。プロンプト（CORE/POLICY）そのものの作法は対象外（別途）。

## Tool の正体（ADR-014）

Tool は **LLM が「この関数をこの引数で呼びたい」と要求 → Python の handler が repo/quant で事実（数字）を計算 → dict を返し、それを JSON 化して LLM が解釈・提案する** 関数。**LLM 自身は計算しない**（ADR-014）。handler は「引数 → repo/quant → dict」の薄い橋渡しで、ロジック・数値計算は持たない（計算は [[backend-service-quant-pattern]]、クエリは [[backend-repo-pattern]] に出す）。

呼び出しは **`service.run_tool_loop` が `REGISTRY[name].handler(args)` を呼び、結果を `json.dumps` して LLM に返す**（provider は OpenAI 互換のみ・codex 経路は ADR-073 で撤去）。

## 3 ファイル分業と追加の 3 点セット

新しい Tool は必ず次の 3 点をセットで足す（1 つでも欠けると露出/検証がズレる）:

1. **`schemas.py`** … 引数の Pydantic モデル `XxxArgs`（余分引数を弾く）。引数なし Tool は空スキーマ（`{"type": "object", "properties": {}}`）を使う。
2. **`handlers.py`** … `async def handle_xxx(args: dict[str, object]) -> dict[str, Any]`。
3. **`registry.py`** … `REGISTRY` に `ToolDef(name=..., description=..., parameters=_schema(XxxArgs), handler=handlers.handle_xxx, min_phase=N)` を追加し、冒頭 import に `XxxArgs` を足す。

`min_phase` は **Phase ゲート**。`openai_tools(available_phase)` が `min_phase <= available_phase` の Tool だけを LLM に露出する。まだ通し検証していない Tool は高い `min_phase` にして隠す。

## handler の作法

```python
async def handle_get_xxx(args: dict[str, object]) -> dict[str, Any]:
    """<Tool の意図>（ADR-0xx）。<返す事実>だけを返す（verdict/判定は付けない＝ADR-014）。"""
    try:
        parsed = GetXxxArgs.model_validate(args)  # 余分/欠落した引数を弾く
        with get_engine().connect() as conn:
            row = repo.get_xxx(conn, parsed.code)
        return {"code": parsed.code, "found": row is not None, "value": row.get("value") if row else None}
    except Exception as exc:
        logger.exception("handle_get_xxx 失敗")
        return {"error": str(exc)}  # dispatch を止めない（落とさない・ADR-018）
```

- 引数は必ず `XxxArgs.model_validate(args)` で検証する（LLM は余分/欠落した引数を渡しうる）。
- **repo/quant を呼ぶだけ。数値計算・派生比率を handler で書かない**（事実は quant 純関数・ADR-014/016）。
- **例外は握って `{"error": str(exc)}` に倒す**＋`logger.exception`。1 つの Tool が落ちても dispatch ループ全体を止めない（ADR-018）。**HTTPException には翻訳しない**（handler は router ではない＝ここが [[backend-router-pattern]] と違う点）。
- 事実だけを返し **verdict（割安/割高・良し悪し）を付けない**。解釈は LLM の仕事（ADR-014）。
- `description`（registry）は **「いつ呼ぶか」を LLM 向けに** 書く。
- 検証専用 Tool（`submit_*` 系）は結果を DB に焼かず、W2 の persist を別経路（runner/router が `tool_runs` から拾う）が担う設計もある。既存の同種 Tool の役割分担に合わせる。

## 返り値は JSON-safe な素の型に限る（最重要）

**handler の返り値（ネストした dict/list の値も含む）は json 標準がシリアライズできる素の型に限る** ＝ `int` / `float` / `str` / `bool` / `None` / `dict` / `list`。

- ❌ `Decimal` / `date` / `datetime` / numpy scalar（`numpy.int64` 等）を **生で返さない**。
- **理由**: 返り値は `run_tool_loop` の `json.dumps` で JSON 化される。**`Decimal` は非対応で 500 になる**（実障害あり）。最終防波堤 `_tool_result_default`（`json.dumps(..., default=...)`）はあるが、それに寄りかからず**返す型を素で正しくする**（出所を断つのが本命）。
- **特に `func.percent_rank()` 等の window 関数・集約は SQLAlchemy が `Decimal` を返す**。これは **repo で `type_coerce(expr, Float())` して Float 化するのが正しい断ち方**（＝[[backend-repo-pattern]]）。handler 側で `float(...)` に包んで誤魔化さない（`None` 混入で落ちる・出所が濁る）。
- 日付は repo 側で ISO 文字列にして返すか、handler で `.isoformat()` する（生 `date`/`datetime` を返さない）。

## 結果値を tool_runs に載せない（ADR-025）

dispatch は `tool_runs` に **呼んだ Tool の name と args だけ** を蓄積する。**結果の数値は載せない**（画面コンテキストと同じ規律＝必要なら再び Tool で取り直す・ADR-025）。

## チェックリスト
- [ ] `schemas.py`（`XxxArgs`）／`handlers.py`（`handle_xxx`）／`registry.py`（`ToolDef` ＋ import）の 3 点を揃えた
- [ ] 引数は `XxxArgs.model_validate(args)` で検証（余分引数を弾く）
- [ ] repo/quant を呼ぶだけ・handler で数値計算していない（ADR-014/016）
- [ ] 例外を握って `{"error": str(exc)}`＋`logger.exception`（dispatch を止めない・HTTPException にしない）
- [ ] 返り値は JSON-safe な素の型のみ（`Decimal`/`date`/numpy を生で返していない）。window 関数由来は repo で Float 化（[[backend-repo-pattern]]）
- [ ] verdict を付けず事実だけ返す・`description` は「いつ呼ぶか」を LLM 向けに
- [ ] `min_phase` を正しく設定した（未検証 Tool は隠す）
