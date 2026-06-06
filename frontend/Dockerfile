# AssetVane frontend（Next.js / Node）— マルチステージ（ADR-021/022）。
#   dev    : コンテナ内 npm ci → next dev --turbopack。ソースは bind mount（compose.yaml）。
#   deps   : 本番依存を npm ci で決定的に入れる。
#   builder: standalone をビルド。NEXT_PUBLIC_* はビルド時に bundle へ焼き込まれるため
#            build-arg で本番 API URL を渡す（architecture.md §7.1 の落とし穴・scripts/deploy.sh）。
#   runner : standalone 成果物だけを載せた最小ランタイム（常駐メモリ最小・ADR-021）。
# 本番は別 PC（Apple Silicon Mac）で arm64 ビルド → ghcr.io → ラズパイは pull のみ（ADR-006/021・ADR-035）。

# ---- dev: 依存だけ先に入れてレイヤキャッシュを効かせる。ソースは bind mount（compose.yaml）。----
# package-lock.json はコミット対象。無いと npm ci は失敗する（= 正しく気づける・ADR-021）。
FROM node:24-slim AS dev
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
EXPOSE 3000
# dev の起動コマンドは compose 側で上書きする。
CMD ["npm", "run", "dev"]

# ---- deps: 本番依存を決定的に入れる ----
FROM node:24-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# ---- builder: standalone をビルド（NEXT_PUBLIC_API_BASE_URL を焼き込む） ----
FROM node:24-slim AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ARG NEXT_PUBLIC_API_BASE_URL
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL
# public/ が無くても standalone の COPY が落ちないよう空で用意してからビルドする。
# build は webpack（dev のみ Turbopack・ADR-022）。next.config の output:"standalone" 前提。
RUN mkdir -p public && npm run build

# ---- runner: standalone 成果物だけを載せた最小ランタイム（next start ではなく Node 直起動）----
FROM node:24-slim AS runner
WORKDIR /app
ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public
EXPOSE 3000
# standalone は server.js を吐く（常駐メモリ最小・ADR-021）。
CMD ["node", "server.js"]
