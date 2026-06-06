# AssetVane — デプロイの薄いラッパ（実体は scripts/deploy.sh・ADR-035）。
# 設定は環境変数で上書きできる（例: make deploy PI_HOST=pi.local API_URL=http://pi.local:8000）。
.PHONY: deploy deploy-build

deploy: ## Mac で arm64 ビルド → ghcr.io → ラズパイへデプロイ
	./scripts/deploy.sh

deploy-build: ## ビルド→push のみ（ラズパイは触らない）
	./scripts/deploy.sh --build-only
