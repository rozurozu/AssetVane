"""業種コードの二体系（分類 S17 / 銘柄 ETF ティッカー）の SSOT 集約（ADR-053・docs/decisions.md）。

設計の真実: docs/decisions.md ADR-053。業種に二つの体系が混在する:

  - classification（分類）= J-Quants S17 業種コード "1".."17"（ETF/REIT は "99"）。
    `stocks.sector17_code`・`news.sector17_code` はこちら。ニュースのセクタータグも
    分類に寄せ、stocks と直接一致させる。
  - instrument（銘柄）= TOPIX-17 業種 ETF のティッカー "1617".."1633"。lead_lag は
    実在 ETF の株価を引くのでこちら（`quant/lead_lag.py` の JP_SYMBOLS 等は不変）。

両空間の対応（ETF = 1616 + S17・全 17 業種で成立確認済み）と和名ラベルをここに集約する。
明示マッピングで持ち、`1616 + N` の算術マジックには依存しない（並び順前提の暗黙ルールを残さない）。

IO 無し・副作用無し・標準ライブラリのみ依存（app.* を import しない＝序列外の中立参照モジュール）。
"""

from __future__ import annotations

# ── S17 業種コード "1".."17" → 和名ラベル ────────────────────────────────────
# 旧 services/lead_lag.py の JP_SECTOR_LABELS（ETF キー "1617".."1633"）の値を S17 キーへ移植。
SECTOR17_LABELS_JA: dict[str, str] = {
    "1": "食品",
    "2": "エネルギー資源",
    "3": "建設・資材",
    "4": "素材・化学",
    "5": "医薬品",
    "6": "自動車・輸送機",
    "7": "鉄鋼・非鉄",
    "8": "機械",
    "9": "電機・精密",
    "10": "情報通信・サービスその他",
    "11": "電力・ガス",
    "12": "運輸・物流",
    "13": "商社・卸売",
    "14": "小売",
    "15": "銀行",
    "16": "金融（除く銀行）",
    "17": "不動産",
}

# ── S17 業種コード "1".."17" ⇄ TOPIX-17 業種 ETF ティッカー "1617".."1633" ──────
# 明示マッピング（ETF = 1616 + S17 だが算術式に依存せず並べる）。
S17_TO_TOPIX17_ETF: dict[str, str] = {
    "1": "1617",
    "2": "1618",
    "3": "1619",
    "4": "1620",
    "5": "1621",
    "6": "1622",
    "7": "1623",
    "8": "1624",
    "9": "1625",
    "10": "1626",
    "11": "1627",
    "12": "1628",
    "13": "1629",
    "14": "1630",
    "15": "1631",
    "16": "1632",
    "17": "1633",
}

# 逆引き（ETF ティッカー → S17）。
TOPIX17_ETF_TO_S17: dict[str, str] = {etf: s17 for s17, etf in S17_TO_TOPIX17_ETF.items()}


def normalize_sector17(value: object) -> str | None:
    """任意入力を S17 業種コード "1".."17" に正規化する（不正・"99"・None は None）。

    int / 前後空白を吸収し（6 / " 6 " → "6"）、"1".."17" の範囲のみ通す。
    "99"（ETF/REIT）・None・範囲外・非数値は None を返す（呼び出し側でセクター層を空にする）。
    """
    if value is None:
        return None
    code = str(value).strip()
    if not code:
        return None
    # 前置ゼロ等の表記揺れを数値経由で吸収しつつ、"1".."17" のみ通す。
    try:
        n = int(code)
    except ValueError:
        return None
    if 1 <= n <= 17:
        return str(n)
    return None


def sector17_to_topix17_etf(code: object) -> str | None:
    """S17 業種コード → TOPIX-17 業種 ETF ティッカー（不明・"99" は None）。"""
    s17 = normalize_sector17(code)
    if s17 is None:
        return None
    return S17_TO_TOPIX17_ETF.get(s17)


def topix17_etf_to_sector17(ticker: object) -> str | None:
    """TOPIX-17 業種 ETF ティッカー "1617".."1633" → S17 業種コード（不明は None）。"""
    if ticker is None:
        return None
    return TOPIX17_ETF_TO_S17.get(str(ticker).strip())


def sector17_label(code: object) -> str | None:
    """S17 業種コード → 和名ラベル（"99"・不明・不正は None）。"""
    s17 = normalize_sector17(code)
    if s17 is None:
        return None
    return SECTOR17_LABELS_JA.get(s17)
