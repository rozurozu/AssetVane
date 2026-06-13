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

本体は tag_us_themes と共通化して _theme_tagging.run_theme_tagging に寄せた（約8割同一・
tasks/review-2026-06-12.md §3）。JP 固有の bump 安価パスは use_bump_optimization=True で有効化する。
"""

from __future__ import annotations

from app.advisor.theme_tagger import tag_stock_themes
from app.batch.jobs._theme_tagging import run_theme_tagging
from app.batch.runner import JobResult
from app.config import settings
from app.db import repo


def run() -> JobResult:
    """JP 調査済み銘柄を優先順に夜天井までタグ付けし、末尾で時間窓 prune する（ADR-050/033/018）。

    種テーマ投入（冪等）→ 選定 → 銘柄ループ（説明未変化は bump・1 銘柄の失敗は握って継続）→ prune。
    失敗が 1 件でもあれば ok=False（runner が Discord に通知）。本体は run_theme_tagging に委譲し、
    tag_stock_themes は本モジュールから渡す（テストの monkeypatch が効く＝§3）。bump 安価パスは
    use_bump_optimization=True で有効（段階B 固有）。
    """
    return run_theme_tagging(
        market="JP",
        cap=settings.theme_tagging_jp_nightly_max,
        list_codes=repo.list_jp_codes_for_theme_tagging,
        tagger=tag_stock_themes,
        use_bump_optimization=True,
    )
