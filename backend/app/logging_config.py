"""ログ基盤の一元設定（ADR-038／architecture.md §7.2）。

backend 全体のログを「人間が読めるテキスト」で stdout に寄せる。JSON 集約は将来
Mac mini 導入時に再検討する（ADR-038）。Pi での永続化・ローテーションは docker 側が担い、
本モジュールは出力形式とレベル・uvicorn ロガーの整列・/health の access 抑制だけを受け持つ。
"""

from __future__ import annotations

import logging
import logging.config

from app.config import settings


class HealthAccessFilter(logging.Filter):
    """uvicorn.access から `/health` のアクセスログを弾くフィルタ（ADR-038）。

    死活監視のポーリングでログが埋まるのを防ぐ。uvicorn.access のメッセージはリクエスト
    パスを含むため、整形済み文字列（`record.getMessage()`）に `/health` を含む行を落とす。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return "/health" not in record.getMessage()


def setup_logging() -> None:
    """logging を dictConfig で一括設定する（ADR-038／architecture.md §7.2）。

    フォーマットはテキスト 1 行、ハンドラは stdout の StreamHandler 1 本。root と uvicorn 系
    ロガーを同じハンドラ・フォーマットに揃え、uvicorn 系は root への二重出力を避けるため
    propagate=False にする。root レベルは settings.log_level（不正値で落ちないよう .upper() のみ）。
    """
    level = settings.log_level.upper()
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "text": {
                    "format": "%(asctime)s %(levelname)s %(name)s: %(message)s",
                    "datefmt": "%Y-%m-%d %H:%M:%S",
                },
            },
            "filters": {
                "health_access": {
                    "()": HealthAccessFilter,
                },
            },
            "handlers": {
                "stdout": {
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stdout",
                    "formatter": "text",
                },
            },
            "root": {
                "level": level,
                "handlers": ["stdout"],
            },
            "loggers": {
                "uvicorn": {
                    "level": level,
                    "handlers": ["stdout"],
                    "propagate": False,
                },
                "uvicorn.error": {
                    "level": level,
                    "handlers": ["stdout"],
                    "propagate": False,
                },
                "uvicorn.access": {
                    "level": level,
                    "handlers": ["stdout"],
                    "filters": ["health_access"],
                    "propagate": False,
                },
            },
        }
    )
