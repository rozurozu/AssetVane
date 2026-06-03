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
from app.quant.volume_spike import compute_volume_spike

logger = logging.getLogger(__name__)

# (signal_type, 計算関数) の対応。後続 Phase はここに append する。
_COMPUTERS = (
    ("momentum", compute_momentum),
    ("volume_spike", compute_volume_spike),
)


def _quotes_df(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """get_quotes の dict 列を date 昇順の DataFrame 化する（adj_close/volume を含む）。"""
    df = pd.DataFrame(rows, columns=["date", "adj_close", "volume"])
    return df.sort_values("date").reset_index(drop=True)


def run() -> JobResult:
    """全銘柄ループで momentum / volume_spike を計算し signals を冪等 UPSERT する（spec §3.3）。

    例外は握って JobResult(ok=False) で返す（runner が Discord 通知）。
    """
    out_rows: list[dict[str, Any]] = []
    n_codes = 0
    try:
        with get_engine().connect() as conn:
            codes = repo.list_stock_codes(conn)
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
                    out_rows.append(
                        {
                            "date": result["date"],
                            "code": code,
                            "signal_type": signal_type,
                            "score": result["score"],
                            # payload は json.dumps 済み文字列で渡す契約（repo は変換しない）。
                            "payload": json.dumps(result["payload"], ensure_ascii=False),
                        }
                    )

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
