"""環境変数の読み込みと検証。

秘密情報（J-Quants / LLM のキー）は backend の .env のみに置く（ADR-005・architecture.md §7.1）。
LLM / J-Quants のキーが無くても Phase 0 は起動できる（architecture.md §7.4）。
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """`.env` から読み込むアプリ設定。未設定でも起動はできる（Phase ごとに必要なキーが増える）。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- J-Quants API (V2) --- Phase 0〜（実データ取得時に必須）
    jquants_api_key: str = ""

    # --- データベース ---
    database_path: str = "./data/assetvane.db"

    # --- LLM (AI Advisor) --- Phase 3〜
    llm_api_key: str = ""
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_model: str = "anthropic/claude-sonnet-4-6"

    # --- API サーバ ---
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_allow_origins: str = "http://localhost:3000"

    # --- 通知 (Discord) --- Phase 6〜
    discord_webhook_url: str = ""

    @property
    def cors_origins(self) -> list[str]:
        """カンマ区切りの CORS オリジンをリストにする。"""
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    def env_status(self) -> dict[str, dict[str, object]]:
        """各キーの充足状況を「どの Phase で要るか」付きで返す（/health 用）。

        未設定でも Phase 0 は動く。実データ取得や AI を使う段階で必要になる。
        """
        return {
            "jquants_api_key": {"set": bool(self.jquants_api_key), "required_from_phase": 0},
            "llm_api_key": {"set": bool(self.llm_api_key), "required_from_phase": 3},
            "discord_webhook_url": {
                "set": bool(self.discord_webhook_url),
                "required_from_phase": 6,
            },
        }


settings = Settings()
