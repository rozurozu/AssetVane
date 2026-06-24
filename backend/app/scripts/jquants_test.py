"""J-Quants 疎通テスト（CLI 口・ADR-008/011/036/061）。

    uv run python -m app.scripts.jquants_test   # J-Quants V2 に認証ピングを 1 発投げる

services/diagnostics.py の check_jquants()（脳）を CLI から叩く薄い口（ADR-011「1つの脳・
複数の起動口」）。接続値（api_key/plan）は env から DB（jquants_config）へ移管したため（ADR-061）、
読み取り接続を開いて渡す。キー未設定 or 疎通失敗なら**終了コード 1**で返す（make などが失敗に
気づけるように）。成功で 0。
"""

from __future__ import annotations

from app.db.engine import get_engine
from app.services.diagnostics import check_jquants


def main() -> int:
    with get_engine().connect() as conn:
        result = check_jquants(conn)
    if not result.configured:
        print("✖ J-Quants API キーが未設定です（/settings の「J-Quants 設定」から登録するのだ）")
        return 1
    if not result.ok:
        print(f"✖ J-Quants 疎通に失敗しました: {result.detail}")
        return 1
    print(f"✔ J-Quants 疎通OK: {result.detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
