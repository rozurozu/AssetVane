"""銘柄テーマの grounded タガー（ADR-050 改訂・段階A・docs/data-model.md「テーマタグ」節）。

設計の真実: docs/decisions.md ADR-050 改訂（実在テキストに grounded な全ユニバース事前タグ）。

- **名前推測の禁止**: `code`/`symbol` は**同一性の識別子**としてだけ LLM に渡す。社名や
  ティッカーの字面から事業を推測させない。判定根拠は `company_descriptions` の実在テキスト
  （US は longBusinessSummary・JP は EDINET「事業の内容」要約＝ADR-055/056）のみ。
- **grounding 検証**: LLM が付けた各テーマには本文からの verbatim 引用（evidence）を要求し、
  空白正規化後に本文の部分文字列でないタグは破棄する（根拠なければタグ付けない・ADR-050）。
  evidence は**永続化しない**（stock_themes/themes に列なし・検証後 log のみ）。
- **語彙 exact 再用**: 既存テーマ語彙をプロンプトに注入し「該当あれば一字一句 exact に再用」
  させる（表記揺れは目録層の reconcile＝ADR-045/050 が吸収するが、まず付与時に抑える）。
- **定性タグのみ**: テーマは実在テキストの定性分類で数値を作らない（ADR-014）。
- **壊れた応答で落とさない**: JSON パース失敗・形不一致は「タグを付けない」側に倒す
  （ADR-018 の思想＝不確かなら書かない）。

接続規律（repo W1/W2 流儀）: `tag_stock_themes` の `conn` は**読み取り専用**
（get_company_description / list_theme_names）。書き込みは repo の engine 内蔵 W1 関数
（insert_themes_if_absent / upsert_stock_themes）が自前 begin で閉じる。呼び出し側は
書き込みトランザクションを開いたまま呼ばないこと（WAL なので読×書は競合しない・ADR-002）。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection

from app.db import repo
from app.services.llm_config import FaceNotConfiguredError

logger = logging.getLogger(__name__)

# プロンプトへ注入する既存語彙の上限（語彙が育ってもプロンプトが無限に膨らまないよう頭打ち）。
_VOCAB_PROMPT_MAX = 300

# 1 銘柄あたりの最大テーマ数（ADR-050: タグ爆発を防ぐ・プロンプトでも同数を指示）。
_MAX_THEMES_PER_STOCK = 5

# 空白正規化（grounding 検証用）: 全空白文字（改行・タブ・連続スペース）を単一スペースに潰す。
_WS_RE = re.compile(r"\s+")

# Markdown コードフェンス剥がし（```json ... ``` で包んで返すモデルへの防御・ADR-012 の
# 安いモデル前提では頻出の癖。中身の JSON だけを取り出す）。
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)

# タガーの指示（ADR-050 改訂の規律を明文化）。symbol は同一性のみ・根拠は本文のみ・
# 既存語彙 exact 再用・verbatim 引用・根拠なければ 0 件で可・JSON のみ出力。
_TAGGER_INSTRUCTION = (
    "あなたは銘柄に投資テーマのタグを付ける分類担当である。"
    "渡される symbol は銘柄の同一性を示す識別子にすぎない。"
    "社名やティッカーの字面から事業内容を推測してはならない。"
    "判定の根拠は、与えられた事業説明テキスト（description）のみとする。"
    "既存テーマ語彙（vocabulary）に該当するテーマがあれば、その名前を一字一句そのまま"
    "（exact に）再用すること。該当が無い場合のみ、簡潔な日本語の新テーマ名を提案してよい。"
    "各テーマには必ず、その判定根拠となる description 本文からの逐語引用（evidence）を付ける。"
    "evidence は本文の原文言語のまま一字一句変えずに抜き出すこと（本文が英語なら英語のまま）。"
    "本文に根拠が無いテーマを付けてはならない。根拠が見つからなければテーマは 0 件で構わない。"
    "テーマは 1 銘柄につき最大 5 個まで。"
    "出力は次の JSON オブジェクトのみとし、前後に地の文を付けない: "
    '{"themes": [{"name": "<テーマ名>", "evidence": "<本文からの逐語引用>"}]}'
)


def _now_iso() -> str:
    """現在時刻を ISO8601（UTC）で返す（dossier.py / nightly.py の作法に合わせる）。"""
    return datetime.now(UTC).isoformat()


def _normalize_ws(value: str) -> str:
    """空白正規化: 全空白文字を単一スペースに潰し前後を strip する（grounding 検証用）。"""
    return _WS_RE.sub(" ", value).strip()


async def tag_stock_themes(conn: Connection, *, market: str, code: str) -> dict[str, Any]:
    """1 銘柄を grounded タグ付けし themes / stock_themes に書き込む（ADR-050 改訂・段階A）。

    段取り: ① 実在テキスト（company_descriptions）を読む → 無ければ skip（テキスト無しに
    タグは付けない＝名前推測禁止の帰結）② 既存語彙をプロンプト注入用に読む ③ LLM 判定
    （classify_themes）④ 新語彙を themes 目録へ追加 ⑤ stock_themes へ UPSERT
    （衝突時は last_seen_at のみ bump＝repo 側の規約・クロバー回避）。

    Args:
        conn: 読み取り用接続（書き込みは repo の W1 関数が自前 begin・モジュール docstring）。
        market: 'JP'/'US'。
        code: JP 5桁コード or US symbol（同一性の識別子・名前推測はしない）。

    Returns:
        説明テキスト無し: `{"code", "themes": [], "skipped": True}`（静かに skip・ADR-018）。
        通常: `{"code", "themes": [name, ...], "n_new_themes": int}`。
    """
    # ① 実在テキスト。無ければタグを付けずに静かに返す（根拠なければタグ付けない・ADR-050）。
    description = repo.get_company_description(conn, market, code)
    description_text = ((description or {}).get("description_text") or "").strip()
    if not description_text:
        logger.info("tag_stock_themes: 事業説明テキストが無いため skip（%s/%s）", market, code)
        return {"code": code, "themes": [], "skipped": True}

    # ② 既存語彙（exact 再用のためプロンプトに注入・上限で頭打ち）。
    vocabulary = repo.list_theme_names(conn)
    vocab_for_prompt = vocabulary[:_VOCAB_PROMPT_MAX]

    # ③ LLM 判定（grounding 検証・上限打ち切り済みのタグが返る）。
    tags = await classify_themes(
        symbol=code, description_text=description_text, vocabulary=vocab_for_prompt
    )
    names = [t["name"] for t in tags]

    now = _now_iso()

    # ④ 新語彙（目録に無い名前）だけを themes へ追加（冪等・on_conflict_do_nothing）。
    #    比較は注入上限前の全語彙に対して行う（上限で隠れた既存語も二重登録しない）。
    known = set(vocabulary)
    new_names = [n for n in names if n not in known]
    n_new_themes = repo.insert_themes_if_absent(new_names, now)

    # ⑤ 銘柄×テーマ台帳へ UPSERT（first_assigned_at=last_seen_at=now の行を渡す。既存行は
    #    repo 側の規約で last_seen_at のみ bump され first_assigned_at は保持される）。
    rows = [
        {
            "market": market,
            "code": code,
            "theme_name": name,
            "first_assigned_at": now,
            "last_seen_at": now,
        }
        for name in names
    ]
    repo.upsert_stock_themes(rows)

    return {"code": code, "themes": names, "n_new_themes": n_new_themes}


async def classify_themes(
    *, symbol: str, description_text: str, vocabulary: list[str]
) -> list[dict[str, str]]:
    """事業説明テキストから投資テーマを LLM 単発で判定する（ADR-050 改訂・ADR-014）。

    LLM 単発 `engine.generate_once`（Tool ループ不要・provider は source="tagger" で解決＝
    未知 source は安全側に openai へ落ちる・ADR-012）。symbol は同一性の識別子としてのみ渡し、
    判定根拠は description_text に限定する（名前推測禁止）。応答は grounding 検証
    （evidence の本文照合）を通過したタグだけを返す。

    Args:
        symbol: 銘柄の同一性識別子（JP 5桁コード or US ティッカー）。
        description_text: 実在の事業説明テキスト（compact プロフィール・ADR-055/056）。
        vocabulary: 既存テーマ語彙（exact 再用を促すプロンプト注入用・上限済み）。

    Returns:
        `[{"name": ..., "evidence": ...}]`（検証済み・最大 _MAX_THEMES_PER_STOCK 件）。
    """
    payload = {
        "symbol": symbol,
        "description": description_text,
        "vocabulary": vocabulary,
    }
    messages: list[dict[str, object]] = [
        {"role": "system", "content": _TAGGER_INSTRUCTION},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]

    # engine は service→registry→handlers の import 鎖の先にあるため、関数内で遅延 import して
    # 循環 import を断つ（dossier.py と同じ流儀）。
    from app.advisor.engine import generate_once

    # tagger 面が未設定なら沈黙 skip（enrichment 扱い＝通知しない・ADR-058 確定8）。タグを付けない
    # 側に倒し（空リスト）、設定後の夜に再タグされる（cursor は自己回復）。
    try:
        content = await generate_once(messages, source="tagger")
    except FaceNotConfiguredError:
        logger.info("theme_tagger: tagger 面が未設定のためテーマ判定を沈黙 skip（ADR-058）")
        return []
    return _parse_tagger_response(content, description_text)


def _parse_tagger_response(content: str | None, description_text: str) -> list[dict[str, str]]:
    """LLM 応答からタグ列を取り出し grounding 検証する（堅牢化・ADR-018/050）。

    壊れた応答で銘柄処理を落とさない: JSON パース失敗・形不一致は**空リスト**
    （タグを付けない側に倒す）。evidence が空、または空白正規化後に本文の部分文字列で
    ないタグは破棄して log.warning（名前推測・捏造引用をここで断つ）。
    name は前後空白を strip し、空 name・重複 name は捨てる。
    採用は _MAX_THEMES_PER_STOCK 件で打ち切る。
    """
    if not content:
        return []

    # コードフェンス（```json ... ```）で包まれた応答は中身だけ取り出してからパースする。
    fence_match = _FENCE_RE.match(content)
    if fence_match:
        content = fence_match.group(1)

    try:
        parsed = json.loads(content)
    except (TypeError, ValueError):
        logger.warning("theme_tagger: 応答が JSON でないためタグを付けない（ADR-018）")
        return []

    if not isinstance(parsed, dict):
        logger.warning("theme_tagger: 応答が JSON オブジェクトでないためタグを付けない")
        return []
    items = parsed.get("themes")
    if not isinstance(items, list):
        logger.warning("theme_tagger: themes が配列でないためタグを付けない")
        return []

    normalized_desc = _normalize_ws(description_text)
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        if len(result) >= _MAX_THEMES_PER_STOCK:
            logger.warning(
                "theme_tagger: テーマが %d 件を超えたため打ち切る", _MAX_THEMES_PER_STOCK
            )
            break
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        evidence = item.get("evidence")
        if not isinstance(name, str):
            continue
        name = name.strip()
        if not name or name in seen:
            continue
        # grounding 検証: evidence が空 or 本文（空白正規化後）の部分文字列でなければ破棄。
        if not isinstance(evidence, str):
            evidence = ""
        normalized_evidence = _normalize_ws(evidence)
        if not normalized_evidence or normalized_evidence not in normalized_desc:
            logger.warning(
                "theme_tagger: evidence が本文に無いタグ %r を破棄（grounding 検証・ADR-050）",
                name,
            )
            continue
        seen.add(name)
        result.append({"name": name, "evidence": evidence})

    return result
