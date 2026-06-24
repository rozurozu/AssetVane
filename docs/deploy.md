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
3. **デプロイ専用 ssh 鍵**を作る（個人ログイン鍵とは分ける）:
   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/assetvane_deploy -C "assetvane-deploy"
   ```
4. `~/.ssh/config` に **deploy ユーザー＋専用鍵**のエイリアスを書く:
   ```ssh-config
   Host assetvane-pi
       HostName raspberrypi.local      # or IP
       User deploy
       IdentityFile ~/.ssh/assetvane_deploy
       IdentitiesOnly yes
   ```
   `deploy.env` には `PI_HOST=assetvane-pi` と書けば、ユーザー/IP/鍵は ssh config 側で吸収される。

### ラズパイ

1. **Docker / docker compose v2** を入れる。**USB SSD ブート推奨**（SD の I/O・寿命対策＝[ADR-021](decisions.md)/[ADR-017](decisions.md)）。

2. **デプロイ専用ユーザー `deploy` を作る**（個人ログインと分離・鍵のみ・パスワード無効）:
   ```bash
   sudo adduser --disabled-password --gecos "" deploy
   sudo usermod -aG docker deploy          # docker のみ（sudo は付けない・docker 経由で完結する）
   # Mac で作った deploy 専用鍵の .pub を登録
   sudo -u deploy mkdir -p /home/deploy/.ssh && sudo -u deploy chmod 700 /home/deploy/.ssh
   sudo -u deploy tee /home/deploy/.ssh/authorized_keys < /tmp/assetvane_deploy.pub >/dev/null
   sudo -u deploy chmod 600 /home/deploy/.ssh/authorized_keys
   ```
   > ⚠️ **docker グループ所属 ≒ root**（コンテナにホスト `/` をマウントして昇格できる）。よって権限の縮減効果は限定的で、分ける主目的は **鍵の分離・所有権の明確化・個人アカウントを自動化に使わない**こと。真に隔離したいなら rootless Docker（家庭内 LAN・単一ユーザー＝ADR-001 なら過剰）。

3. **ghcr.io へログイン**（deploy ユーザーで・private package を pull するため・`read:packages` の PAT）:
   ```bash
   sudo -u deploy -i
   echo <PAT> | docker login ghcr.io -u <github-user> --password-stdin
   exit
   ```

4. **デプロイ先 `/opt/assetvane` を作って deploy 所有にする**（ホーム配下に置かない＝FHS・アカウントから切り離す）:
   ```bash
   sudo mkdir -p /opt/assetvane/data /opt/assetvane/backend/models
   sudo chown -R deploy:deploy /opt/assetvane
   ```

5. **`backend/.env`（秘密）を用意する**。初回はラズパイに存在しないので自分で置く（J-Quants/LLM/Discord キー・リポジトリには入れない＝ADR-005）。どちらかで:
   - **ラズパイで直に書く**（本番だけキーを変えるなら）:
     ```bash
     sudo -u deploy nano /opt/assetvane/backend/.env          # 中身を貼って保存
     sudo chmod 600 /opt/assetvane/backend/.env
     ```

   > ℹ️ **CORS / API_URL の設定は不要になった**（[ADR-037](decisions.md) で同一オリジン化）。ブラウザは
   > frontend の `/api` だけを叩き、Next の rewrites が裏で内部 DNS `backend:8000` へ転送するため、
   > 「ブラウザで開く URL」「焼き込み API_URL」「CORS」の **3 ホスト一致を気にする必要が無い**。Pi の
   > IP/mDNS が何であろうと同じイメージ・無設定で動く（DHCP で IP が変わっても再発しない）。`backend/.env`
   > に必要なのは秘密情報（J-Quants/LLM/Discord キー）だけ。変更後は `make reload`（コンテナ再生成で
   > `.env` を読み直す）で反映する。`make restart` は停止→起動のみで `.env` を読み直さないので注意。

   構成（`compose.prod.yaml` は deploy.sh が毎回 rsync する）:
   ```
   /opt/assetvane/
     compose.prod.yaml      # deploy.sh が rsync（初回は手動でも可）
     backend/.env           # 秘密。chmod 600
     backend/models/        # Phase 5 推論モデル（.pkl・空でよい）
     data/                  # SQLite と backups/。空でよい（初回起動で作られる）
     .codex/auth.json       # （任意）codex provider 用。開発機で codex login して置く・chmod 600
   ```
   - `data/` は bind mount。`backend/.env` は `compose.prod.yaml` の `env_file` が読む。`compose.prod.yaml` の相対 volume（`./data` 等）は `/opt/assetvane` 基準で解決される。

6. **（任意）AI を codex で動かすなら `auth.json` を置く**（[ADR-032](decisions.md)）。codex CLI は backend イメージに同梱済み（Node 不要の musl バイナリ・rust-v0.137.0）なので**ラズパイに codex を入れる必要はない**。必要なのは login 済みの `auth.json` だけ。codex login は ChatGPT のブラウザ認証が要るので、**ヘッドレスなラズパイでは行わず開発機（Mac）で済ませて運ぶ**:
   ```bash
   # 開発機（Mac）で。ラズパイには codex バイナリもブラウザも要らない
   codex login                                              # ~/.codex/auth.json が生成される
   ssh assetvane-pi 'mkdir -p /opt/assetvane/.codex'        # /opt/assetvane は deploy 所有なので sudo 不要
   scp ~/.codex/auth.json assetvane-pi:/opt/assetvane/.codex/auth.json
   ssh assetvane-pi 'chmod 600 /opt/assetvane/.codex/auth.json'
   ```
   その後 **`/settings` の「面別 LLM 割当」で対象面の provider に「codex」を選ぶ**（model は空なら `CODEX_MODEL`）。codex のプロセス設定（`CODEX_MODEL=gpt-5.5` 等）だけ `backend/.env` に残し `make reload`。`compose.prod.yaml` が `./.codex` を `/root/.codex` に **read-write** マウントする。
   > ⚠️ `auth.json` はトークンリフレッシュで **codex 自身が書き換える**（だから read-write）。毎晩 nightly/chat で codex を動かしていればリフレッシュされ続け実用上は失効しにくいが、**長期放置すると失効**する。失効すると codex 呼び出しが失敗して [ADR-018](decisions.md) で Discord 通知に出るので、気づいたら開発機で `codex login` し直して再コピーする。無人 cron での継続が未実証のため、夜間面（nightly）は当面 openai provider 推奨（[ADR-032](decisions.md)/[ADR-058](decisions.md)）。

   > ⚠️ **LLM の初回設定（[ADR-058](decisions.md)）**: provider/api_key/model は env ではなく DB に保存し `/settings` で登録する。**migration（`alembic upgrade head`／起動時 `init_db`）後、`/settings` で provider を登録するまで鍵あり面（chat/nightly/dossier/tagger）の LLM は動かない**（chat は 503・夜間/ドシエは通知付き skip・タグ付けは沈黙 skip）。codex は鍵なし組み込みなので面に割り当てれば最初から使える。

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
make deploy PI_HOST=pi@192.168.1.50
```

優先順位は **実行時の環境変数 ＞ `deploy.env` ＞ スクリプト既定値**。

| 変数 | 既定 | 意味 |
|---|---|---|
| `REGISTRY` | `ghcr.io/rozurozu` | イメージの置き場 |
| `PI_HOST` | `raspberrypi.local` | ラズパイの ssh ホスト（`~/.ssh/config` のエイリアス推奨＝`assetvane-pi`）|
| `PI_DIR` | `/opt/assetvane` | ラズパイ上のデプロイ先（絶対パス）|
| `PLATFORM` | `linux/arm64` | ビルドターゲット |

> `API_URL` は [ADR-037](decisions.md) の同一オリジン化で**廃止**（frontend は backend のホストを知らず、Next の rewrites が内部 DNS `backend:8000` へ転送する）。Pi ごとの URL 指定は不要。

ビルドだけ（ラズパイを触らない）:

```bash
make deploy-build      # = ./scripts/deploy.sh --build-only
```

---

## ロールバック

`/health` 失敗時は `scripts/deploy.sh` が **自動で** `.last_good_tag`（ラズパイの `/opt/assetvane/.last_good_tag`）の前タグへ戻す。手動で戻す場合:

```bash
ssh assetvane-pi
cd /opt/assetvane
IMAGE_TAG=<戻したいタグ> docker compose -f compose.prod.yaml up -d
```

過去タグは ghcr の package ページ、または直近正常タグは `.last_good_tag` で確認できる。

---

## バックアップと復元

- **デプロイのたび**、`up` の前に `backups/assetvane-<IMAGE_TAG>.db` が `VACUUM INTO` で作られる（`backend/app/scripts/backup.py`・直近 10 世代を保持・[ADR-017](decisions.md)）。手入力の一点もの（`policy`/`transactions`/`holdings`/`cash`/`advisor_journal`）が消えないための保険。
- **手動バックアップ**:
  ```bash
  ssh assetvane-pi
  cd /opt/assetvane
  docker compose -f compose.prod.yaml exec backend uv run --no-sync python -m app.scripts.backup
  ```
- **復元**（マイグレーション失敗等で壊れたとき。**一点ものなので自動上書きはしない**＝手動）:
  ```bash
  cd /opt/assetvane
  docker compose -f compose.prod.yaml down
  # data/ 配下はコンテナが root で作るため、ホスト直の cp は sudo が要る（home 配下時代と同じ）。
  sudo cp data/backups/assetvane-<タグ>.db data/assetvane.db   # WAL/SHM が残るなら退避してから
  docker compose -f compose.prod.yaml up -d
  ```

---

## ログの見方（ADR-038）

障害の一次情報はコンテナの stdout/stderr に出る（アプリは FileHandler を持たない＝[ADR-038](decisions.md)）。

```bash
# backend のログを追う（夜バッチ含む・dev/Pi 自動判定）。これが手早い
make logs

# 素で叩くなら（テキスト形式 `時刻 LEVEL ロガー名: メッセージ`）
docker compose -f compose.prod.yaml logs -f backend
# frontend も同様
docker compose -f compose.prod.yaml logs -f frontend
```

- **詳細を出したい**: `backend/.env` に `LOG_LEVEL=DEBUG` を足して `make reload`（root レベルが DEBUG に上がる。既定は `INFO`）。切り分けが済んだら戻す。
- **`/health` の access ログは出ない**: 定期ヘルスチェックで埋もれないよう抑制してある（[ADR-038](decisions.md)）。backend が応答しているかは `/health` を直に叩いて確認する（`exec frontend wget -qO- http://backend:8000/health`）。
- **ログが消える/古い分が無い**: docker の json-file ローテーション（`compose*.yaml` の `logging:` ＝ `max-size: 10m` × `max-file: 5`）で **1 サービスあたり最大 50MB** に頭打ちし、古いものから破棄する（SD カードの I/O・寿命対策＝[ADR-017](decisions.md)）。それ以前のログは残らない。常時残したい失敗は Discord 通知（[ADR-018](decisions.md)）側で拾う。

## トラブルシュート

- **pull で unauthorized**: ラズパイ/ローカルの `docker login ghcr.io`（PAT 権限・期限）を確認。private package には `read:packages` が要る。
- **画面に「backend 未接続」バッジが出る**: バッジはブラウザから `/api/health` への fetch が失敗すると出る（`Topbar`）。[ADR-037](decisions.md) の同一オリジン化以降、これは **frontend(Next) → backend の rewrites 転送が通っていない**ことを意味する（CORS/焼き込みの 3 ホスト一致地雷は無くなった）。切り分け（ブラウザ DevTools の Network で `/api/health` の Request URL と Status を見る）:
  - **frontend 自体に繋がらない**（ページが開けない・`/api/health` がそもそも飛ばない）: frontend コンテナが落ちている。`docker compose -f compose.prod.yaml ps` / `logs frontend` を確認。
  - **`/api/health` が 502/504**（frontend は応答するが API が返らない）: rewrites の転送先 backend に届いていない。**backend コンテナの健全性**（`logs backend`・`exec frontend wget -qO- http://backend:8000/health`）と、frontend コンテナから内部 DNS `backend` が引けるか（同一 compose network にいるか）を確認。マイグレーション失敗で backend が起動途中に落ちている場合もここに出る。
  - **古いイメージが残っている**（旧 `NEXT_PUBLIC_API_BASE_URL` 焼き込み版が動いている）: `make deploy` で作り直し、`make reload`。DevTools の Network で `/api/health` の Request URL を見て、`/api/...` 相対でなく `host:8000` 直なら**古い焼き込みイメージ**が動いている＝`make deploy`（焼き直し）→`make reload`。
- **arm64 になっているか確認**:
  ```bash
  docker buildx imagetools inspect ghcr.io/rozurozu/assetvane-backend:<タグ>
  ```
- **マイグレーションで起動が落ちる**: `up -d` 後に `docker compose -f compose.prod.yaml logs backend` を確認。事前バックアップから復元（上記）。
- **無人運用の失敗通知**: 夜間バッチの失敗は `DISCORD_WEBHOOK_URL` へ通知される（[ADR-018](decisions.md)）。デプロイ自体の失敗はスクリプトの終了コードで気づく。
- **EDINET 取得失敗が通知されたら `backfill_edinet` を回す**: `fetch_edinet_descriptions`（段階C・[ADR-056](decisions.md)）は**失敗時も提出日カーソルを前進させる**設計（liveness 優先＝1 日の取りこぼしで以降を止めない）。このため夜間運用だけだと、失敗した提出日の有報「事業の内容」は**次の訂正/再提出まで欠落**し続ける。Discord に EDINET 取得失敗が出たら、その提出日あたりから `backfill_edinet`（提出日クロール型・冪等 UPSERT・中断再開可）で取り直す:
  ```bash
  # 失敗が出た提出日あたりを開始日に明示（--from は約15ヶ月窓より優先）。LLM コストが出るので
  # まず --limit で件数 cap を掛けて試走し、見積もってから全量を回す。
  docker compose -f compose.prod.yaml exec backend \
    uv run python -m app.scripts.backfill_edinet --from 2026-06-10 --limit 50
  ```
  既存の `company_descriptions(JP,'dossier')`（調査済み）は事前 skip ＋ `source!='dossier'` ガードの 2 段で**上書きしない**（dossier 優先・[ADR-056](decisions.md)）。`docTypeCode=120` のみ対象（訂正 130 は対象外）。
