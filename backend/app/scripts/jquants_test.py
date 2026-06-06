"""J-Quants 疎通テスト（CLI 口・ADR-008/011/036）。

    uv run python -m app.scripts.jquants_test   # J-Quants V2 に認証ピングを 1 発投げる

services/diagnostics.py の check_jquants()（脳）を CLI から叩く薄い口（ADR-011「1つの脳・
複数の起動口」）。DB には触らない。キー未設定 or 疎通失敗なら**終了コード 1**で返す
（make などが失敗に気づけるように）。成功で 0。API キーは backend の .env 固定（秘密情報）。
"""

from __future__ import annotations

from app.services.diagnostics import check_jquants


def main() -> int:
    result = check_jquants()
    if not result.configured:
        print("✖ JQUANTS_API_KEY が未設定です（backend の .env を確認するのだ）")
        return 1
    if not result.ok:
        print(f"✖ J-Quants 疎通に失敗しました: {result.detail}")
        return 1
    print(f"✔ J-Quants 疎通OK: {result.detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
