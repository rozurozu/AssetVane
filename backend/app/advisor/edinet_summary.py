"""EDINET 有報「事業の内容」の要約（テーマタグ段階C・ADR-056・ADR-020・ADR-014）。

設計の真実: docs/decisions.md ADR-056（EDINET を JP の事業説明テキスト源にする）・ADR-020
（「取得 → 要約 → 本文は捨てる」イディオム）・ADR-014（AI に数値を計算させない／LLM は解釈のみ）。

EdinetAdapter（adapters/edinet.py）が抜いた事業の内容（数ページの生テキスト）を、grounded テーマ
タガー（advisor/theme_tagger.classify_themes）が evidence を verbatim 照合できる compact プロ
フィールへ要約する。タガーは要約文から根拠句を引用するため、**製品・セグメント・事業領域の具体
名詞を保つ**ことが要約の肝（抽象化しすぎると grounding できるテーマが減る）。

adapter は IO 専用で LLM に触れない（ADR-010）ので、要約（LLM 呼び出し）はこの service 相当の
ヘルパに置く。news.summarize_article（ニュース記事 2〜3 行要約）とは目的・粒度が違う（事業構造の
保持 vs 出来事の圧縮）ため流用せず、専用指示を持つ。
"""

from __future__ import annotations

# 事業の内容の要約指示（最小指示＋本文を渡し generate_once を 1 回呼ぶ・summarize_article 流儀）。
# 具体名詞の保持と、本文に無い情報の補完禁止（grounding を壊さない・名前推測禁止＝ADR-050）。
_EDINET_SUMMARIZE_INSTRUCTION = (
    "あなたは有価証券報告書の「事業の内容」を要約する担当である。"
    "渡された本文に書かれている事実のみを使い、その企業が何の事業を営むかを日本語で簡潔にまとめよ。"
    "投資テーマの判定に使えるよう、製品名・サービス名・事業セグメント・対象市場・技術領域などの"
    "具体的な名詞を残すこと。一般化・抽象化しすぎて固有の事業内容が消えないようにする。"
    "本文に無い情報の補完・推測・将来予測はしない。社名やブランドから事業を推測しない。"
    "前後に地の文・見出し・箇条書き記号を付けず、数文〜十数文のプレーンな段落で出力する。"
)

# 要約に渡す本文の上限文字数（トークン暴走の保険。事業の内容は数千字なので頭から十分量を渡す）。
_MAX_INPUT_CHARS = 12_000


async def summarize_business_description(text: str) -> str:
    """事業の内容の生テキストを LLM 単発で compact 要約する（ADR-056/020・grounding 保持）。

    advisor の CORE/POLICY は使わず最小指示＋本文を渡して generate_once を 1 回呼ぶ
    （summarize_article と同じ流儀）。source="dossier"＝夜のテキスト要約レーンの面別 provider/model
    設定（ADR-012/058・resolve_face）を流用する。engine は import 鎖の先にあるため遅延 import で
    循環を断つ。dossier 面が未設定なら FaceNotConfiguredError が伝播し、呼び出し元（夜間ジョブ）が
    runner 集約で通知する（ADR-018・確定8）。

    Args:
        text: EdinetAdapter が抜いた事業の内容（HTML strip 済みプレーンテキスト）。

    Returns:
        compact 要約（company_descriptions.description_text に焼く本文）。
    """
    from app.advisor.engine import generate_once

    body = text[:_MAX_INPUT_CHARS]
    messages: list[dict[str, object]] = [
        {"role": "system", "content": _EDINET_SUMMARIZE_INSTRUCTION},
        {"role": "user", "content": body},
    ]
    return await generate_once(messages, source="dossier")
