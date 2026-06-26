"""知識カードの初期シード（ADR-062）。旧 jp-market-context.md の市場文脈を DB へ移す。

    uv run python -m app.scripts.seed_knowledge_cards

ADR-062 で手法カード（cards/*.md・常時注入）を廃し、知識を knowledge_cards へ移した。規律
（単一指標で決めるな 等）は CORE へ吸収、一般教科書知識（PER 15 倍が目安 等）は LLM に任せて drop。
残る市場固有の時事文脈（低 PBR 是正・持ち合い解消・ガバナンス改革・脱デフレ）は LLM が知らない/
古いことがあるので知識カードとして残す（status='active'・level='market'）。

冪等: 同じ title が既にあれば skip。マイグレーションでなくスクリプトで入れる（0025 は「シードは
別途」と明記・rich markdown を migration に詰めない）。
"""

from __future__ import annotations

from app.db import repo
from app.db.engine import get_engine, init_db

# 旧 jp-market-context.md（ADR-048/041）の市場文脈を 1 文脈 1 カードへ（level='market'・active）。
_SEED_CARDS: list[dict[str, str]] = [
    {
        "title": "東証の低 PBR 是正要請（資本コスト経営）",
        "when_to_apply": "日本株の PBR が 1 倍前後・割安に見える銘柄を評価・割安判断するとき",
        "body": (
            "2023 年以降、東証はプライム/スタンダード上場企業に「資本コストや株価を意識した経営」を"
            "要請し、特に PBR が 1 倍を割る企業に改善策の開示を促している。含意: PBR<1 は単なる割安"
            "シグナルでなく、改革のカタリスト（自社株買い・増配・事業再編・ROE 改善）になりうる。"
            "低 PBR 企業は「放置された割安」か「動き出す割安」かを区別する。"
        ),
    },
    {
        "title": "政策保有株（持ち合い）の解消",
        "when_to_apply": "持ち合いの多い日本企業の資本効率/株主還元余地を評価するとき",
        "body": (
            "ガバナンス改革の一環で、政策保有株（事業上の関係維持目的の持ち合い）の縮小が進む。"
            "含意: 持ち合い解消はバランスシートのスリム化・資本効率（ROE）改善・株主還元余地に"
            "つながりうる。保有株の多い企業はその解消余地を材料として見る。"
        ),
    },
    {
        "title": "コーポレートガバナンス改革・株主アクティビズム",
        "when_to_apply": "株主還元方針の変化やアクティビスト関与のある日本株を評価するとき",
        "body": (
            "ガバナンスコード浸透とアクティビストの活動で、株主還元（自社株買い・増配）の増加・"
            "取締役会の独立性向上が進む。含意: 還元方針の変化・アクティビストの関与は株価の"
            "カタリストになる。ROE・配当性向・自己株買いの動向と併せて見る。"
        ),
    },
    {
        "title": "脱デフレ後のインフレ転換",
        "when_to_apply": "日本株の価格転嫁力・名目成長・金利感応度を評価するとき",
        "body": (
            "数十年のデフレから、インフレ・名目成長・金利のある世界への転換が進む。含意: 価格"
            "転嫁力・名目成長・金利環境が企業価値に効くようになる。割引率・ターミナル成長の前提や、"
            "ディフェンシブ/シクリカルの強弱が変わりうる。"
        ),
    },
]


def seed() -> int:
    """市場文脈カードを冪等に投入し、新規挿入した件数を返す（既存 title は skip）。"""
    init_db()
    with get_engine().connect() as conn:
        existing = {row["title"] for row in repo.list_knowledge_cards(conn)}

    inserted = 0
    for card in _SEED_CARDS:
        if card["title"] in existing:
            print(f"  skip（既存）: {card['title']}")
            continue
        repo.insert_knowledge_card(
            title=card["title"],
            body=card["body"],
            when_to_apply=card["when_to_apply"],
            status="active",
            level="market",
        )
        inserted += 1
        print(f"  挿入: {card['title']}")

    print(f"✔ 完了: 知識カード {inserted} 件を挿入（既存は skip）")
    return inserted


if __name__ == "__main__":
    seed()
