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

    # --- ログ（ADR-038） ---
    # logging の root レベル。env LOG_LEVEL で可変（case-insensitive で読む）。
    # 形式は人間が読めるテキストで stdout に寄せる（Pi の永続化は docker 側で別レーン担当）。
    log_level: str = "INFO"

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

    # --- LLM provider 面別切替（codex 接続・ADR-012 の延長／plans 参照） ---
    # source（chat/nightly/dossier）ごとに "openai"（既定・OpenRouter 等）か "codex"
    # （codex app-server ＋ FastAPI ホストの MCP）を選ぶ。何も設定しなければ全面 openai＝従来通り。
    # codex は ChatGPT サブスク認証（API キー不要）で限界費用ゼロ。無人 cron のトークン継続に
    # 制約があるため nightly は既定 openai のまま実証後に寄せる方針。
    llm_provider_chat: str = "openai"
    llm_provider_nightly: str = "openai"
    llm_provider_dossier: str = "openai"

    # --- codex app-server（provider="codex" のとき使う） ---
    # codex CLI を常駐 app-server（stdio JSON-RPC）として駆動し、自前 Tool は MCP 越しに呼ばせる
    # （plans / ADR-012）。exec は MCP がキャンセルされる既知不具合のため app-server を使う。
    codex_bin: str = "codex"  # 実行ファイル（PATH 上の名前 or 絶対パス）。`codex app-server` を起動
    codex_model: str = "gpt-5.5"  # thread/start の model（codex 側の強モデル）
    # thread/start の sandbox。Advisor は書かない（書き込みは MCP Tool＝FastAPI）。
    codex_sandbox: str = "read-only"
    codex_mcp_url: str = "http://localhost:8000/mcp"  # FastAPI 内 MCP の URL（codex が接続する）
    codex_startup_timeout_seconds: float = 20.0  # app-server ハンドシェイク（initialize）の待ち上限
    codex_timeout_seconds: float = 180.0  # 1 turn の上限（内部 Tool ループ込みで余裕を持つ）
    codex_max_retries: int = 2  # 一過性失敗（serverOverloaded 等）の指数バックオフ再試行回数

    # --- API サーバ ---
    # CORS 設定は廃止（ADR-037）。ブラウザは Next 同一オリジンの /api だけを叩き、Next の
    # rewrites が裏で backend へ素通しするため、許可オリジンを backend に持たせる必要が無くなった。
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # --- 通知 (Discord) --- Phase 6〜
    discord_webhook_url: str = ""

    # --- 通知 digest（Phase 6 Signal Beacon・phase6-spec.md §3・ADR-007/018） ---
    # 夜間バッチ末尾の notify_digest が⑦⑧＋夜AI 提案を 1 通に束ねて送る。閾値は env で後から差替可。
    alert_score_min: float = 0.6  # ⑧ 高スコア銘柄の閾値（signals.score 0..1）
    alert_top_n: int = 10  # digest に載せるシグナルの上限（score 降順・残りは件数のみ）
    rebalance_alert_days: int = 14  # ⑦ 最終見直し（policy.updated_at）からの経過閾値（日）
    always_daily_digest: bool = True  # 検知ゼロでも毎朝サマリを送る（False で好機がある日だけ）
    # ⑧ 出来高急増（平常 3 倍）の閾値は quant が payload.notable に焼く（ADR-016）。通知層は
    # 閾値を再定義せず notable を読む（score>=alert_score_min または notable で⑧アラート）。

    # --- 夜間バッチ / cron --- Phase 1〜（spec §3.7・ADR-021・ADR-011）
    # APScheduler を FastAPI プロセスに同居させる（追加コンテナ 0）。
    # dev の --reload 二重起動を避けるため既定は false（prod で true）。
    batch_scheduler_enabled: bool = False
    batch_cron_hour: int = 2  # 既定 02:00 JST（U-9・spec §3.7）
    batch_cron_minute: int = 0
    batch_tz: str = "Asia/Tokyo"
    # 初回バックフィルで頭から取り直す年数（spec §3.4）。
    backfill_years: int = 2
    # J-Quants の契約プラン名。許容値は free / light（ADR-008）。秒数（スロットル間隔）は env で
    # お守りせず、アダプタ（adapters/jquants.py の _PLAN_INTERVALS）がプラン名から決める＝
    # free→16s / light→1s。V2 にプランを返す API は無いため env でプラン名だけ指定する。
    # プラン移行（実運用時に1回・ADR-008）は env のこの1語を変えるだけ（秒数のコード変更は不要）。
    jquants_plan: str = "free"

    # --- IndexAdapter（主要指数・Phase 2〜・phase2-spec.md §3.1・ADR-010） ---
    # Stooq を既定ソースとして使用（裁定 L-10）。シンボルはカンマ区切りで指定。
    # Stooq シンボル例: ^SPX（S&P500）・^NKX（日経225）・^TPX（TOPIX）。
    # [OPEN] TOPIX/日経の J-Quants 指数 API 有無は実 API 確認待ち（spec §3.1）。
    index_source: str = "stooq"
    index_symbols: str = "^SPX,^NKX,^TPX"
    # IndexAdapter（Stooq）取得のスロットル間隔（秒）。Stooq は 1.0 で十分（ADR-010）。
    index_min_interval_seconds: float = 1.0

    # --- NewsAdapter（fetch_news 実取得・Phase 4・ADR-010/ADR-020） ---
    # httpx 一本（Google News RSS → URL 復元 → trafilatura 本文 → 記事ごと AI 要約 → 本文破棄）。
    # 直結ハードコード禁止（ADR-010）。値は config 経由で渡す。
    news_enabled: bool = True  # 無効化スイッチ（false でニュース取得をスキップ）
    news_max_articles_per_stock: int = 10  # 1 銘柄あたり要約する記事の上限（コスト制御）
    news_http_timeout_seconds: float = 20.0  # 本文/RSS GET のタイムアウト（秒）
    news_min_interval_seconds: float = 1.0  # 取得スロットル間隔（秒・throttle）
    google_news_base_url: str = "https://news.google.com"  # Google News RSS のベース URL
    google_news_lang: str = "ja"  # クエリの hl（言語）
    google_news_country: str = "JP"  # クエリの gl/ceid（国）

    # --- AI Alpha Scorer（Phase 5・phase5-spec.md §4・ADR-006） ---
    # 学習済み .pkl（別 PC 産・ADR-006）とメタ JSON の置き場。git 管理外・compose で bind mount。
    # model_store.load_active が <kind>-latest.json → .pkl を引く。既定 "./models"（dev=backend/
    # 直下・prod コンテナ=/app/models）。本番（compose.prod.yaml）は ./backend/models を mount。
    ml_model_dir: str = "./models"

    # --- 銘柄別調査 cadence（夜の巡回・ADR-033） ---
    # watchlist の interval_days で各銘柄の調査間隔を持ち、夜あたりの処理本数は天井で抑える。
    # 固定 N=3 を廃し、暴走防止の上限として残す（投資 dossier 巡回ジョブが消費する）。
    dossier_nightly_max: int = 3  # 夜あたり巡回上限の天井（暴走防止）

    def provider_for(self, source: str) -> str:
        """source（chat/nightly/dossier）から LLM provider を返す（既定 openai）。

        未知 source や未設定は安全側に openai（従来経路）へ落とす。engine が参照する。
        """
        mapping = {
            "chat": self.llm_provider_chat,
            "nightly": self.llm_provider_nightly,
            "dossier": self.llm_provider_dossier,
        }
        return mapping.get(source, "openai") or "openai"

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
