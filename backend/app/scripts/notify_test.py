"""Discord 疎通テスト（CLI 口・ADR-007/011/018）。

    uv run python -m app.scripts.notify_test   # Discord に 1 通テスト送信する

batch/notify.py の send_test_notification()（脳）を CLI から叩く薄い口（ADR-011「1つの脳・
複数の起動口」）。冪等は通さず毎回飛ぶ。Webhook 未設定 or 送信失敗なら**終了コード 1**で返す
（make などが失敗に気づけるように）。送信成功で 0。webhook URL は backend の .env 固定（秘密情報）。
"""

from __future__ import annotations

from app.batch.notify import send_test_notification


def main() -> int:
    result = send_test_notification()
    if not result.enabled:
        print("✖ DISCORD_WEBHOOK_URL が未設定です（backend の .env を確認するのだ）")
        return 1
    if not result.sent:
        print("✖ Discord 送信に失敗しました（Webhook URL・ネットワークを確認するのだ）")
        return 1
    print("✔ Discord にテストメッセージを送信しました")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
