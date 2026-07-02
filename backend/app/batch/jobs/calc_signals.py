"""シグナル計算ジョブ — quant 純関数を全銘柄ループで呼び signals に焼く（spec §3.3）。

data-arch×quant の境界。quant モジュール（momentum / volume_spike）は DB を知らない純関数
（ADR-016）。このジョブが糊で、各銘柄の日足を `get_quotes` で読んで DataFrame 化し、純関数を
呼び、戻り値に `code`/`signal_type` を付けて `upsert_signals` にまとめて渡す（書き込みはこの
ジョブ側＝ADR-014「事実は Python が計算」）。payload は json.dumps した文字列で repo に渡す契約。
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pandas as pd

from app.batch.runner import JobResult
from app.db import repo
from app.db.engine import get_engine
from app.quant.momentum import compute_momentum
from app.quant.stealth_accumulation import compute_stealth_accumulation
from app.quant.volume_spike import compute_volume_spike

logger = logging.getLogger(__name__)

# (signal_type, 計算関数) の対応。df 1 引数で済む純関数はここ。後続 Phase はここに append する。
# stealth_accum は時価総額が要る（別シグネチャ）ため run() 内で別扱い（ADR-074）。
_COMPUTERS = (
    ("momentum", compute_momentum),
    ("volume_spike", compute_volume_spike),
)

_OHLC_COLS = ["date", "open", "high", "low", "close", "adj_close", "volume"]


def _quotes_df(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """get_quotes の dict 列を date 昇順の DataFrame 化する（OHLC/adj_close/volume を含む）。

    stealth_accum は OHLC（下ひげ・レンジ）を使うため四本値まで取る。momentum/volume_spike は
    adj_close/volume だけ参照するので widen しても無害（余分列は無視される・ADR-074）。
    """
    df = pd.DataFrame(rows, columns=_OHLC_COLS)
    return df.sort_values("date").reset_index(drop=True)


def _signal_row(code: str, signal_type: str, result: dict[str, Any]) -> dict[str, Any]:
    """quant の戻り（{date,score,payload}）に code/signal_type を付けた signals 用行を組む。

    payload は json.dumps 済み文字列で渡す契約（repo は変換しない・spec §3.3）。
    """
    return {
        "date": result["date"],
        "code": code,
        "signal_type": signal_type,
        "score": result["score"],
        "payload": json.dumps(result["payload"], ensure_ascii=False),
    }


def run() -> JobResult:
    """全銘柄ループで momentum / volume_spike を計算し signals を冪等 UPSERT する（spec §3.3）。

    例外は握って JobResult(ok=False) で返す（runner が Discord 通知）。
    """
    out_rows: list[dict[str, Any]] = []
    n_codes = 0
    try:
        with get_engine().connect() as conn:
            codes = repo.list_stock_codes(conn)
            # 時価総額はループ前に 1 回 bulk 取得（stealth_accum のフロア用・N クエリ回避）。
            market_caps = repo.get_market_caps_by_code(conn)
            for code in codes:
                quotes = repo.get_quotes(conn, code)
                if not quotes:
                    continue
                n_codes += 1
                df = _quotes_df(quotes)
                for signal_type, compute in _COMPUTERS:
                    result = compute(df)
                    if result is None:
                        continue
                    out_rows.append(_signal_row(code, signal_type, result))
                # stealth_accum は時価総額が要るので別扱い（ADR-074）。
                stealth = compute_stealth_accumulation(df, market_caps.get(code))
                if stealth is not None:
                    out_rows.append(_signal_row(code, "stealth_accum", stealth))

        n = repo.upsert_signals(out_rows)
    except Exception as exc:  # noqa: BLE001 — ジョブ境界で握り runner に返す
        logger.exception("calc_signals が失敗")
        return JobResult(name="calc_signals", ok=False, rows=len(out_rows), detail=f"失敗: {exc}")

    return JobResult(
        name="calc_signals",
        ok=True,
        rows=n,
        detail=f"{n_codes} 銘柄を評価・signals {n} 行 UPSERT",
    )
