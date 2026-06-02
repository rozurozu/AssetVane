# AssetVane frontend（Next.js / Node）
# dev はコンテナ内で npm ci → next dev --turbopack（ADR-022）。
# 本番は standalone output の multi-stage に差し替える（ADR-021/022）。後続で追加。
FROM node:24-slim

WORKDIR /app

# 依存だけ先に入れてレイヤキャッシュを効かせる。
# node_modules は compose で anonymous volume として保護する（ホストを汚さない）。
# lockfile を正本として npm ci で決定的に入れる（dev/prod parity・ADR-021）。
# package-lock.json はコミット対象。無いと npm ci は失敗する（= 正しく気づける）。
COPY package.json package-lock.json ./
RUN npm ci

# ソースは compose のバインドマウントで持ち込む（dev）。本番ビルドではここで COPY する。
EXPOSE 3000

# dev の起動コマンドは compose 側で上書きする。
CMD ["npm", "run", "dev"]
