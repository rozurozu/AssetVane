"""米株業種（Yahoo `.info.sector` ＝ GICS 相当 11 分類）の和訳ラベル＋正規化（ADR-055）。

設計の真実: docs/decisions.md ADR-055（Phase 7(B-1) 米株業種は「GICS 相当」と割り切る）。
yfinance `.info.sector` が返す英語ラベル（"Technology" / "Financial Services" / "Healthcare"
等）は厳密な GICS 11 セクター名とは表記が一部異なる（Yahoo 独自の呼称）。厳密 GICS は追わず、
Yahoo が返す英語ラベルをそのまま `us_stocks.gics_sector` に文字列保持し、表示用の和訳と正規化だけ
ここに集約する（sector_codes.py 同型＝SSOT）。screen の業種フィルタ・GICS 内 window ランクは
この英語ラベルを partition キーに使う（後続ウェーブ）。

Yahoo 表記の根拠: yfinance の sectorKey/sector マップ（11 分類）に準拠。GICS 正式名との差は
"Financial Services"（GICS は "Financials"）・"Consumer Cyclical"（GICS は "Consumer
Discretionary"）・"Consumer Defensive"（GICS は "Consumer Staples"）・"Basic Materials"
（GICS は "Materials"）・"Communication Services"（GICS と同）等。実値は実機の `.info` で要再確認
（憶測を避ける＝報告に明記）。エイリアスで正式 GICS 名も拾えるようにし表記揺れを吸収する。

IO 無し・副作用無し・標準ライブラリのみ依存（app.* を import しない＝序列外の中立参照モジュール）。
"""

from __future__ import annotations

# ── Yahoo `.info.sector` の英語ラベル（GICS 相当 11 分類）→ 和名ラベル ──────────────
# キーは Yahoo が返す表記（canonical）。値は画面/通知の表示和名。
GICS_SECTOR_LABELS_JA: dict[str, str] = {
    "Technology": "情報技術",
    "Financial Services": "金融",
    "Healthcare": "ヘルスケア",
    "Consumer Cyclical": "一般消費財",
    "Consumer Defensive": "生活必需品",
    "Industrials": "資本財・サービス",
    "Communication Services": "通信サービス",
    "Energy": "エネルギー",
    "Basic Materials": "素材",
    "Utilities": "公益事業",
    "Real Estate": "不動産",
}

# ── 表記揺れ吸収用エイリアス（正式 GICS 名 等 → Yahoo canonical ラベル） ────────────
# 厳密 GICS 名や旧称・別表記を Yahoo canonical に寄せる。小文字化して突合する（大小無視）。
_SECTOR_ALIASES: dict[str, str] = {
    # 正式 GICS 名 → Yahoo canonical
    "information technology": "Technology",
    "financials": "Financial Services",
    "consumer discretionary": "Consumer Cyclical",
    "consumer staples": "Consumer Defensive",
    "materials": "Basic Materials",
    "health care": "Healthcare",
    # Yahoo canonical 自身（恒等・大小/前後空白の揺れを通す）
    "technology": "Technology",
    "financial services": "Financial Services",
    "healthcare": "Healthcare",
    "consumer cyclical": "Consumer Cyclical",
    "consumer defensive": "Consumer Defensive",
    "industrials": "Industrials",
    "communication services": "Communication Services",
    "energy": "Energy",
    "basic materials": "Basic Materials",
    "utilities": "Utilities",
    "real estate": "Real Estate",
}


def normalize_gics_sector(value: object) -> str | None:
    """任意入力を Yahoo canonical の業種ラベル（11 分類）に正規化する（不明・None は None）。

    前後空白・大小・正式 GICS 名/別表記を吸収して canonical（"Technology" 等）に寄せる。
    11 分類のいずれにも該当しなければ None を返す（呼び出し側で業種未分類として扱う）。
    """
    if value is None:
        return None
    label = str(value).strip()
    if not label:
        return None
    # 既に canonical ならそのまま通す。
    if label in GICS_SECTOR_LABELS_JA:
        return label
    return _SECTOR_ALIASES.get(label.lower())


def gics_sector_label(value: object) -> str | None:
    """Yahoo canonical の業種ラベル → 和名ラベル（不明・不正は None）。"""
    canonical = normalize_gics_sector(value)
    if canonical is None:
        return None
    return GICS_SECTOR_LABELS_JA.get(canonical)
