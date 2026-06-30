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

.PHONY: up down discord-test jquants-test batch-full logs restart reload test lint format deploy deploy-build db-backup db-restore backfill-topix train-ai-alpha

# ===== 運用（dev / Pi 共通・compose 自動判定）=====

up: ## front/back を起動（バックグラウンド・初回はイメージ build/pull も走る）
	$(COMPOSE) up -d

down: ## front/back を停止してコンテナ/ネットワークを削除（data は bind mount なので残る）
	$(COMPOSE) down

discord-test: ## Discord に疎通テストを 1 通送る（冪等回避＝毎回飛ぶ）
	$(COMPOSE) exec backend uv run python -m app.scripts.notify_test

jquants-test: ## J-Quants V2 に認証ピングを 1 発投げる（DB 非依存・ADR-036）
	$(COMPOSE) exec backend uv run python -m app.scripts.jquants_test

batch-full: ## 全銘柄フルバックフィルを 1 回流す（初回投入/復旧・約100〜150分・ADR-036）
	$(COMPOSE) exec backend uv run python -m app.scripts.backfill --nightly

logs: ## backend のログを追う（夜バッチ含む・dev/Pi 自動判定・Ctrl-C で抜ける・ADR-038）
	$(COMPOSE) logs -f backend

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

# ===== dev の DB バックアップ/復元（named volume 化に伴う・2026-06-22）=====
# dev は SQLite を named volume（assetvane-db）に載せるため、ホストから素ファイルとして見えない。
# 旧 bind mount 時の「./data/assetvane.db をそのままコピー」に代わる手段。prod（Pi）は bind mount の
# ままなので /data/backups を直接見られる（ADR-017）。container に sqlite3 CLI が無いので Python で取る。
db-backup: ## dev: named volume の SQLite を一貫スナップショット（VACUUM INTO）でホスト ./backups/ に書き出す
	@mkdir -p backups
	$(COMPOSE) exec -T backend uv run python -c 'import sqlite3; sqlite3.connect("/data/assetvane.db").execute("VACUUM main INTO \x27/data/_backup_tmp.db\x27"); print("snapshot ok")'
	$(COMPOSE) cp backend:/data/_backup_tmp.db backups/assetvane-$(shell date +%Y%m%d-%H%M%S).db
	$(COMPOSE) exec -T backend rm -f /data/_backup_tmp.db
	@echo "✓ backups/ に書き出したのだ"

db-restore: ## dev: ./backups/<FILE> を named volume に書き戻して再起動（make db-restore FILE=assetvane-YYYYmmdd-HHMMSS.db）
	@test -n "$(FILE)" || { echo "✖ FILE=<backups/ 内のファイル名> を指定するのだ"; exit 1; }
	@test -f "backups/$(FILE)" || { echo "✖ backups/$(FILE) が無いのだ"; exit 1; }
	$(COMPOSE) stop backend
	$(COMPOSE) run --rm --no-deps -v $(CURDIR)/backups:/restore backend sh -c 'cp /restore/$(FILE) /data/assetvane.db && rm -f /data/assetvane.db-wal /data/assetvane.db-shm && echo restored'
	$(COMPOSE) up -d backend
	@echo "✓ backups/$(FILE) を復元して再起動したのだ"

# ===== AI Alpha Scorer 学習（別 PC＝開発機のコンテナ内・ADR-006/066）=====
# 学習は「別 PC」でのみ（ADR-006）。開発機の Docker で現用 DB（named volume `assetvane-db`）を
# 読み取り専用で読み（書きロック競合なし・ADR-002）、.pkl を ./models（bind mount でホストの
# backend/models/ に出る）へ焼く。ラズパイ本番（推論のみ）ではこのターゲットを使わない。
# バックアップ吸い出しは不要（現用 volume を直読）。ベンチ TOPIX は Free で取れないため backfill-topix
# で TOPIX ETF を入れてから `make train-ai-alpha ARGS="--bench-symbol 1306.T"`（ml-training.md §1）。
backfill-topix: ## dev: TOPIX 連動 ETF（既定 1306.T）を index_quotes へ＝学習ベンチ（Free で ^TPX 不可の穴埋め）
	$(COMPOSE) run --rm --no-deps backend uv run python -m app.scripts.backfill_topix_benchmark $(ARGS)

train-ai-alpha: ## dev: 現用 DB で AI Alpha を学習し .pkl を models へ（引数: make train-ai-alpha ARGS="--horizon 20"）
	$(COMPOSE) run --rm --no-deps backend uv run python -m app.scripts.train_ai_alpha $(ARGS)

deploy: ## Mac で arm64 ビルド → ghcr.io → ラズパイへデプロイ
	@test -f compose.yaml || { echo "✖ deploy は本番（Pi）では不要なのだ。Mac から実行するのだ。"; exit 1; }
	./scripts/deploy.sh

deploy-build: ## ビルド→push のみ（ラズパイは触らない）
	@test -f compose.yaml || { echo "✖ deploy-build は本番（Pi）では不要なのだ。Mac から実行するのだ。"; exit 1; }
	./scripts/deploy.sh --build-only
