"""夜間バッチ: US 銘柄テーマの grounded タグ付けジョブ（ADR-050 改訂・段階A）。

設計の真実: docs/decisions.md ADR-050 改訂（全ユニバース grounded 事前タグ）・ADR-033
（夜あたり天井のローテ cadence）・ADR-018（部分失敗の握り）。

NIGHTLY_JOBS では calc_us_valuation.run の直後・run_advisor.run の前に置く
（fetch_us_fundamentals が company_descriptions を更新した直後にタグを焼き、夜の分析AI が
当夜のタグを Tool で読めるようにする）。

段取り:
  1. **種テーマ投入**: SEED_THEMES を insert_themes_if_absent で目録へ仕込む（冪等・
     on_conflict_do_nothing なので**毎晩呼んでよい**＝reference/theme_seeds.py に種を足せば
     次の夜に自動で目録へ反映される。コールドスタートと種の追従を 1 口で兼ねる）。
  2. **選定**: list_us_codes_for_theme_tagging が「未タグ → 説明テキスト変化 → 古い順ローテ」
     の優先で settings.theme_tagging_nightly_max 件まで返す（ADR-033 流用・ETF 除外は SQL 側）。
  3. **銘柄ループ**: 銘柄ごとに読み取り conn を開き asyncio.run(tag_stock_themes(...)) で
     async タガーを駆動する（investigate_dossier の流儀。書き込みは repo の W1 関数が自前
     begin で閉じるため、ここで書き込みトランザクションは開かない＝theme_tagger の接続規律）。
     成功で fetch_meta['us_themes:<code>'] を **ISO datetime（時刻まで）**で前進させる
     （list_us_codes_for_theme_tagging の「説明変化」判定が fetched_at との文字列比較で成立
     する前提）。1 銘柄の失敗は握って後続を止めず mark_fetch_attempt_failed を記録する
     （ADR-018・1 件でも失敗なら ok=False で runner が通知）。
  4. **末尾で時間窓 prune**: theme_prune_days より古い last_seen_at の US タグを枯らす。
     **prune をタガーと同居させる理由**＝独立ジョブにするとタガー停止中（障害・無効化）にも
     prune だけ走り続け、再確認の機会が無いまま全タグが枯れる事故が起きる。同居なら
     「タガーが回った夜にだけ枯らす」が構造で保証される（ADR-050 の UPSERT＋bump と対）。

選定 0 件（company_descriptions の US 行が空＝fetch_us_fundamentals 未稼働）は ok=True・
rows=0 で静かに返す（prune もしない＝タガーが何も再確認していない夜に枯らさない）。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from app.advisor.theme_tagger import tag_stock_themes
from app.batch.runner import JobResult
from app.config import settings
from app.db import repo
from app.db.engine import get_engine
from app.reference.theme_seeds import SEED_THEMES

logger = logging.getLogger(__name__)

_SOURCE_PREFIX = "us_themes"  # per-symbol fetch_meta source キー接頭辞（us_fundamentals 同型）


def _source_key(code: str) -> str:
    """銘柄ごとの fetch_meta source キー（例: 'us_themes:AAPL'・repo のキー慣行）。"""
    return f"{_SOURCE_PREFIX}:{code}"


def _now_iso() -> str:
    """現在時刻を ISO8601（UTC・時刻まで）で返す（fetch_meta カーソルの前提形式・ADR-050）。"""
    return datetime.now(UTC).isoformat()


def run() -> JobResult:
    """US 銘柄を優先順に夜天井までタグ付けし、末尾で時間窓 prune する（ADR-050/033/018）。

    種テーマ投入（冪等）→ 選定 → 銘柄ループ（1 銘柄の失敗は握って継続）→ prune。
    失敗が 1 件でもあれば ok=False（runner が Discord に通知）。
    """
    cap = settings.theme_tagging_nightly_max
    try:
        # 種テーマは毎晩冪等に仕込む（種の追加が次の夜に自動反映・モジュール docstring）。
        n_seeded = repo.insert_themes_if_absent(list(SEED_THEMES), _now_iso())
        with get_engine().connect() as conn:
            codes = repo.list_us_codes_for_theme_tagging(conn, cap)
    except Exception as exc:  # noqa: BLE001 — ジョブ境界で握り runner に返す
        logger.exception("tag_us_themes: 種投入/巡回対象の選定に失敗")
        return JobResult(name="tag_us_themes", ok=False, rows=0, detail=f"対象選定失敗: {exc}")

    if not codes:
        return JobResult(
            name="tag_us_themes",
            ok=True,
            rows=0,
            detail="company_descriptions(US) が空＝巡回対象なし",
        )

    n_ok = 0
    n_new_themes = 0
    failures: list[str] = []
    for code in codes:
        try:
            # 読み取り専用 conn を渡す（書き込みは repo W1 関数が自前 begin・theme_tagger 規律）。
            with get_engine().connect() as conn:
                result = asyncio.run(tag_stock_themes(conn, market="US", code=code))
            # skip（説明テキスト空）も成功扱いでカーソルを前進させる（同銘柄の再選定ループ防止。
            # テキストが変化すれば fetched_at 比較で再び優先される＝ADR-050 の差分定義）。
            repo.upsert_fetch_meta(_source_key(code), _now_iso())
            n_ok += 1
            n_new_themes += int(result.get("n_new_themes", 0) or 0)
        except Exception as exc:  # noqa: BLE001 — 銘柄境界で握り後続銘柄を止めない（ADR-018）
            logger.exception("tag_us_themes: 銘柄 %s のタグ付けに失敗", code)
            repo.mark_fetch_attempt_failed(_source_key(code))
            failures.append(f"{code}: {exc}")

    # 末尾の時間窓 prune（タガーと同居させる理由はモジュール docstring・ADR-050）。
    pruned = 0
    try:
        cutoff = (datetime.now(UTC) - timedelta(days=settings.theme_prune_days)).isoformat()
        pruned = repo.prune_stale_stock_themes(market="US", cutoff_iso=cutoff)
    except Exception as exc:  # noqa: BLE001 — prune 失敗もジョブ内で握り detail に集約（ADR-018）
        logger.exception("tag_us_themes: 時間窓 prune に失敗")
        failures.append(f"prune: {exc}")

    detail = (
        f"タグ付け {len(codes)} 件中 成功 {n_ok}・失敗 {len(failures)}（夜天井 {cap}）"
        f"・新テーマ {n_new_themes} 件・prune {pruned} 行"
    )
    if n_seeded:
        detail += f"・種テーマ投入 {n_seeded} 件"
    if failures:
        detail += " / 失敗詳細: " + "; ".join(failures[:5])
    return JobResult(name="tag_us_themes", ok=not failures, rows=n_ok, detail=detail)
