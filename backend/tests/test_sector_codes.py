"""app.reference.sector_codes（業種コード二体系の SSOT）の単体テスト（ADR-053・ADR-044）。

設計の真実: docs/decisions.md ADR-053。業種に二体系が混在する＝classification(分類)=J-Quants
S17 業種コード "1".."17"（ETF/REIT は "99"）／instrument(銘柄)=TOPIX-17 業種 ETF ティッカー
"1617".."1633"。reference はその対応・和名ラベル・正規化を持つ純参照モジュール。

担保すること:
- S17 ⇄ ETF ティッカーの往復が全 17 業種で無矛盾（"6"↔"1622" 等）。
- normalize_sector17 の正規化（int/空白吸収・"99"/None/不正→None）。
- sector17_label の和名解決（"6"→自動車・輸送機・"99"→None）。
- 定義域の不変条件: SECTOR17_LABELS_JA / SECTOR_NEWS_QUERIES のキー集合 == {"1".."17"}。
- 整合不変条件: services.lead_lag.JP_SECTOR_LABELS が reference 由来で len==17・["1622"]=自動車。

IO 無し・DB 非依存の純関数なので temp_db フィクスチャは不要（ネットにも出ない＝testing-strategy）。
"""

from __future__ import annotations

from app.reference.sector_codes import (
    S17_TO_TOPIX17_ETF,
    SECTOR17_LABELS_JA,
    TOPIX17_ETF_TO_S17,
    normalize_sector17,
    sector17_label,
    sector17_to_topix17_etf,
    topix17_etf_to_sector17,
)

# S17 業種コードの定義域（"1".."17"）。
_S17_DOMAIN = {str(n) for n in range(1, 18)}


def test_s17_to_etf_roundtrip_known_pair() -> None:
    """既知ペア（自動車・輸送機 S17 "6" ⇄ ETF "1622"）の往復が成立する。"""
    assert sector17_to_topix17_etf("6") == "1622"
    assert topix17_etf_to_sector17("1622") == "6"


def test_s17_etf_roundtrip_all_17_sectors() -> None:
    """全 17 業種で S17 → ETF → S17 の往復が無矛盾（明示マッピングの整合）。"""
    assert set(S17_TO_TOPIX17_ETF) == _S17_DOMAIN
    assert set(TOPIX17_ETF_TO_S17) == set(S17_TO_TOPIX17_ETF.values())
    for s17 in _S17_DOMAIN:
        etf = sector17_to_topix17_etf(s17)
        assert etf is not None
        assert topix17_etf_to_sector17(etf) == s17
    # 逆向きも全 ETF で成立。
    for etf in S17_TO_TOPIX17_ETF.values():
        s17 = topix17_etf_to_sector17(etf)
        assert s17 is not None
        assert sector17_to_topix17_etf(s17) == etf


def test_normalize_sector17_canonicalizes_valid() -> None:
    """str/int/前後空白を S17 正規形 "1".."17" に正規化する。"""
    assert normalize_sector17("6") == "6"
    assert normalize_sector17(6) == "6"
    assert normalize_sector17(" 6 ") == "6"
    assert normalize_sector17("06") == "6"  # 前置ゼロも数値経由で吸収
    assert normalize_sector17("17") == "17"
    assert normalize_sector17(1) == "1"


def test_normalize_sector17_rejects_invalid() -> None:
    """ "99"(ETF/REIT)・None・空・範囲外・非数値は None（呼び側がセクター層を空にする）。"""
    assert normalize_sector17("99") is None
    assert normalize_sector17(None) is None
    assert normalize_sector17("") is None
    assert normalize_sector17("   ") is None
    assert normalize_sector17("x") is None
    assert normalize_sector17("0") is None
    assert normalize_sector17("18") is None
    assert normalize_sector17("1622") is None  # ETF ティッカーは分類コードではない


def test_sector17_label_resolves_japanese_name() -> None:
    """S17 業種コード → 和名（"6"→自動車・輸送機・"99"/不明は None）。"""
    assert sector17_label("6") == "自動車・輸送機"
    assert sector17_label("1") == "食品"
    assert sector17_label("17") == "不動産"
    assert sector17_label(6) == "自動車・輸送機"  # int も正規化される
    assert sector17_label("99") is None
    assert sector17_label(None) is None
    assert sector17_label("x") is None


def test_sector17_labels_ja_domain_is_1_to_17() -> None:
    """SECTOR17_LABELS_JA のキー集合は {"1".."17"}（17 業種ちょうど）。"""
    assert set(SECTOR17_LABELS_JA) == _S17_DOMAIN
    assert all(SECTOR17_LABELS_JA[s17] for s17 in _S17_DOMAIN)  # 全業種に和名がある


def test_sector_news_queries_domain_is_1_to_17() -> None:
    """SECTOR_NEWS_QUERIES のキー集合も {"1".."17"} で reference と整合する（ADR-053）。"""
    from app.adapters.general_news_config import SECTOR_NEWS_QUERIES

    assert set(SECTOR_NEWS_QUERIES) == _S17_DOMAIN
    # 値は query 文字列のみ（label を持たない＝ADR-053 で簡素化）。
    assert all(isinstance(q, str) and q for q in SECTOR_NEWS_QUERIES.values())


def test_lead_lag_sector_labels_derived_from_reference() -> None:
    """services.lead_lag.JP_SECTOR_LABELS が reference 由来で len==17・ETF キーで和名を持つ。"""
    from app.services.lead_lag import JP_SECTOR_LABELS

    assert len(JP_SECTOR_LABELS) == 17
    # ETF ティッカー空間（instrument）でキーを持ち、reference の S17 和名を引き継ぐ。
    assert JP_SECTOR_LABELS["1622"] == "自動車・輸送機"
    # 全 ETF キーが S17 → 和名と一致（reference からの導出が無矛盾）。
    for s17, etf in S17_TO_TOPIX17_ETF.items():
        assert JP_SECTOR_LABELS[etf] == SECTOR17_LABELS_JA[s17]
