"""差分取得カーソルの開始日決定（純関数・batch-pattern／ADR-018）。

複数の差分取得ジョブ（fetch_index / fetch_us_quotes / fetch_fx_rates / fetch_fund_navs）が
持っていた「last_fetched_date から鮮度プローブ分だけ重ねて開始日を決める」計算をここに
純関数化する（tasks/review-2026-06-12.md C-1: 同一式が 4 ジョブに散在し、fetch_index の
overlap 修正が他ジョブへ伝播しなかった実害の解消）。

この関数は DB を知らない純関数（ADR-005: DB に触れるのはジョブ側）。ジョブが fetch_meta から
last_fetched_date を読み、初回/全取得の開始日（backfill_start）を用意して渡す。カーソルの粒度
（銘柄毎／全銘柄共通／単一ペア／ISIN 毎の fetch_meta キー）・full_backfill の早期分岐・空取得時
の前進可否はジョブ固有なのでこの関数の外に残す（batch-pattern）。
"""

from __future__ import annotations

from datetime import date, timedelta

# 差分取得の鮮度プローブ用の重ね日数（旧 _REFETCH_OVERLAP_DAYS の正準値・ADR-018）。
# 開始日を最終取得日からこの日数だけ前に戻して取り直すことで、健全なソースは直近営業日のバーが
# 必ず窓に入り ≥1 行返る → 「新規データ無し（週末・連休明け・当日未掲載）」と「取得不能」を
# 区別できる（誤検知防止）。週末＋単発祝日を跨いでも直近営業日が窓に入る余裕。再取得は UPSERT で
# 冪等（ADR-002）。
DEFAULT_OVERLAP_DAYS = 5


def backfill_start_date(today: str, years: int) -> str:
    """初回/全取得の開始日を today から years 年前で決める純関数（閏日安全・#16）。

    `date.replace(year=...)` は today が 2/29 で years 年前が非閏年だと ValueError を投げる。素朴に
    `last.replace(year=last.year - N)` を書いていた 5 ジョブ（fetch_quotes/financials/index/
    us_quotes/fx_rates）は、閏日（2/29）実行で差分運転でも毎回クラッシュしていた。年差を保ったまま
    2/29 → 2/28 に丸めて安全に計算する（1 日のズレは backfill 開始日の粒度では無害・ADR-018）。
    """
    d = date.fromisoformat(today)
    try:
        return d.replace(year=d.year - years).isoformat()
    except ValueError:  # 2/29 → years 年前が非閏年: 2/28 に丸める
        return d.replace(year=d.year - years, day=28).isoformat()


def resolve_differential_start(
    last_fetched: str | None,
    *,
    backfill_start: str,
    overlap_days: int = DEFAULT_OVERLAP_DAYS,
) -> str:
    """差分取得の開始日（YYYY-MM-DD）を決める純関数（ADR-018 鮮度プローブ）。

    last_fetched（fetch_meta の last_fetched_date）が None（未取得）なら backfill_start を
    そのまま返す（初回）。それ以外は last_fetched に overlap_days 日重ねた地点
    （last_fetched − overlap_days）を返す。翌日からではなく重ねて取り直すことで、健全な
    ソースは直近営業日のバーが必ず窓に入り ≥1 行返るため、「新規データ無し」と「取得不能」を
    区別できる（誤検知防止・ADR-018）。再取得は UPSERT で冪等（ADR-002）。

    初回/全取得の開始日差（BACKFILL_YEARS 年前 か '1900-01-01' 番兵）は backfill_start 引数で
    吸収する（番兵判定はジョブ側に残す）。full_backfill による「カーソル無視で頭から」も
    ジョブ側の早期分岐に残す（この関数には渡さない）。

    Args:
        last_fetched: fetch_meta の last_fetched_date（未取得なら None）。
        backfill_start: 初回（last_fetched is None）に返す開始日。年差はジョブ側で計算し、
            番兵 '1900-01-01' を使うジョブはそれを渡す。
        overlap_days: 鮮度プローブの重ね日数（既定 DEFAULT_OVERLAP_DAYS）。
    """
    if last_fetched is None:
        return backfill_start
    return (date.fromisoformat(last_fetched) - timedelta(days=overlap_days)).isoformat()
