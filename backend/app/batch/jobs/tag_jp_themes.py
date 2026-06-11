"""夜間バッチ: JP 調査済み銘柄テーマの grounded タグ付けジョブ（ADR-050 改訂・段階B）。

設計の真実: docs/decisions.md ADR-050 改訂（全ユニバース grounded 事前タグ・段階B＝JP 調査済み
オーバーレイ）・ADR-033（夜あたり天井のローテ cadence）・ADR-018（部分失敗の握り）。

tag_us_themes（段階A・US）と完全対称の独立ジョブ。違いは信号源の出所だけ＝US は
fetch_us_fundamentals の longBusinessSummary、JP は investigate_stock がドシエ要約
（summary_md）を company_descriptions(market='JP', source='dossier') に焼いたもの。

NIGHTLY_JOBS では **investigate_dossier.run の直後・embed_themes.run の前**に置く。理由＝JP の
信号源を書くのは後段の investigate_dossier（run_advisor の後）なので、その夜に投資調査された
銘柄の説明変化を同じ夜のタグ付けまで反映するには investigate_dossier より後に走らせる必要がある
（US は前段 fetch_us_fundamentals 由来なので run_advisor の前に置けた＝この非対称は信号源の
生成元の違いから来る必然）。後続 embed_themes が当夜の新テーマ語彙を拾う順序も保たれる。

段取り（tag_us_themes と同一）:
  1. 種テーマ投入（SEED_THEMES を冪等に目録へ・毎晩呼んでよい）。
  2. 選定: list_jp_codes_for_theme_tagging が「未タグ → 説明変化 → 古い順ローテ」の優先で
     settings.theme_tagging_jp_nightly_max 件まで返す（ETF 除外は SQL 側）。
  3. 銘柄ループ: 説明テキストが前回タグ以降に未変化（既タグ済み かつ fetched_at <=
     last_fetched_date）なら LLM を呼ばず bump_stock_themes_last_seen で last_seen_at だけ bump
     する（毎晩 LLM 再タグのコスト削減・段階B 固有。小さい調査済み母集団は毎晩全件ローテ選定される
     ため。語彙ドリフト追従を全件 LLM 再タグで担う段階A US＝tag_us_themes とは挙動が割れるが意図）。
     ①未タグ・②変化のみ asyncio.run(tag_stock_themes(conn, market="JP", code)) で再分類。成功で
     fetch_meta['jp_themes:<code>'] を ISO datetime（時刻まで）で前進。1 銘柄の失敗は握って後続を
     止めず mark_fetch_attempt_failed を記録（ADR-018）。
  4. 末尾で時間窓 prune: theme_prune_days より古い last_seen_at の JP タグを枯らす（prune を
     タガーと同居させる理由は tag_us_themes のモジュール docstring・ADR-050 の UPSERT＋bump と対）。

選定 0 件（company_descriptions の JP 行が空＝まだ誰も investigate していない）は ok=True・rows=0 で
静かに返す（prune もしない＝タガーが何も再確認していない夜に枯らさない）。
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

_SOURCE_PREFIX = "jp_themes"  # per-code fetch_meta source キー接頭辞（us_themes 同型）


def _source_key(code: str) -> str:
    """銘柄ごとの fetch_meta source キー（例: 'jp_themes:72030'・repo のキー慣行）。"""
    return f"{_SOURCE_PREFIX}:{code}"


def _now_iso() -> str:
    """現在時刻を ISO8601（UTC・時刻まで）で返す（fetch_meta カーソルの前提形式・ADR-050）。"""
    return datetime.now(UTC).isoformat()


def run() -> JobResult:
    """JP 調査済み銘柄を優先順に夜天井までタグ付けし、末尾で時間窓 prune する（ADR-050/033/018）。

    種テーマ投入（冪等）→ 選定 → 銘柄ループ（1 銘柄の失敗は握って継続）→ prune。
    失敗が 1 件でもあれば ok=False（runner が Discord に通知）。
    """
    cap = settings.theme_tagging_jp_nightly_max
    try:
        # 種テーマは毎晩冪等に仕込む（tag_us_themes が無効でも種が入る安全側・冪等で二重なし）。
        n_seeded = repo.insert_themes_if_absent(list(SEED_THEMES), _now_iso())
        with get_engine().connect() as conn:
            codes = repo.list_jp_codes_for_theme_tagging(conn, cap)
    except Exception as exc:  # noqa: BLE001 — ジョブ境界で握り runner に返す
        logger.exception("tag_jp_themes: 種投入/巡回対象の選定に失敗")
        return JobResult(name="tag_jp_themes", ok=False, rows=0, detail=f"対象選定失敗: {exc}")

    if not codes:
        return JobResult(
            name="tag_jp_themes",
            ok=True,
            rows=0,
            detail="company_descriptions(JP) が空＝巡回対象なし",
        )

    n_ok = 0
    n_new_themes = 0
    n_bumped = 0
    failures: list[str] = []
    for code in codes:
        try:
            with get_engine().connect() as conn:
                meta = repo.get_fetch_meta(conn, _source_key(code))
                desc = repo.get_company_description(conn, "JP", code)
            last_tagged = (meta or {}).get("last_fetched_date")
            desc_fetched = (desc or {}).get("fetched_at")
            # 説明が前回タグ以降に未変化（既タグ済み かつ fetched_at <= last_fetched_date）なら
            # LLM を呼ばず既存タグの last_seen_at だけ bump する安価パス（毎晩 LLM 再タグのコスト
            # 削減・段階B 固有＝小さい母集団は毎晩全件ローテ選定されるため。段階A US は語彙ドリフト
            # 追従で全件 LLM 再タグを維持＝tag_us_themes は無改変）。①未タグ・②変化は下の LLM へ。
            if last_tagged and desc_fetched and desc_fetched <= last_tagged:
                repo.bump_stock_themes_last_seen(market="JP", code=code, last_seen_at=_now_iso())
                repo.upsert_fetch_meta(_source_key(code), _now_iso())
                n_ok += 1
                n_bumped += 1
                continue
            # 読み取り専用 conn を渡す（書き込みは repo W1 関数が自前 begin・theme_tagger 規律）。
            with get_engine().connect() as conn:
                result = asyncio.run(tag_stock_themes(conn, market="JP", code=code))
            # skip（説明テキスト空）も成功扱いでカーソルを前進させる（同銘柄の再選定ループ防止。
            # テキストが変化すれば fetched_at 比較で再び優先される＝ADR-050 の差分定義）。
            repo.upsert_fetch_meta(_source_key(code), _now_iso())
            n_ok += 1
            n_new_themes += int(result.get("n_new_themes", 0) or 0)
        except Exception as exc:  # noqa: BLE001 — 銘柄境界で握り後続銘柄を止めない（ADR-018）
            logger.exception("tag_jp_themes: 銘柄 %s のタグ付けに失敗", code)
            repo.mark_fetch_attempt_failed(_source_key(code))
            failures.append(f"{code}: {exc}")

    # 末尾の時間窓 prune（market='JP' 限定＝US タグを誤って枯らさない安全弁・ADR-050）。
    pruned = 0
    try:
        cutoff = (datetime.now(UTC) - timedelta(days=settings.theme_prune_days)).isoformat()
        pruned = repo.prune_stale_stock_themes(market="JP", cutoff_iso=cutoff)
    except Exception as exc:  # noqa: BLE001 — prune 失敗もジョブ内で握り detail に集約（ADR-018）
        logger.exception("tag_jp_themes: 時間窓 prune に失敗")
        failures.append(f"prune: {exc}")

    detail = (
        f"タグ付け {len(codes)} 件中 成功 {n_ok}・失敗 {len(failures)}（夜天井 {cap}）"
        f"・新テーマ {n_new_themes} 件・bump のみ {n_bumped} 件・prune {pruned} 行"
    )
    if n_seeded:
        detail += f"・種テーマ投入 {n_seeded} 件"
    if failures:
        detail += " / 失敗詳細: " + "; ".join(failures[:5])
    return JobResult(name="tag_jp_themes", ok=not failures, rows=n_ok, detail=detail)
