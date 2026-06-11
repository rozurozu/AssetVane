"""EdinetAdapter の正規化・抽出の検証（ADR-056・ADR-010・testing-strategy）。

ネットに出ない＝書類一覧の正規化はサンプル dict、事業の内容抽出はメモリ上の CSV ZIP
（UTF-16・タブ区切り）で検証する。HTTP（httpx）は呼ばず純関数ヘルパを直接叩く。
"""

from __future__ import annotations

import io
import zipfile

from app.adapters.edinet import (
    _extract_business_text,
    _normalize_doc,
    _strip_html,
)


def _make_csv_zip(
    rows: list[list[str]], *, name: str = "jpcrp030000-asr-001_E00000-000.csv"
) -> bytes:
    """要素ID 行を含む CSV（UTF-16・タブ区切り）を ZIP に詰めて返す（EDINET type=5 模擬）。"""
    buf = io.StringIO()
    for row in rows:
        buf.write("\t".join(row) + "\r\n")
    data = buf.getvalue().encode("utf-16")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr(name, data)
    return out.getvalue()


def test_normalize_doc_maps_external_keys() -> None:
    """書類一覧の外部キー名（docID/secCode/...）を内部名へ正規化する（ADR-010 の境界）。"""
    raw = {
        "docID": "S100ABCD",
        "secCode": "72030",
        "docTypeCode": "120",
        "filerName": "トヨタ自動車株式会社",
        "periodEnd": "2025-03-31",
        "submitDateTime": "2025-06-25 09:00",
        "csvFlag": "1",
        "extraneous": "無視される",
    }
    doc = _normalize_doc(raw)
    assert doc == {
        "doc_id": "S100ABCD",
        "sec_code": "72030",
        "doc_type_code": "120",
        "filer_name": "トヨタ自動車株式会社",
        "period_end": "2025-03-31",
        "submit_datetime": "2025-06-25 09:00",
        "csv_flag": "1",
    }


def test_strip_html_removes_tags_and_unescapes() -> None:
    """HTML タグ除去＋エンティティ復元＋空白畳み込み（要約前の下ごしらえ）。"""
    raw = "<p>当社&amp;グループは、<b>半導体</b>製造装置を製造する。</p><table><tr><td>X</td></tr></table>"  # noqa: E501 — HTML サンプルは 1 行で読みやすさ優先
    out = _strip_html(raw)
    assert "<" not in out and ">" not in out
    assert "&amp;" not in out and "当社&グループ" in out
    # タグ境界は改行に置換される（タガーは evidence 照合で空白正規化するため grounding は維持）。
    assert "半導体" in out and "製造装置を製造する" in out


def test_extract_business_text_finds_element_and_strips_html() -> None:
    """ZIP 内の CSV から DescriptionOfBusinessTextBlock 行の値を取り HTML を strip する。"""
    rows = [
        [
            "要素ID",
            "項目名",
            "コンテキストID",
            "相対年度",
            "連結・個別",
            "期間・時点",
            "ユニットID",
            "単位",
            "値",
        ],
        [
            "jpcrp_cor:CompanyNameCoverPage",
            "会社名",
            "FilingDateInstant",
            "",
            "",
            "",
            "",
            "",
            "テスト株式会社",
        ],
        [
            "jpcrp_cor:DescriptionOfBusinessTextBlock",
            "事業の内容",
            "CurrentYearDuration",
            "当期",
            "連結",
            "期間",
            "",
            "",
            "<p>当社グループは産業用ロボットとFA機器を製造・販売する。</p>",
        ],
    ]
    zip_bytes = _make_csv_zip(rows)
    text = _extract_business_text(zip_bytes)
    assert "産業用ロボットとFA機器を製造・販売する" in text
    assert "<p>" not in text


def test_extract_business_text_returns_empty_when_absent() -> None:
    """事業の内容の要素が無い書類（型違い等）は空文字を返す（呼び出し側で None 扱い）。"""
    rows = [
        ["要素ID", "項目名", "値"],
        ["jpcrp_cor:CompanyNameCoverPage", "会社名", "テスト株式会社"],
    ]
    assert _extract_business_text(_make_csv_zip(rows)) == ""
