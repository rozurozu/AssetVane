# デプロイ運用手順（ラズパイ本番）

AssetVane を本番（ラズパイ 4B・aarch64・家庭内 LAN）へ配る手順。設計の「なぜ」は
[ADR-035](decisions.md)（GHA 不採用・Mac ローカルビルド→ghcr→ssh デプロイ）・
[ADR-021](decisions.md)（別 PC クロスビルド→ラズパイ pull）・[ADR-006](decisions.md)・
[ADR-017](decisions.md)（バックアップ）を参照。

> 要約: **Apple Silicon Mac で `linux/arm64` をネイティブビルド → `ghcr.io` に push →
> 同一 LAN のラズパイへ `ssh` で `compose pull → up`**。1 コマンド `make deploy`（実体は
> `scripts/deploy.sh`）。GitHub Actions は使わない。

---

## 全体像

```
[Mac（Apple Silicon＝ADR-021 の「別 PC」）]
  scripts/deploy.sh
   ├─ docker buildx --platform linux/arm64 --target prod      （backend）
   ├─ docker buildx --platform linux/arm64 --target runner    （frontend・NEXT_PUBLIC を焼き込み）
   ├─ ghcr.io/rozurozu/assetvane-{backend,frontend}:<tag> へ push
   ├─ rsync compose.prod.yaml → ラズパイ
   └─ ssh ラズパイ:
        1. VACUUM INTO バックアップ（旧コンテナ・ADR-017）
        2. docker compose -f compose.prod.yaml pull
        3. up -d（FastAPI 起動で alembic upgrade head 自動実行）
        4. /health ポーリング → 失敗なら .last_good_tag で自動ロールバック
```

タグは `YYYYMMDD-HHMMSS`（イミュータブル）＋ `latest`。jj の change-id は OCI label に焼く。

---

## 初回セットアップ

### ローカル（Mac）

1. **docker buildx** が使えること（Docker Desktop / colima で arm64 が焼けること）。
2. **ghcr.io へログイン**（`write:packages` 権限の PAT）:
   ```bash
   echo <PAT> | docker login ghcr.io -u <github-user> --password-stdin
   ```
3. ラズパイへ **ssh 鍵**で入れること（`ssh raspberrypi.local` がパスワード無しで通る）。

### ラズパイ

1. **Docker / docker compose v2** を入れる。**USB SSD ブート推奨**（SD の I/O・寿命対策＝[ADR-021](decisions.md)/[ADR-017](decisions.md)）。
2. **ghcr.io へログイン**（private package を pull するため・`read:packages` の PAT）:
   ```bash
   echo <PAT> | docker login ghcr.io -u <github-user> --password-stdin
   ```
3. デプロイ先ディレクトリと配置物（ホーム直下 `~/assetvane`）:
   ```
   ~/assetvane/
     compose.prod.yaml      # deploy.sh が毎回 rsync するが初回は手動でも可
     backend/.env           # 秘密（J-Quants/LLM/Discord キー）。リポジトリには入れない（ADR-005）
     data/                  # SQLite と backups/。空でよい（初回起動で作られる）
   ```
   - `data/` は named volume 相当の bind。`backend/.env` は `compose.prod.yaml` の `env_file` が読む。

---

## 通常のデプロイ

Mac のリポジトリ直下で:

```bash
make deploy
# または直接:
./scripts/deploy.sh
```

設定は **`deploy.env`（git 管理外）** に置くのが基本。雛形をコピーして埋める:

```bash
cp deploy.env.example deploy.env   # PI_HOST / API_URL 等を書く
```

`deploy.env` は `scripts/deploy.sh` が起動時に読み込む。1 回限りの上書きは実行時の環境変数で:

```bash
make deploy PI_HOST=pi@192.168.1.50 API_URL=http://192.168.1.50:8000
```

優先順位は **実行時の環境変数 ＞ `deploy.env` ＞ スクリプト既定値**。

| 変数 | 既定 | 意味 |
|---|---|---|
| `REGISTRY` | `ghcr.io/rozurozu` | イメージの置き場 |
| `PI_HOST` | `raspberrypi.local` | ラズパイの ssh ホスト |
| `PI_DIR` | `assetvane` | ラズパイのホーム相対のデプロイ先 |
| `API_URL` | `http://raspberrypi.local:8000` | **frontend に焼き込む**本番 API URL |
| `PLATFORM` | `linux/arm64` | ビルドターゲット |

ビルドだけ（ラズパイを触らない）:

```bash
make deploy-build      # = ./scripts/deploy.sh --build-only
```

---

## ロールバック

`/health` 失敗時は `scripts/deploy.sh` が **自動で** `.last_good_tag`（ラズパイの `~/assetvane/.last_good_tag`）の前タグへ戻す。手動で戻す場合:

```bash
ssh raspberrypi.local
cd ~/assetvane
IMAGE_TAG=<戻したいタグ> docker compose -f compose.prod.yaml up -d
```

過去タグは ghcr の package ページ、または直近正常タグは `.last_good_tag` で確認できる。

---

## バックアップと復元

- **デプロイのたび**、`up` の前に `backups/assetvane-<IMAGE_TAG>.db` が `VACUUM INTO` で作られる（`backend/app/scripts/backup.py`・直近 10 世代を保持・[ADR-017](decisions.md)）。手入力の一点もの（`policy`/`transactions`/`holdings`/`cash`/`advisor_journal`）が消えないための保険。
- **手動バックアップ**:
  ```bash
  ssh raspberrypi.local
  cd ~/assetvane
  docker compose -f compose.prod.yaml exec backend uv run --no-sync python -m app.scripts.backup
  ```
- **復元**（マイグレーション失敗等で壊れたとき。**一点ものなので自動上書きはしない**＝手動）:
  ```bash
  cd ~/assetvane
  docker compose -f compose.prod.yaml down
  cp data/backups/assetvane-<タグ>.db data/assetvane.db   # WAL/SHM が残るなら退避してから
  docker compose -f compose.prod.yaml up -d
  ```

---

## トラブルシュート

- **pull で unauthorized**: ラズパイ/ローカルの `docker login ghcr.io`（PAT 権限・期限）を確認。private package には `read:packages` が要る。
- **frontend が API を見失う**: `NEXT_PUBLIC_API_BASE_URL` はビルド時に焼き込まれる。`API_URL` を正しい値にして**ビルドし直す**（実行時 env では変わらない＝[architecture.md §7.1](architecture.md)）。
- **arm64 になっているか確認**:
  ```bash
  docker buildx imagetools inspect ghcr.io/rozurozu/assetvane-backend:<タグ>
  ```
- **マイグレーションで起動が落ちる**: `up -d` 後に `docker compose -f compose.prod.yaml logs backend` を確認。事前バックアップから復元（上記）。
- **無人運用の失敗通知**: 夜間バッチの失敗は `DISCORD_WEBHOOK_URL` へ通知される（[ADR-018](decisions.md)）。デプロイ自体の失敗はスクリプトの終了コードで気づく。
