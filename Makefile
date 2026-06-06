# AssetVane — 便利コマンド集（ADR-011「1つの脳・複数の起動口」／ADR-035 デプロイ）。
# 設定は環境変数で上書きできる（例: make deploy PI_HOST=pi.local）。
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

# Pi 本番の compose.prod.yaml は image タグに ${IMAGE_TAG:?...} を要求するため、IMAGE_TAG が
# 環境に無いと restart/exec/up すべてが「IMAGE_TAG required」で落ちる。ssh して運用コマンドを叩く
# ときは環境に無いので、直近正常タグ（.last_good_tag・deploy.sh が記録）から補う。コマンドラインや
# 環境で渡せば（make reload IMAGE_TAG=...）そちらが優先。dev の compose.yaml は IMAGE_TAG を
# 参照しないので空でも無害。
IMAGE_TAG ?= $(shell cat .last_good_tag 2>/dev/null)
export IMAGE_TAG

.PHONY: discord-test jquants-test batch-full restart reload test lint format deploy deploy-build

# ===== 運用（dev / Pi 共通・compose 自動判定）=====

discord-test: ## Discord に疎通テストを 1 通送る（冪等回避＝毎回飛ぶ）
	$(COMPOSE) exec backend uv run python -m app.scripts.notify_test

jquants-test: ## J-Quants V2 に認証ピングを 1 発投げる（DB 非依存・ADR-036）
	$(COMPOSE) exec backend uv run python -m app.scripts.jquants_test

batch-full: ## 全銘柄フルバックフィルを 1 回流す（初回投入/復旧・約100〜150分・ADR-036）
	$(COMPOSE) exec backend uv run python -m app.scripts.backfill --nightly

# restart と reload の違い（front/back 両方が対象）:
#   restart … `docker compose restart`。既存コンテナを止めて起こすだけ。速い・普段使い。
#             コンテナは作り直さないので、image も backend/.env も読み直さない（env は生成時に焼かれる）。
#   reload  … `docker compose up -d --force-recreate`。コンテナを作り直す。backend/.env を変えた
#             （DISCORD_WEBHOOK_URL 等）ときはこちらでないと反映されない。少し遅い。
# 「ただ再起動したい」は restart、「.env を変えたから効かせたい」は reload を使う。
restart: ## front/back 両方を普通に再起動（停止→起動のみ・速い・.env は読み直さない）
	$(COMPOSE) restart

reload: ## front/back 両方を作り直して再起動（backend/.env の変更を反映する）
	$(COMPOSE) up -d --force-recreate

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
