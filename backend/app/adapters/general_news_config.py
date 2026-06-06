"""一般ニュースダイジェストのカテゴリ定義と取得パラメータ（ADR-034）。

設計の真実: docs/decisions.md ADR-034・grill-me 合意（3-adr-034-floofy-hoare）。

ADR-034 は「銘柄に紐づかない一般ニュース」を dossier_sources（code FK 必須）とは別系統で
持つ構想。本モジュールはその取得対象（カテゴリ＝ラベル＋Google News 検索クエリ）と件数の
天井を **定数** で持つ。

なぜ env / config.py に置かないか（grill-me 確定事項5）:
  カテゴリ定義は「環境ごとに切り替える運用パラメータ」ではなく、コードと一緒に育てる
  安定資産（CORE プロンプト・手法カードと同類＝ADR-020 の精神）。構造データ（list[dict]）を
  .env の JSON 文字列で持つとパース・同期が煩雑なだけで益が無い。ADR-010 が禁じるのは
  「接続情報（URL・APIキー）のハードコード」であって、検索キーワードという参照知識は別物。
  実際の Google News への接続情報（base_url / lang / country / timeout）は従来どおり settings。

クエリは Google News 検索構文（OR で語をまとめられる）。投資ダッシュボードなので
市況・マクロ・世界情勢に寄せる（汎用トピックフィードより文脈に効く）。
"""

from __future__ import annotations

# カテゴリ定義（label=表示名／query=Google News 検索クエリ）。
# 1 人用ダッシュボードの市況文脈に効く 3 本。増減はここを編集する（env 不要）。
GENERAL_NEWS_CATEGORIES: list[dict[str, str]] = [
    {
        "label": "市況・マーケット",
        "query": "株式市場 OR 日経平均 OR 東証 OR NYダウ OR ナスダック",
    },
    {
        "label": "マクロ経済・金融政策",
        "query": "金融政策 OR 日銀 OR FRB OR 金利 OR インフレ OR 為替",
    },
    {
        "label": "世界情勢",
        "query": "世界情勢 OR 国際情勢 OR 地政学 OR 貿易摩擦",
    },
]

# カテゴリあたりの要約上限（コスト天井）。カテゴリ数 × 本数ぶん LLM 要約が走るため低めに保つ。
GENERAL_NEWS_MAX_PER_CATEGORY: int = 5

# 取得 lookback 日数（発行がこの日数以内の記事のみ拾う）。当日の市況文脈が目的なので短く。
GENERAL_NEWS_LOOKBACK_DAYS: int = 2
