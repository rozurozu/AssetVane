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

本体は tag_jp_themes と共通化して _theme_tagging.run_theme_tagging に寄せた（約8割同一・
tasks/review-2026-06-12.md §3）。US は語彙ドリフト追従で常に LLM 再タグ
＝use_bump_optimization=False。
"""

from __future__ import annotations

from app.advisor.theme_tagger import tag_stock_themes
from app.batch.jobs._theme_tagging import run_theme_tagging
from app.batch.runner import JobResult
from app.config import settings
from app.db import repo


def run() -> JobResult:
    """US 銘柄を優先順に夜天井までタグ付けし、末尾で時間窓 prune する（ADR-050/033/018）。

    種テーマ投入（冪等）→ 選定 → 銘柄ループ（1 銘柄の失敗は握って継続）→ prune。
    失敗が 1 件でもあれば ok=False（runner が Discord に通知）。本体は run_theme_tagging
    に委譲し、tag_stock_themes は本モジュールから渡す（テストの monkeypatch が効く＝§3）。
    """
    return run_theme_tagging(
        market="US",
        cap=settings.theme_tagging_nightly_max,
        list_codes=repo.list_us_codes_for_theme_tagging,
        tagger=tag_stock_themes,
        use_bump_optimization=False,
    )
