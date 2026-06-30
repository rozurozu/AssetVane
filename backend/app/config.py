"""環境変数の読み込みと検証。

秘密情報のうち LLM・embedding・J-Quants の接続（キー/プラン）は env ではなく DB へ移管し
（ADR-058/059/061）、/settings の WebUI から編集する。env に残る秘密情報（Discord / EDINET 等）は
backend の .env のみに置く（ADR-005・architecture.md §7.1）。キーが無くても Phase 0 は起動できる
（architecture.md §7.4）。
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
    # api_key と契約プラン名は DB（jquants_config・単一行）へ移管し /settings の WebUI から編集する
    # （ADR-061・ADR-058/059 と同方針）。env には接続パラメータを残さない（鍵もプランも DB 一本）。
    # スロットル間隔はプラン名から adapters/jquants.py の _PLAN_INTERVALS が決める（ADR-008）。

    # --- データベース ---
    database_path: str = "./data/assetvane.db"
    # デプロイ前バックアップ（VACUUM INTO・ADR-017）で残す世代数。これより古いものは prune する。
    # 全量バックアップ 1 個 ≒ 数百 MB なので少なめが既定。Pi の容量に応じ env BACKUP_KEEP で可変。
    backup_keep: int = 3

    # --- ログ（ADR-038） ---
    # logging の root レベル。env LOG_LEVEL で可変（case-insensitive で読む）。
    # 形式は人間が読めるテキストで stdout に寄せる（Pi の永続化は docker 側で別レーン担当）。
    log_level: str = "INFO"

    # --- LLM (AI Advisor) --- Phase 3〜
    # provider/model/api_key/base_url と面別割当は DB（llm_providers/llm_face_config）に移管し
    # /settings の WebUI から編集する（ADR-058）。env には接続パラメータとコストガードだけ残す。
    # LLM 呼び出しのタイムアウト・リトライ（phase3-spec.md §4.3/§7・data-arch §3.3・ADR-012/018）。
    # AsyncOpenAI の timeout / max_retries に渡す（provider 共通の接続パラメータ）。base × 2^n。
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 3
    llm_retry_base_seconds: float = 2.0
    # LLM コストガードレール（ADR-028・spec §7.1）。クラウド LLM 期間限定の月額ガード。
    # mode: "off"（監視しない）/ "warn"（既定・止めず通知）/ "block"（超過で呼び出しを止める）。
    # OpenRouter 実コスト（usage.cost）を llm_usage に積み当月累計で判定。OpenRouter 以外は cost を
    # 返さず 0 計上＝ガードが空洞化する既知の限界（ADR-058）。
    llm_cost_limit_usd: float = 50.0
    llm_cost_guard_mode: str = "warn"

    # --- Embedding（ニュース意味検索・Phase 4〜・ADR-045/012/006/018/059） ---
    # base_url / api_key / model / dim は DB（embedding_config・/settings で編集）へ移管した
    # （ADR-059）。env には接続パラメータ（timeout）だけ残す。3 キーのいずれかが未設定なら静かに
    # 機能オフ（resolve_embedding_config が None を返す・ADR-006/018）。
    embedding_timeout_seconds: float = 30.0  # embeddings API のタイムアウト（秒）

    # --- codex app-server（provider="codex" のとき使う） ---
    # codex CLI を常駐 app-server（stdio JSON-RPC）として駆動し、自前 Tool は MCP 越しに呼ばせる
    # （plans / ADR-012）。exec は MCP がキャンセルされる既知不具合のため app-server を使う。
    codex_bin: str = "codex"  # 実行ファイル（PATH 上の名前 or 絶対パス）。`codex app-server` を起動
    codex_model: str = "gpt-5.5"  # thread/start の model（codex 側の強モデル）
    # 推論努力レベル＝thread/start の config.model_reasoning_effort（ReasoningEffort enum）。
    # none/minimal/low/medium/high/xhigh のいずれか。空文字なら codex/モデル既定に任せる。
    codex_reasoning_effort: str = ""
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

    # --- IndexAdapter（主要指数・Phase 2〜・phase2-spec.md §3.1・ADR-010） ---
    # 複数ソースをフォールバック連鎖（優先順）。落ちたら次ソースを試す（grill 2026-06）。
    # 既定 "yahoo,stooq,jquants"＝Yahoo（yfinance・配当調整後 close）主・Stooq フォールバック・
    # jquants 最後段。Stooq が bot 判定で死んだため Yahoo を主に据えた（ADR-010）。名前は
    # adapters/index.py の _REGISTRY が解決（未知名はスキップ）。シンボルは canonical 表記を
    # カンマ区切りで（^SPX=S&P500・^NKX=日経225・^TPX=TOPIX・米国業種 ETF=XLK 等の素ティッカー）。
    # jquants は ^TPX 専用・最後段（yahoo が ^SPX/^NKX/米 ETF を成功で返すので非 TOPIX では到達
    # しない。TOPIX は J-Quants /v2/indices/bars/daily/topix・Light 以上で取得＝Free では 403）。
    index_sources: str = "yahoo,stooq,jquants"
    index_symbols: str = "^SPX,^NKX,^TPX"
    # IndexAdapter（Stooq）取得のスロットル間隔（秒）。Stooq は 1.0 で十分（ADR-010）。
    index_min_interval_seconds: float = 1.0

    # --- FundNavAdapter（投信 NAV・基準価額・Phase 2/投信保有管理・ADR-010/054） ---
    # 投信総合検索ライブラリー（ウエルスアドバイザー運営）の CSV を取得する。
    # ダウンロード: {base}/FdsWeb/FDST030000/csv-file-download?isinCd=<ISIN>&associFundCd=<協会>。
    # 文字コードは Shift_JIS（content-type は utf-8 を名乗るが実体 SJIS・2026-06 実機確認）。
    fund_nav_base_url: str = "https://toushin-lib.fwg.ne.jp"
    fund_nav_min_interval_seconds: float = 1.0  # 取得スロットル間隔（秒・Free 系に優しく）
    fund_nav_http_timeout_seconds: float = 30.0  # CSV GET のタイムアウト（秒）

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

    # --- UsEquityAdapter（米国株・Phase 7(B-1)・ADR-010/039/048/055） ---
    # 米株はソース別フォールバック連鎖の素地を残しつつ当面 yahoo（yfinance）一本（grill 確定）。
    # 名前は adapters/us_equity.py の _REGISTRY が解決（未知名はスキップ）。index_sources と同型の
    # CSV・優先順。fetch_quotes（OHLCV）/ fetch_fundamentals（`.info`）/ fetch_universe（NASDAQ
    # Trader directory）をこのアダプタに閉じ込める。秘密情報は不要（yfinance は無認証）。
    us_equity_source: str = "yahoo"
    # yfinance のリクエスト間隔（秒）。`.info` は重く NASDAQ も Free 系なので軽くあける（ADR-010）。
    us_equity_min_interval_seconds: float = 1.0
    # NASDAQ Trader directory（nasdaqlisted/otherlisted）取得のベース URL（HTTP・パイプ区切り）。
    us_universe_base_url: str = "https://www.nasdaqtrader.com"
    us_equity_http_timeout_seconds: float = 30.0  # directory/`.info` GET のタイムアウト（秒）
    # fundamentals 低頻度ローテ巡回の夜あたり処理本数の天井（ADR-033 同型・約 6000 銘柄を約 7 夜で
    # 一周。`.info` が重いので暴走防止の上限として持つ）。後続ウェーブの fetch_us_fundamentals 用。
    us_fundamentals_nightly_max: int = 900
    # OHLCV 取得（fetch_us_quotes）で 1 回の yf.download に一括投入するシンボル数（1 リクエスト＝
    # 1 バッチ＝1 UPSERT トランザクション・ADR-055 バルク化）。per-symbol 取得（約 3 時間）を
    # 桁で短縮する。値を上げるほど HTTP 往復・スロットル待ちは減るが 1 リクエストが重くなりタイム
    # アウト/レート制限を誘発しうるため、まず 50 で運用し実測で 100→200 と段階的に上げる（差分
    # カーソルは全銘柄共通＝fetch_quotes 同型・ADR-018）。
    us_quotes_batch_size: int = 50

    # --- FxAdapter（FX レート・Phase 7(B-2)・ADR-010/057） ---
    # 米株保有を JPY 資産概要へ合算するための FX 換算基盤。取得源は yfinance JPY=X 日足終値一本
    # （UsEquityAdapter 同型のフォールバック連鎖の素地を残しつつ当面 yahoo・grill 確定）。秘密情報は
    # 不要（yfinance は無認証）。直結ハードコード禁止＝値は config 経由で渡す（ADR-010）。
    fx_source: str = (
        "yahoo"  # カンマ区切り・優先順（adapters/fx.py の _REGISTRY が解決・未知名は skip）
    )
    fx_min_interval_seconds: float = 1.0  # yfinance のリクエスト間隔（秒・Free 系に優しく）
    fx_http_timeout_seconds: float = (
        30.0  # 取得タイムアウト（秒・予備＝yfinance 内部 timeout の保険）
    )

    # --- テーマタグ（ADR-050 改訂・段階A・grounded 事前タグ） ---
    # 夜あたりタグ付け本数の天井（ADR-033 の cadence 流用・暴走防止）。約 6000 銘柄の US
    # ユニバースを約 40 夜で一周するローテ（未タグ→説明変化→古い順の優先はクエリ側が担う）。
    theme_tagging_nightly_max: int = 150
    # JP（段階B＝調査済みドシエ）の夜あたりタグ付け天井。母集団は watchlist 調査済み銘柄で
    # US ユニバースより遥かに小さいため控えめ。tag_jp_themes が消費（ADR-050 段階B）。
    theme_tagging_jp_nightly_max: int = 100
    # stock_themes の時間窓 prune（最終再確認 last_seen_at からの日数）。どの再タグにも
    # 再確認されなかったタグだけ枯らす（UPSERT＋bump と対の設計・ADR-050）。
    # 不変条件: theme_prune_days はローテ一周日数（ユニバース数 ÷ theme_tagging_nightly_max）
    # より十分大きく保つこと（一周しきる前に健全なタグが prune される事故防止）。
    theme_prune_days: int = 90

    # --- EdinetAdapter（有報「事業の内容」・テーマタグ段階C・ADR-010/050/056） ---
    # JP 全ユニバースの grounded テーマタグの信号源＝EDINET 有報「事業の内容」。価格・財務は
    # J-Quants のまま（ADR-008）、EDINET はテキスト専用の additive ソース（ADR-056）。秘密情報は
    # backend の .env のみ（ADR-005）。直結ハードコード禁止＝値は config 経由で渡す（ADR-010）。
    edinet_api_key: str = ""  # EDINET API v2 のサブスクリプションキー（無料登録・Subscription-Key）
    edinet_base_url: str = "https://api.edinet-fsa.go.jp/api/v2"  # 書類一覧/取得 API のベース URL
    edinet_http_timeout_seconds: float = 30.0  # documents.json / ZIP GET のタイムアウト（秒）
    edinet_min_interval_seconds: float = 1.0  # 取得スロットル間隔（秒・Free 系に優しく）
    # 夜あたり要約する有報の天井（ADR-033 同型・暴走と LLM コストの上限）。提出日クロールで拾った
    # docTypeCode=120 のうち要約まで進める件数を抑える。差分は低 churn なので通常これに達しない。
    edinet_nightly_max: int = 100
    # バックフィル script の遡及窓（日数）。約 470 日＝15ヶ月で年次サイクル 1 周＋提出ラグ 3ヶ月を
    # カバーし、3月決算以外（12月/9月等）も最新有報を 1 本ずつ拾える（ADR-056・grill 2026-06-11）。
    edinet_backfill_window_days: int = 470

    # --- 銘柄別調査 cadence（夜の巡回・ADR-033） ---
    # watchlist の interval_days で各銘柄の調査間隔を持ち、夜あたりの処理本数は天井で抑える。
    # 固定 N=3 を廃し、暴走防止の上限として残す（投資 dossier 巡回ジョブが消費する）。
    dossier_nightly_max: int = 3  # 夜あたり巡回上限の天井（暴走防止）

    # --- EDINET DB（edinetdb.jp）#2 売掛/在庫の質の取得 cadence（ADR-064） ---
    # 接続値（api_key/plan）は DB（edinetdb_config）・ここは取得頻度の制御のみ（レート予算節約）。
    # 各銘柄の #2 を何日あけて再取得するか（財務は四半期更新ゆえ週次で十分）。
    # fetch_meta で per-code 追跡。
    edinetdb_refresh_interval_days: int = 7
    # 月の残予算がこの数を下回ったら当夜の #2 取得を打ち切る予備（
    # x-ratelimit-monthly-remaining 監視）。
    edinetdb_monthly_reserve: int = 50

    @property
    def index_symbol_list(self) -> list[str]:
        """カンマ区切りの指数シンボルをリストにする（phase2-spec.md §3.1）。"""
        return [s.strip() for s in self.index_symbols.split(",") if s.strip()]

    @property
    def index_source_list(self) -> list[str]:
        """カンマ区切りの指数ソース名を優先順リストにする（フォールバック連鎖・ADR-010）。"""
        return [s.strip() for s in self.index_sources.split(",") if s.strip()]

    @property
    def us_equity_source_list(self) -> list[str]:
        """カンマ区切りの米株ソース名を優先順リストにする（index_source_list 同型・ADR-039）。"""
        return [s.strip() for s in self.us_equity_source.split(",") if s.strip()]

    @property
    def fx_source_list(self) -> list[str]:
        """カンマ区切りの FX ソース名を優先順リストにする（us_equity_source_list 同型・ADR-057）。

        ADR-057（Phase 7(B-2)）。フォールバック連鎖の素地（当面 yahoo 一本）。
        """
        return [s.strip() for s in self.fx_source.split(",") if s.strip()]

    def env_status(self) -> dict[str, dict[str, object]]:
        """各キーの充足状況を「どの Phase で要るか」付きで返す（/health 用）。

        未設定でも Phase 0 は動く。実データ取得や AI を使う段階で必要になる。
        """
        # LLM・embedding・J-Quants の充足は env ではなく DB（面別設定 / embedding_config /
        # jquants_config）で決まるため env_status からは外す（ADR-058/059/061）。画面は
        # GET /llm/faces・/llm/embedding・/jquants/config の configured フラグで設定状況を表示する。
        return {
            "discord_webhook_url": {
                "set": bool(self.discord_webhook_url),
                "required_from_phase": 6,
            },
            # EDINET 有報の事業の内容（テーマタグ段階C・ADR-056）。未設定なら段階C 取得は機能オフ。
            "edinet_api_key": {"set": bool(self.edinet_api_key), "required_from_phase": 7},
        }


settings = Settings()
