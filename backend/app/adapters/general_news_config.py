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


# ── セクターニュース（ADR-044 (ii) セクター層）────────────────────────────────
# 統合ニュースコーパスの 3 階層（銘柄/セクター/市況）のうち「セクター」層を埋めるための
# 業種別 Google News 検索クエリ（ADR-044）。キーは TOPIX-17 業種コード '1617'..'1633'
# （stocks.sector17_code・news.sector17_code と同じ 4 桁体系）。label は services/lead_lag.py
# の JP_SECTOR_LABELS の和名に揃える（ただし import せず self-contained に写経。役割が違うため
# ＝lead_lag は ETF payload の表示名、こちらはニュース category 表示名。安定資産なので二重管理を
# 許容し、循環 import / 改名の巻き添えを避ける）。
#
# query は「業種を表す日本語キーワードの OR 連結」。GENERAL_NEWS_CATEGORIES と同じく Google News
# 検索構文。業種名そのものだと検索語として弱い業種があるため、業種名＋代表企業/具体語を混ぜる。
# クエリ文言はチューニング可（拾いの良し悪しを見て編集する。これは接続情報ではなく参照知識なので
# env 化しない＝GENERAL_NEWS_CATEGORIES と同じ判断・ADR-010/ADR-034）。
SECTOR_NEWS_QUERIES: dict[str, dict[str, str]] = {
    "1617": {"label": "食品", "query": "食品業界 OR 食品メーカー OR 飲料 OR 味の素 OR キリン"},
    "1618": {
        "label": "エネルギー資源",
        "query": "石油 OR 原油 OR エネルギー資源 OR ENEOS OR INPEX OR 天然ガス",
    },
    "1619": {
        "label": "建設・資材",
        "query": "建設業界 OR ゼネコン OR 建設資材 OR 大林組 OR 鹿島建設",
    },
    "1620": {
        "label": "素材・化学",
        "query": "化学メーカー OR 素材産業 OR 信越化学 OR 三菱ケミカル OR 化学業界",
    },
    "1621": {
        "label": "医薬品",
        "query": "製薬 OR 医薬品 OR 創薬 OR 武田薬品 OR 第一三共 OR バイオ医薬",
    },
    "1622": {
        "label": "自動車・輸送機",
        "query": "自動車 OR 自動車業界 OR トヨタ OR ホンダ OR 輸送機 OR EV",
    },
    "1623": {
        "label": "鉄鋼・非鉄",
        "query": "鉄鋼 OR 非鉄金属 OR 日本製鉄 OR JFE OR 銅 OR アルミ",
    },
    "1624": {
        "label": "機械",
        "query": "機械業界 OR 産業機械 OR 工作機械 OR ファナック OR コマツ OR 建機",
    },
    "1625": {
        "label": "電機・精密",
        "query": "電機メーカー OR 半導体 OR 精密機器 OR ソニー OR キーエンス OR 電子部品",
    },
    "1626": {
        "label": "情報通信・サービスその他",
        "query": "情報通信 OR IT業界 OR 通信 OR ソフトバンク OR NTT OR ソフトウェア",
    },
    "1627": {
        "label": "電力・ガス",
        "query": "電力会社 OR ガス会社 OR 東京電力 OR 関西電力 OR 電気料金 OR 都市ガス",
    },
    "1628": {
        "label": "運輸・物流",
        "query": "運輸 OR 物流 OR 海運 OR 鉄道 OR 日本郵船 OR ヤマト運輸 OR 航空",
    },
    "1629": {
        "label": "商社・卸売",
        "query": "総合商社 OR 卸売 OR 三菱商事 OR 伊藤忠 OR 三井物産 OR 商社業界",
    },
    "1630": {
        "label": "小売",
        "query": "小売業界 OR 流通 OR 百貨店 OR コンビニ OR ファーストリテイリング OR イオン",
    },
    "1631": {
        "label": "銀行",
        "query": "銀行 OR メガバンク OR 三菱UFJ OR 三井住友銀行 OR みずほ OR 地方銀行",
    },
    "1632": {
        "label": "金融（除く銀行）",
        "query": "証券 OR 保険 OR 金融サービス OR 野村證券 OR 東京海上 OR ノンバンク",
    },
    "1633": {
        "label": "不動産",
        "query": "不動産業界 OR 不動産開発 OR 三井不動産 OR 三菱地所 OR REIT OR マンション市況",
    },
}

# 業種あたりの要約上限（コスト天井）。17 業種 × 本数ぶん LLM 要約が走るため一般ニュースより低め。
SECTOR_NEWS_MAX_PER_SECTOR: int = 3

# 取得 lookback 日数（セクター層。市況文脈が目的なので短め）。
SECTOR_NEWS_LOOKBACK_DAYS: int = 3
