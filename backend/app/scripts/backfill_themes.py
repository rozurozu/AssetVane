"""テーマタグ段階 A（US）の一括バックフィル script（ADR-050 改訂「手動フル再タグ」の口）。

    uv run python -m app.scripts.backfill_themes                      # フェーズ1+2（説明→タグ）
    uv run python -m app.scripts.backfill_themes --descriptions-only  # フェーズ1のみ
    uv run python -m app.scripts.backfill_themes --retag-all          # 手動フル再タグ（ADR-050）
    uv run python -m app.scripts.backfill_themes --limit 50           # 各フェーズの上限（試走用）

- **フェーズ1（説明バックフィル）**: `us_stocks` の普通株（is_etf=0 または NULL＝
  list_us_codes_for_theme_tagging の coalesce 扱いと整合）のうち **company_descriptions に
  行が無い** symbol だけ `.info` を取得し、business_summary 非空なら保存する（捏造しない＝
  ADR-014）。**既存行 skip がそのまま中断再開の仕組み**＝Ctrl-C で途中終了しても、再実行すれば
  保存済み銘柄を飛ばして続きから走る（冪等）。
- **フェーズ2（タグ付け）**: 種テーマ投入（冪等）→ fetch_meta カーソル `us_themes:<symbol>` が
  **無い** symbol だけ grounded タガー（tag_stock_themes）にかけ、成功でカーソルを ISO datetime
  （時刻まで・repo の差分判定契約）で前進させる。**カーソルが中断再開点**＝再実行で続きから。
  `--retag-all` はカーソルの有無を無視して全対象（company_descriptions 有り・ETF 除外）を回し、
  成功した銘柄のカーソルを bump する（語彙が育った後のフル再タグ用・ADR-050）。

運用注意:
- フェーズ1 は数千銘柄 × 約 1 秒スロットル（UsEquityAdapter 内蔵・ADR-010）で**数時間かかる**。
- フェーズ2 は LLM を数千回呼ぶ（安いモデル前提でも llm_usage に計上されコストが発生する＝
  ADR-012）。`--limit` で試走してコストを見積もってから全量を流すこと。
- 夜間バッチと同じ Yahoo/LLM 資源を使うため、**夜間バッチ時間帯を避けて実行する**こと。

銘柄単位の失敗は握って続行し（✖ print・ADR-018 の流儀）、最後に成功/失敗/skip 件数と
失敗一覧を print する。SQL は書かず repo の既存関数の組み合わせで完結する（backend-repo 規約）。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime

from sqlalchemy import Connection

from app.adapters.us_equity import UsEquityAdapter
from app.advisor import theme_tagger
from app.db import repo
from app.db.engine import get_engine, init_db
from app.reference.theme_seeds import SEED_THEMES

# per-symbol fetch_meta source キー接頭辞（list_us_codes_for_theme_tagging と同じ慣行）。
_THEME_SOURCE_PREFIX = "us_themes"

# repo.list_us_codes_for_theme_tagging は limit 必須のため、全件列挙には十分大きい値を渡す
# （全件版 select を repo に増やさず既存関数を流用する＝タスク規約）。
_LIST_ALL_LIMIT = 1_000_000


def _theme_source_key(symbol: str) -> str:
    """シンボルごとのタガーカーソルキー（例: 'us_themes:AAPL'・fetch_us_fundamentals 同型）。"""
    return f"{_THEME_SOURCE_PREFIX}:{symbol}"


def _has_theme_cursor(conn: Connection, symbol: str) -> bool:
    """タグ付け済みカーソルが立っているか（last_fetched_date が入っているときだけ True）。

    mark_fetch_attempt_failed 等で行だけあり last_fetched_date が NULL のものは「未タグ」扱い
    （失敗銘柄は再実行で拾い直す＝ADR-018 部分失敗からの再開）。
    """
    meta = repo.get_fetch_meta(conn, _theme_source_key(symbol))
    return bool(meta and meta.get("last_fetched_date"))


def backfill_descriptions(
    *, limit: int | None = None, adapter: UsEquityAdapter | None = None
) -> tuple[int, int, list[str]]:
    """フェーズ1: company_descriptions の無い US 普通株の事業説明を取得・保存する。

    対象は us_stocks の普通株（is_etf=0 または NULL）のうち company_descriptions（market='US'）
    に行が無い symbol だけ。**既存行 skip が中断再開の仕組み**（再実行で続きから・冪等）。
    business_summary が欠損/空の銘柄は書かない（捏造しない＝ADR-014）。
    `.info` のスロットル（約 1 秒/銘柄）は adapter 内蔵（ADR-010）。

    Returns:
        (成功件数, summary 無し skip 件数, 失敗一覧)。
    """
    # 対象選定: repo の既存関数の組み合わせで完結する（script 内 SQL 禁止＝repo 規約）。
    # get_company_description の N 回 select になるが、一回きりの script なので許容する。
    with get_engine().connect() as conn:
        all_stocks = repo.list_us_stocks(conn)
        # ETF 除外は list_us_codes_for_theme_tagging の coalesce(is_etf,0)=0 と同じ扱い
        # （NULL は普通株扱い）。
        symbols = [s["symbol"] for s in all_stocks if (s.get("is_etf") or 0) == 0]
        pending = [s for s in symbols if repo.get_company_description(conn, "US", s) is None]

    n_existing = len(symbols) - len(pending)
    if limit is not None:
        pending = pending[:limit]
    print(
        f"▶ フェーズ1（説明バックフィル）: 対象 {len(pending)} 件"
        f"（普通株 {len(symbols)} 件中・既存 skip {n_existing} 件）"
    )
    if not pending:
        return 0, 0, []

    adapter = adapter or UsEquityAdapter()
    n_ok = 0
    n_no_summary = 0
    failures: list[str] = []
    total = len(pending)

    for i, symbol in enumerate(pending, start=1):
        try:
            snap = adapter.fetch_fundamentals(symbol)
            summary = snap.get("business_summary")
            if not (isinstance(summary, str) and summary.strip()):
                n_no_summary += 1
                print(f"  - [{i}/{total}] {symbol}: business_summary 無し（書かない）")
                continue
            repo.upsert_company_description(
                {
                    "market": "US",
                    "code": symbol,
                    "source": "yfinance",
                    "description_text": summary,
                    "disclosed_date": None,  # `.info` に基準日なし（US は NULL・ADR-050）
                    "doc_id": None,  # EDINET 専用 provenance（US は NULL）
                    "fetched_at": datetime.now(UTC).isoformat(),
                }
            )
            n_ok += 1
            print(f"  ✔ [{i}/{total}] {symbol}: 説明 {len(summary)} 字を保存")
        except Exception as exc:  # noqa: BLE001 — 銘柄単位で握って続行（ADR-018 の流儀）
            failures.append(f"説明 {symbol}: {exc}")
            print(f"  ✖ [{i}/{total}] {symbol}: 取得失敗: {exc}", file=sys.stderr)

    print(
        f"▶ フェーズ1 完了: 成功 {n_ok} / summary 無し {n_no_summary} / 失敗 {len(failures)}"
        f"（既存 skip {n_existing}）"
    )
    return n_ok, n_no_summary, failures


def backfill_tags(
    *, limit: int | None = None, retag_all: bool = False
) -> tuple[int, int, list[str]]:
    """フェーズ2: company_descriptions のある US 普通株を grounded タグ付けする（ADR-050 改訂）。

    種テーマ（SEED_THEMES）を冪等に目録へ投入後、fetch_meta カーソル `us_themes:<symbol>` が
    無い symbol（company_descriptions 有り・ETF 除外）だけを tag_stock_themes にかけ、成功で
    カーソルを ISO datetime（時刻まで）で前進させる。**カーソルが中断再開点**。
    retag_all=True はカーソルの有無を無視して全対象を回す（成功でカーソル bump・手動フル再タグ）。
    説明テキスト無しで tagger が skip した銘柄はカーソルを進めない（タグ付けは起きていないため
    再実行で拾い直される。candidates は説明行が前提なので通常は起きない）。

    Returns:
        (成功件数, tagger skip 件数, 失敗一覧)。
    """
    now = datetime.now(UTC).isoformat()
    n_seeded = repo.insert_themes_if_absent(list(SEED_THEMES), now)
    print(f"▶ フェーズ2（タグ付け）: 種テーマ投入 {n_seeded} 件（既存は素通し・冪等）")

    # 対象選定: 未タグ最優先の既存関数を limit 大で全件列挙に流用し、通常運転ではカーソル有りを
    # 落とす（--retag-all はカーソル無視で全対象）。
    with get_engine().connect() as conn:
        candidates = repo.list_us_codes_for_theme_tagging(conn, _LIST_ALL_LIMIT)
        if not retag_all:
            candidates = [c for c in candidates if not _has_theme_cursor(conn, c)]
    if limit is not None:
        candidates = candidates[:limit]
    print(f"  対象 {len(candidates)} 件（retag_all={retag_all}）")
    if not candidates:
        return 0, 0, []

    n_ok = 0
    n_skip = 0
    failures: list[str] = []
    total = len(candidates)

    for i, code in enumerate(candidates, start=1):
        try:
            # 接続は銘柄ごとに開閉する: 長寿命読み取りスナップショットを持つと、直前の銘柄で
            # 増えた語彙（themes）が list_theme_names に見えず exact 再用が崩れるため。
            with get_engine().connect() as conn:
                result = asyncio.run(theme_tagger.tag_stock_themes(conn, market="US", code=code))
            if result.get("skipped"):
                n_skip += 1
                print(f"  - [{i}/{total}] {code}: 説明テキスト無しで skip（カーソルは進めない）")
                continue
            repo.upsert_fetch_meta(_theme_source_key(code), datetime.now(UTC).isoformat())
            n_ok += 1
            print(f"  ✔ [{i}/{total}] {code}: themes={result.get('themes')}")
        except Exception as exc:  # noqa: BLE001 — 銘柄単位で握って続行（ADR-018 の流儀）
            failures.append(f"タグ {code}: {exc}")
            print(f"  ✖ [{i}/{total}] {code}: タグ付け失敗: {exc}", file=sys.stderr)

    print(f"▶ フェーズ2 完了: 成功 {n_ok} / skip {n_skip} / 失敗 {len(failures)}")
    return n_ok, n_skip, failures


def main(argv: list[str] | None = None) -> int:
    """argv を解釈してフェーズ1（＋フェーズ2）を流す（テストから引数 list で呼べる）。"""
    parser = argparse.ArgumentParser(
        prog="backfill_themes",
        description="テーマタグ段階 A（US）の一括バックフィル（ADR-050 改訂）",
    )
    parser.add_argument(
        "--descriptions-only",
        action="store_true",
        help="フェーズ1（説明バックフィル）だけ実行し、タグ付けはしない",
    )
    parser.add_argument(
        "--retag-all",
        action="store_true",
        help="カーソルの有無を無視して全対象を再タグする（手動フル再タグ・ADR-050）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="各フェーズの処理銘柄数上限（試走・コスト見積もり用）",
    )
    args = parser.parse_args(argv)

    init_db()

    failures: list[str] = []
    try:
        _, _, desc_failures = backfill_descriptions(limit=args.limit)
        failures.extend(desc_failures)
        if not args.descriptions_only:
            _, _, tag_failures = backfill_tags(limit=args.limit, retag_all=args.retag_all)
            failures.extend(tag_failures)
    except KeyboardInterrupt:
        # フェーズ1=既存行 skip・フェーズ2=fetch_meta カーソルが再開点（モジュール docstring）。
        print("✖ 中断した。再実行すれば続きから走る（冪等）", file=sys.stderr)
        return 130

    if failures:
        print(f"✖ 失敗 {len(failures)} 件:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print("✔ 完了")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
