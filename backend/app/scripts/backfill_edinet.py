"""テーマタグ段階 C（EDINET → JP 全ユニバース）の事業説明バックフィル script（ADR-056・ADR-050）。

    uv run python -m app.scripts.backfill_edinet                 # 既定窓（約15ヶ月）を一括クロール
    uv run python -m app.scripts.backfill_edinet --window-days 30  # 窓を縮めて試走（コスト見積り）
    uv run python -m app.scripts.backfill_edinet --limit 50        # 要約件数の上限（試走用 cap）
    uv run python -m app.scripts.backfill_edinet --from 2025-06-01 # 開始提出日を明示（窓より優先）

提出日クロール型（grill 2026-06-11）。EDINET 書類一覧 API は提出日でしか引けないため、JP 全
ユニバースの最新有報を 1 本ずつ拾うには **trailing 約15ヶ月**（年次サイクル 1 周＋提出ラグ 3ヶ月）の
提出日を舐める（3月決算以外＝12月/9月決算等も拾える）。夜間差分（fetch_edinet_descriptions.run）と
**同じ crawl core・同じ fetch_meta('edinet:crawl') カーソル**を共有し、起点だけ変える。

中断再開可: カーソルが**完了した最後の提出日**を持つので、Ctrl-C で止めても再実行すれば続きから走る
（要約済み銘柄は事前 skip で撃ち直さない＝冪等）。初回は LLM コストが大きい（要約 1 発/銘柄＋後段の
tag_jp_themes でタガー 1 発/銘柄）。`--limit`/`--window-days` で試走してコストを見積もってから全量を
流すこと。夜間バッチと同じ EDINET/LLM 資源を使うため、**夜間バッチ時間帯を避けて実行する**こと。

運用順: 本 script（説明を company_descriptions(JP, source='edinet') に埋める）→ 既存
`app.scripts.backfill_themes` 相当の JP 一括タグ付け（夜間 tag_jp_themes が source 不問で拾うので、
本 script 後は夜間だけでも徐々にタグが付くが、一括で付けたいときは tag_jp_themes を回す）。
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

from app.batch.jobs.fetch_edinet_descriptions import _resolve_start, _today_jst, crawl
from app.config import settings
from app.db.engine import init_db


def main(argv: list[str] | None = None) -> int:
    """argv を解釈して EDINET 事業説明バックフィルを流す（テストから引数 list で呼べる）。"""
    parser = argparse.ArgumentParser(
        prog="backfill_edinet",
        description="テーマタグ段階 C（EDINET → JP 全ユニバース）の事業説明バックフィル（ADR-056）",
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=settings.edinet_backfill_window_days,
        help="遡及する提出日窓の日数（既定 約15ヶ月。カーソルがあれば続きから＝窓は初回のみ効く）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="この実行で要約まで進める件数の上限（試走・コスト見積もり用。未指定＝無制限）",
    )
    parser.add_argument(
        "--from",
        dest="from_date",
        type=str,
        default=None,
        help="開始提出日 YYYY-MM-DD を明示（カーソル/窓より優先・特定日からの再走用）",
    )
    args = parser.parse_args(argv)

    if not settings.edinet_api_key:
        print(
            "✖ EDINET_API_KEY が未設定です（backend の .env に設定してください）", file=sys.stderr
        )
        return 2

    init_db()

    today = _today_jst()
    if args.from_date:
        start = date.fromisoformat(args.from_date)
    else:
        # カーソルがあれば続き（cursor+1）・無ければ窓頭（today − window）から（_resolve_start）。
        start = _resolve_start(no_cursor_fallback=today - timedelta(days=args.window_days))

    if start > today:
        print(f"✔ 未クロールの提出日なし（カーソル {start - timedelta(days=1)}）。完了。")
        return 0

    print(f"▶ EDINET バックフィル: 提出日 {start}〜{today} をクロール（cap={args.limit}）")
    try:
        result = crawl(start_date=start, end_date=today, cap=args.limit, log=print)
    except KeyboardInterrupt:
        print("✖ 中断した。再実行すれば続きから走る（カーソルが再開点・冪等）", file=sys.stderr)
        return 130

    failures = result["failures"]
    print(
        f"▶ 完了: {result['dates_done']} 日クロール・要約 {result['n_summarized']} 件"
        f"・skip dossier {result['n_skip_dossier']}/既存 {result['n_skip_existing']}"
        f"・事業の内容なし {result['n_no_business']}・失敗 {len(failures)}"
        f"（最終カーソル {result['last_cursor']}・cap 到達={result['cap_reached']}）"
    )
    if failures:
        print(f"✖ 失敗 {len(failures)} 件（再実行で拾い直し可）:", file=sys.stderr)
        for f in failures[:20]:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print("✔ 完了")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
