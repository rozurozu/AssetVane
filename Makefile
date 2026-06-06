# AssetVane — 便利コマンド集（ADR-011「1つの脳・複数の起動口」／ADR-035 デプロイ）。
# 設定は環境変数で上書きできる（例: make deploy PI_HOST=pi.local API_URL=http://pi.local:8000）。
#
# compose ファイルは dev と Pi 本番で名前が違うため自動判定する:
#   - dev      … リポジトリに compose.yaml があるので docker compose（既定）
#   - Pi 本番  … deploy で compose.prod.yaml だけが配られるので -f で明示
# これで「運用」ターゲットは dev でも Pi に ssh しても文字通り同じコマンドで動く。
ifeq ($(wildcard compose.yaml),)
  COMPOSE := docker compose -f compose.prod.yaml
else
  COMPOSE := docker compose
endif

.PHONY: discord-test test lint format deploy deploy-build

# ===== 運用（dev / Pi 共通・compose 自動判定）=====

discord-test: ## Discord に疎通テストを 1 通送る（冪等回避＝毎回飛ぶ）
	$(COMPOSE) exec backend uv run python -m app.scripts.notify_test

# ===== 開発・デプロイ（Mac 専用）=====

test: ## backend テスト（一時 SQLite・本DB は触らない）
	cd backend && uv run pytest -q

lint: ## backend lint（Ruff・ADR-023）
	cd backend && uv run ruff check .

format: ## backend format（Ruff・ADR-023）
	cd backend && uv run ruff format .

deploy: ## Mac で arm64 ビルド → ghcr.io → ラズパイへデプロイ
	@test -f compose.yaml || { echo "✖ deploy は本番（Pi）では不要なのだ。Mac から実行するのだ。"; exit 1; }
	./scripts/deploy.sh

deploy-build: ## ビルド→push のみ（ラズパイは触らない）
	@test -f compose.yaml || { echo "✖ deploy-build は本番（Pi）では不要なのだ。Mac から実行するのだ。"; exit 1; }
	./scripts/deploy.sh --build-only
