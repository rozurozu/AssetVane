# AssetVane backend（FastAPI / Python）— マルチステージ（ADR-021）。
#   base : 共通土台（uv・libatomic）。
#   dev  : 依存のみ解決。ソースは compose のバインドマウントで持ち込み --reload（compose.yaml）。
#   prod : 依存（dev グループ除外）＋ソースを焼き込み、--reload なしで起動（compose.prod.yaml）。
# 本番は別 PC（Apple Silicon Mac）で arm64 ネイティブビルド → ghcr.io → ラズパイは pull のみ
# （ADR-006/021・ARM ネイティブビルド回避＝scripts/deploy.sh・docs/deploy.md・ADR-035）。
FROM python:3.12-slim AS base

# uv（パッケージ/venv 管理・ADR-023）を公式イメージからコピー。
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# pyright（型チェック・ADR-023）は内部で Node ランタイムを取得して動く。slim には
# libatomic が無く Node が起動できないため入れておく（lint/型チェックを通すための土台）。
# libgomp1 は LightGBM（AI Alpha Scorer・Phase 5）の OpenMP ランタイム（libgomp.so.1）。
# slim には無く、無いと `import lightgbm` が OSError で落ちる。dev の学習（make train-ai-alpha・
# ADR-066）と prod の推論（score_ai_alpha が .pkl をロード→predict・ADR-006）の双方で要るので
# base に焼く（pyproject の「ARM ゲート」コメントが指す system 依存の実体）。
# ca-certificates は外部 HTTPS（J-Quants / ニュース / LLM API / yfinance 等）の TLS 検証に要る。
RUN apt-get update \
    && apt-get install -y --no-install-recommends libatomic1 libgomp1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# .venv は /app/.venv に作られる。dev は anonymous volume で保護、prod は焼き込む。
ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# ---- dev: 依存だけ先に解決してレイヤキャッシュを効かせる。ソースは bind mount（compose.yaml）。----
FROM base AS dev
COPY pyproject.toml ./
# uv.lock があれば使い、無ければ解決して生成する（初回ビルドでマウント経由ホストへ書き出す）。
COPY uv.lock* ./
RUN uv sync --no-install-project
EXPOSE 8000
# dev の起動コマンドは compose 側で上書きする（--reload 付き）。
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ---- prod: 依存＋ソースを焼き込み、--reload なしで起動。----
FROM base AS prod
# 依存を先に解決してレイヤキャッシュを効かせる（ソース変更だけでは再 install しない）。
# uv.lock を正本に --frozen で決定的に入れる（dev/prod parity・ADR-021）。
# あえて --no-dev は使わない: ランタイム依存が dev グループ経由でしか満たされていないケースがあり
# （pyportfolioopt→skbase→packaging が pytest 経由でしか入っていなかった＝2026-06-06 に prod 起動で判明）、
# --no-dev だと取りこぼして起動時に ModuleNotFoundError で落ちる。dev ツール（ruff/pyright）は
# pure-python で軽く無害なので、parity を優先して dev と同じ依存集合を焼く。
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project
# ソースを焼き込む（dev のバインドマウント相当）。.dockerignore で .venv/.env/__pycache__ は除外済み。
COPY . .
RUN uv sync --frozen
EXPOSE 8000
# 本番は APScheduler 同居の夜間バッチを起動する（compose.prod で BATCH_SCHEDULER_ENABLED=true）。
# --no-sync 必須: 付けないと `uv run` が起動時に再 sync して dev 依存（pyright 等）を引き込み、
# 無人ラズパイの起動時にネットを要求してしまう（--no-dev ビルドの意味が消える）。既存 .venv をそのまま使う。
CMD ["uv", "run", "--no-sync", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
