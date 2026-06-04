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
    # LLM 呼び出しのタイムアウト・リトライ（phase3-spec.md §4.3/§7・data-arch §3.3・ADR-012/018）。
    # AsyncOpenAI の timeout / max_retries に渡す。指数バックオフは base × 2^n。
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 3
    llm_retry_base_seconds: float = 2.0
    # LLM コストガードレール（ADR-028・spec §7.1）。クラウド LLM 期間限定の月額ガード。
    # mode: "off"（監視しない）/ "warn"（既定・止めず通知）/ "block"（超過で呼び出しを止める）。
    # OpenRouter 実コスト（usage.cost）を llm_usage に積み、当月累計で判定。Ollama は $0。
    llm_cost_limit_usd: float = 50.0
    llm_cost_guard_mode: str = "warn"

    # --- API サーバ ---
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_allow_origins: str = "http://localhost:3000"

    # --- 通知 (Discord) --- Phase 6〜
    discord_webhook_url: str = ""

    # --- 夜間バッチ / cron --- Phase 1〜（spec §3.7・ADR-021・ADR-011）
    # APScheduler を FastAPI プロセスに同居させる（追加コンテナ 0）。
    # dev の --reload 二重起動を避けるため既定は false（prod で true）。
    batch_scheduler_enabled: bool = False
    batch_cron_hour: int = 2  # 既定 02:00 JST（U-9・spec §3.7）
    batch_cron_minute: int = 0
    batch_tz: str = "Asia/Tokyo"
    # 初回バックフィルで頭から取り直す年数（spec §3.4）。
    backfill_years: int = 2
    # J-Quants 取得のスロットル間隔（秒）。Free=13.0 / Light=1.0（ADR-008・spec §3.4・L-6）。
    jquants_min_interval_seconds: float = 13.0

    # --- IndexAdapter（主要指数・Phase 2〜・phase2-spec.md §3.1・ADR-010） ---
    # Stooq を既定ソースとして使用（裁定 L-10）。シンボルはカンマ区切りで指定。
    # Stooq シンボル例: ^SPX（S&P500）・^NKX（日経225）・^TPX（TOPIX）。
    # [OPEN] TOPIX/日経の J-Quants 指数 API 有無は実機確認待ち（spec §3.1）。
    index_source: str = "stooq"
    index_symbols: str = "^SPX,^NKX,^TPX"
    # IndexAdapter（Stooq）取得のスロットル間隔（秒）。Stooq は 1.0 で十分（ADR-010）。
    index_min_interval_seconds: float = 1.0

    @property
    def cors_origins(self) -> list[str]:
        """カンマ区切りの CORS オリジンをリストにする。"""
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    @property
    def index_symbol_list(self) -> list[str]:
        """カンマ区切りの指数シンボルをリストにする（phase2-spec.md §3.1）。"""
        return [s.strip() for s in self.index_symbols.split(",") if s.strip()]

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
