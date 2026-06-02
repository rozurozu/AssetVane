# AssetVane backend（FastAPI / Python）
# ARM(ラズパイ) でも動くよう slim ベース。学習・本番ともコンテナで動かす（ADR-021）。
# 本番は別 PC でクロスビルド → ラズパイは pull のみ（ADR-006/021）。本番ステージは後続で追加。
FROM python:3.12-slim

# uv（パッケージ/venv 管理・ADR-023）を公式イメージからコピー。
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# pyright（型チェック・ADR-023）は内部で Node ランタイムを取得して動く。slim には
# libatomic が無く Node が起動できないため入れておく（lint/型チェックを通すための土台）。
RUN apt-get update \
    && apt-get install -y --no-install-recommends libatomic1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 依存だけ先に解決してレイヤキャッシュを効かせる。
# .venv は /app/.venv に作られ、compose で anonymous volume として保護する（ホストを汚さない）。
ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml ./
# uv.lock があれば使い、無ければ解決して生成する（初回ビルドでマウント経由ホストへ書き出す）。
COPY uv.lock* ./
RUN uv sync --no-install-project

# ソースは compose のバインドマウントで持ち込む（dev）。本番ビルドではここで COPY する。
EXPOSE 8000

# dev の起動コマンドは compose 側で上書きする（--reload 付き）。
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
