#!/usr/bin/env bash
# AssetVane デプロイ — Mac(arm64 ネイティブ)ビルド → ghcr.io → ラズパイ pull（ADR-006/021/035）。
#
# GHA は使わない：Apple Silicon Mac が ADR-021 の「別 PC」を兼ね、同一 LAN のラズパイへ ssh 直結する。
# これでクラウドが家庭内ラズパイ（外部非公開・ADR-001）に届かない問題と、x86 ランナーでの QEMU
# エミュ（lightgbm/cvxpy 等が重く失敗しやすい・ADR-021 が警告）を同時に回避する。詳細 docs/deploy.md。
#
# 使い方:
#   ./scripts/deploy.sh                # ビルド→push→ラズパイへデプロイ（backup→pull→up→health→rollback）
#   ./scripts/deploy.sh --build-only   # ビルド→push のみ（ラズパイは触らない）
#
# 前提（初回セットアップは docs/deploy.md）:
#   - ローカルに docker buildx・ghcr.io へ `docker login ghcr.io` 済み。
#   - ラズパイへ ssh 鍵で入れる・ラズパイで `docker login ghcr.io`（read:packages PAT）済み。
#   - ラズパイの ~/$PI_DIR に backend/.env と data/ を配置済み。
set -euo pipefail

cd "$(dirname "$0")/.."

# ローカルのデプロイ設定（PI_HOST 等）は deploy.env に置く（git 管理外・雛形は deploy.env.example）。
# あれば読み込む。ここで設定した変数を下の ${VAR:-既定値} が採用する。
# 優先順位: make deploy VAR=...（実行時の環境変数）＞ deploy.env ＞ 下の既定値。
[ -f ./deploy.env ] && . ./deploy.env

# ---- 設定（deploy.env または実行時の環境変数で上書き可） ----
REGISTRY="${REGISTRY:-ghcr.io/rozurozu}"
PI_HOST="${PI_HOST:-raspberrypi.local}"
# ラズパイのホームからの相対パス（~ を渡すと remote で展開されない罠を避ける）。
PI_DIR="${PI_DIR:-assetvane}"
# frontend に焼き込む本番 API URL（ブラウザから到達できる名前・architecture.md §7.1）。
API_URL="${API_URL:-http://raspberrypi.local:8000}"
PLATFORM="${PLATFORM:-linux/arm64}"

IMAGE_TAG="$(date +%Y%m%d-%H%M%S)"
JJ_ID="$(jj log -r @ --no-graph -T 'change_id.short()' 2>/dev/null || echo unknown)"
BACKEND_IMG="$REGISTRY/assetvane-backend"
FRONTEND_IMG="$REGISTRY/assetvane-frontend"

echo "▶ build & push   tag=$IMAGE_TAG  jj=$JJ_ID  platform=$PLATFORM"

# backend（prod ステージ）。タグはイミュータブルな時刻＋追従用 latest の 2 本。jj id を label に。
docker buildx build --platform "$PLATFORM" --target prod \
  -t "$BACKEND_IMG:$IMAGE_TAG" -t "$BACKEND_IMG:latest" \
  --label "org.opencontainers.image.revision=$JJ_ID" \
  --push ./backend

# frontend（runner ステージ）。NEXT_PUBLIC_API_BASE_URL はビルド時に焼き込む（地雷回避）。
docker buildx build --platform "$PLATFORM" --target runner \
  --build-arg "NEXT_PUBLIC_API_BASE_URL=$API_URL" \
  -t "$FRONTEND_IMG:$IMAGE_TAG" -t "$FRONTEND_IMG:latest" \
  --label "org.opencontainers.image.revision=$JJ_ID" \
  --push ./frontend

echo "✔ pushed  $BACKEND_IMG:$IMAGE_TAG  /  $FRONTEND_IMG:$IMAGE_TAG"

if [ "${1:-}" = "--build-only" ]; then
  echo "↩ --build-only：デプロイはスキップ"
  exit 0
fi

# ---- compose.prod.yaml をラズパイへ同期（Pi が常に最新の定義を持つ） ----
echo "▶ sync compose.prod.yaml → $PI_HOST:~/$PI_DIR/"
ssh "$PI_HOST" "mkdir -p $PI_DIR"
rsync -az ./compose.prod.yaml "$PI_HOST:$PI_DIR/compose.prod.yaml"

# ---- ラズパイ上でデプロイ（backup → pull → up → health → 失敗で自動ロールバック） ----
echo "▶ deploy on $PI_HOST"
ssh "$PI_HOST" IMAGE_TAG="$IMAGE_TAG" PI_DIR="$PI_DIR" bash -s <<'REMOTE'
set -euo pipefail
cd "$HOME/$PI_DIR"
export IMAGE_TAG

# 1) デプロイ前バックアップ（旧コンテナで VACUUM INTO・ADR-017）。初回（未起動）は握りつぶす。
echo "  · backup (VACUUM INTO)"
docker compose -f compose.prod.yaml exec -T backend \
  uv run --no-sync python -m app.scripts.backup "$IMAGE_TAG" \
  || echo "    （バックアップ skip：初回デプロイか旧コンテナ未起動）"

# 2) 新イメージ取得 → 起動（FastAPI 起動時に alembic upgrade head が自動実行・ADR-021）。
echo "  · pull & up   IMAGE_TAG=$IMAGE_TAG"
docker compose -f compose.prod.yaml pull
docker compose -f compose.prod.yaml up -d

# 3) /health を最大 ~30 秒ポーリング。
echo "  · health check"
ok=0
for _ in $(seq 1 15); do
  if curl -fsS "http://localhost:${BACKEND_PORT:-8000}/health" >/dev/null 2>&1; then ok=1; break; fi
  sleep 2
done

if [ "$ok" = "1" ]; then
  echo "$IMAGE_TAG" > .last_good_tag
  echo "  ✔ healthy（.last_good_tag を更新）"
else
  echo "  ✖ unhealthy → ロールバック"
  if [ -f .last_good_tag ]; then
    prev="$(cat .last_good_tag)"
    echo "    · rollback to $prev"
    IMAGE_TAG="$prev" docker compose -f compose.prod.yaml up -d
  else
    echo "    · .last_good_tag が無い（戻り先不明）。手動対応が必要。"
  fi
  exit 1
fi
REMOTE

echo "✔ デプロイ完了: $IMAGE_TAG"
