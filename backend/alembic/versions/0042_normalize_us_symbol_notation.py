"""米株シンボルの Yahoo 表記正規化に伴う旧・誤表記行の掃除（ADR-090）

Revision ID: 0042_normalize_us_symbol_notation
Revises: 0041_edinet_config
Create Date: 2026-07-07

ADR-090。`sync_us_universe` の NASDAQ Trader directory パーサが、クラス株をドット（`BF.A`）・
優先株等を `$`（`BAC$L`）で綴った生シンボルを無変換で `us_stocks` に焼いていたため、Yahoo
Finance で引けず（`yf.Ticker('BF.A').info` が空 dict）夜バッチ fetch_us_fundamentals が毎晩
「bot 検知/レート制限の疑い」と誤診して失敗していた。パーサ側をドット→ハイフン正規化＋特殊記号
除外に直したが（`_normalize_us_symbol`）、`sync_us_universe` は UPSERT のみで旧行を消さないため、
既に焼かれた誤表記行（`BF.A` 等）が巡回対象に残り失敗し続ける。この migration で旧・誤表記行を
一度だけ掃除する（次回 sync で `BF-A` 等の正表記が入る）。

掃除対象: `us_stocks` のうち Yahoo 表記でない（英大文字・数字・ハイフン以外の文字を含む）symbol と
その関連行（`us_daily_quotes`／`us_valuation_snapshots`／fundamentals 巡回カーソル
`fetch_meta['us_fundamentals:<symbol>']`）＝ repo.delete_us_stock と同じ範囲。ただし
`us_transactions`（＝ us_holdings の一次データ）が参照する symbol は残す（FK 保護・ユーザー入力の
保有/取引データを消さない）。

採番: 直前 head は 0041_edinet_config。連鎖を直線に保つため down_revision=0041。
冪等性: 条件に合う行が無ければ no-op（create_schema 直後の空 us_stocks では何も消えない）。DB に
触れる OS プロセスは FastAPI 1 つ（ADR-005）。downgrade は復元不能なので no-op（次回 sync で正表記が
入るため実害は無い）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0042_normalize_us_symbol_notation"
down_revision: str | None = "0041_edinet_config"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Yahoo 表記でない（英大文字・数字・ハイフン以外を含む）symbol の GLOB。ドット/ダラー等を含む旧・
# 誤表記行を拾う（SQLite GLOB は case-sensitive・末尾 '-' はリテラル）。
_BAD_SYMBOL_GLOB = "*[^A-Z0-9-]*"


def _existing_tables() -> set[str]:
    bind = op.get_bind()
    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    tables = _existing_tables()
    if "us_stocks" not in tables:
        return
    # 保有/取引が参照する symbol は残す（FK 保護）。us_transactions が無い環境では条件を外す。
    keep = (
        " AND symbol NOT IN (SELECT symbol FROM us_transactions)"
        if "us_transactions" in tables
        else ""
    )
    where = f"symbol GLOB :bad{keep}"
    params = {"bad": _BAD_SYMBOL_GLOB}

    if "us_daily_quotes" in tables:
        op.execute(sa.text(f"DELETE FROM us_daily_quotes WHERE {where}").bindparams(**params))
    if "us_valuation_snapshots" in tables:
        op.execute(
            sa.text(f"DELETE FROM us_valuation_snapshots WHERE {where}").bindparams(**params)
        )
    if "fetch_meta" in tables:
        op.execute(
            sa.text(
                "DELETE FROM fetch_meta WHERE source IN "
                f"(SELECT 'us_fundamentals:' || symbol FROM us_stocks WHERE {where})"
            ).bindparams(**params)
        )
    op.execute(sa.text(f"DELETE FROM us_stocks WHERE {where}").bindparams(**params))


def downgrade() -> None:
    # 復元不能（消したマスタ/OHLCV は戻せない）。次回 sync_us_universe で正表記が入るため no-op。
    pass
