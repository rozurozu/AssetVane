"use client";

import { Card } from "@/components/ui/Card";
import { StatusBlock } from "@/components/ui/StatusBlock";
import {
  type BatchStatusResponse,
  type HealthResponse,
  getBatchStatus,
  getHealth,
  runBatch,
  runEdinetDifferential,
  sendDiscordTest,
  sendJquantsTest,
  stopBatch,
} from "@/lib/api";
import { useApi } from "@/lib/use-api";
import { useEffect, useState } from "react";

// Settings 画面（screens.md #14・phase6-spec §6・ADR-005/007/036）。
// 通知は backend（Discord）が送るので UI は最小: backend 健全性（/health）の env 詳細表示 ＋
// 夜間バッチ手動起動（差分/全銘柄フル）＋進捗ポーリング＋停止（ADR-036）＋ 外部依存の疎通テスト
// （Discord / J-Quants）。Webhook URL・しきい値は .env 固定で UI から編集しない（L-25）。

// env キーの日本語ラベル（順序固定・config.py env_status のキーに対応）。
const ENV_LABELS: { key: string; label: string }[] = [
  { key: "jquants_api_key", label: "J-Quants API キー" },
  { key: "llm_api_key", label: "LLM API キー" },
  { key: "discord_webhook_url", label: "Discord Webhook URL（通知）" },
  { key: "edinet_api_key", label: "EDINET API キー（テーマタグ段階C）" },
];

/** started_at（ISO8601・UTC）からの経過分。未走行・解析不能は null。 */
function elapsedMin(startedAt: string | null): number | null {
  if (!startedAt) return null;
  const t = Date.parse(startedAt);
  if (Number.isNaN(t)) return null;
  return Math.max(0, Math.floor((Date.now() - t) / 60000));
}

export default function SettingsPage() {
  const { data, error, loading } = useApi<HealthResponse>((signal) => getHealth(signal), []);

  // バッチ実行状態をポーリングして「実行中・今どのジョブ・経過」を映す（ADR-036）。
  // cron・/batch/run・CLI --nightly のどの口で走っていても同じ状態を見られる（ADR-011）。
  const [batchStatus, setBatchStatus] = useState<BatchStatusResponse | null>(null);
  useEffect(() => {
    let active = true;
    const tick = async () => {
      try {
        const s = await getBatchStatus();
        if (active) setBatchStatus(s);
      } catch {
        // 一時的な取得失敗は無視（次の tick で回復する）。
      }
    };
    tick();
    const id = setInterval(tick, 3000);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, []);

  // 手動バッチ起動（Dashboard と同じ流儀・mutation 起点は useState＝rule (c)）。
  const [fullBackfill, setFullBackfill] = useState(false);
  const [batchBusy, setBatchBusy] = useState(false);
  const [stopBusy, setStopBusy] = useState(false);
  const [batchNote, setBatchNote] = useState<string | null>(null);

  async function onRunBatch() {
    // 全銘柄フルは稀・重い（約100〜150分）ので誤クリック防止に確認ダイアログを挟む（grill-me 合意）。
    if (fullBackfill && !window.confirm("全銘柄フル取得は約100〜150分かかるのだ。実行していい？")) {
      return;
    }
    setBatchBusy(true);
    setBatchNote(null);
    try {
      await runBatch(fullBackfill);
      setBatchNote(
        fullBackfill
          ? "全銘柄フル取得を起動したのだ。下の進捗で追えるのだ。"
          : "差分バッチを起動したのだ。下の進捗で追えるのだ。",
      );
    } catch (e) {
      // 409（実行中）も ApiError の message で拾って表示する。
      setBatchNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBatchBusy(false);
    }
  }

  async function onStopBatch() {
    setStopBusy(true);
    try {
      const r = await stopBatch();
      setBatchNote(
        r.stopping
          ? "停止を要求したのだ。今のジョブが終わり次第止まるのだ。"
          : "実行中のバッチは無いのだ。",
      );
    } catch (e) {
      setBatchNote(e instanceof Error ? e.message : String(e));
    } finally {
      setStopBusy(false);
    }
  }

  // EDINET 差分タグ付け（テーマタグ段階C・ADR-056）。夜間と同じ差分を run_jobs で 1 回回す。
  // 進捗は上のバッチ状態ポーリングに相乗りで映る（同じ state・ADR-011/036）。
  const [edinetBusy, setEdinetBusy] = useState(false);
  const [edinetNote, setEdinetNote] = useState<string | null>(null);

  async function onRunEdinet() {
    setEdinetBusy(true);
    setEdinetNote(null);
    try {
      await runEdinetDifferential();
      setEdinetNote("EDINET 差分タグ付けを起動したのだ。下の夜間バッチ進捗で追えるのだ。");
    } catch (e) {
      // 409（実行中）も ApiError の message で拾って表示する。
      setEdinetNote(e instanceof Error ? e.message : String(e));
    } finally {
      setEdinetBusy(false);
    }
  }

  // Discord 疎通テスト（冪等回避＝毎回飛ぶ・ADR-011 の別口）。
  const [discordBusy, setDiscordBusy] = useState(false);
  const [discordNote, setDiscordNote] = useState<string | null>(null);

  async function onDiscordTest() {
    setDiscordBusy(true);
    setDiscordNote(null);
    try {
      const r = await sendDiscordTest();
      if (!r.enabled) {
        setDiscordNote("Discord Webhook URL が未設定なのだ（backend の .env を確認するのだ）。");
      } else if (!r.sent) {
        setDiscordNote("送信に失敗したのだ（Webhook URL・ネットワークを確認するのだ）。");
      } else {
        setDiscordNote("テストメッセージを送ったのだ ✅ Discord を確認するのだ。");
      }
    } catch (e) {
      setDiscordNote(e instanceof Error ? e.message : String(e));
    } finally {
      setDiscordBusy(false);
    }
  }

  // J-Quants 疎通テスト（認証ピング・DB 非依存・ADR-008/011/036）。
  const [jquantsBusy, setJquantsBusy] = useState(false);
  const [jquantsNote, setJquantsNote] = useState<string | null>(null);

  async function onJquantsTest() {
    setJquantsBusy(true);
    setJquantsNote(null);
    try {
      const r = await sendJquantsTest();
      if (!r.configured) {
        setJquantsNote("J-Quants API キーが未設定なのだ（backend の .env を確認するのだ）。");
      } else if (!r.ok) {
        setJquantsNote(`疎通に失敗したのだ（${r.detail}）。`);
      } else {
        setJquantsNote(`疎通OK ✅ ${r.detail}`);
      }
    } catch (e) {
      setJquantsNote(e instanceof Error ? e.message : String(e));
    } finally {
      setJquantsBusy(false);
    }
  }

  const running = batchStatus?.running ?? false;

  return (
    <>
      <div className="mb-3">
        <div className="font-semibold text-[20px] tracking-[-0.4px]">Settings</div>
        <div className="mt-0.5 text-[12px] text-ink-muted">
          backend の健全性と環境変数の充足、夜間バッチの手動起動、外部依存の疎通テスト。秘密情報は
          backend の .env のみ。
        </div>
      </div>

      <div className="grid gap-3">
        {/* backend 健全性（/health の env 詳細） */}
        <Card
          title="backend 健全性"
          meta={data ? `phase ${data.phase ?? "?"} / db ${data.db ?? "?"}` : undefined}
        >
          <StatusBlock
            loading={loading}
            error={error}
            errorHint="backend が起動しているか、Next の rewrites 転送先（BACKEND_ORIGIN）を確認するのだ。"
          >
            {data && (
              <ul className="grid gap-1.5">
                {ENV_LABELS.map(({ key, label }) => {
                  const st = data.env?.[key];
                  return (
                    <li key={key} className="flex items-center justify-between text-[13px]">
                      <span className="text-ink-muted">
                        {label}
                        {st && (
                          <span className="ml-1 text-[11px] text-ink-subtle">
                            (Phase {st.required_from_phase}〜)
                          </span>
                        )}
                      </span>
                      <span className={st?.set ? "text-up" : "text-ink-subtle"}>
                        {st?.set ? "● 設定済み" : "○ 未設定"}
                      </span>
                    </li>
                  );
                })}
              </ul>
            )}
          </StatusBlock>
        </Card>

        {/* 夜間バッチ手動起動（差分/全銘柄フル）＋進捗＋停止（ADR-036） */}
        <Card title="夜間バッチ">
          <p className="mb-2 text-[12px] text-ink-muted">
            取得 → signals 算出 → 夜の分析AI → Discord 通知（digest）を手動で 1 回走らせる。
            通知が届くには Discord Webhook URL の設定が必要なのだ。
          </p>

          <label className="mb-2 flex items-center gap-2 text-[12px] text-ink-muted">
            <input
              type="checkbox"
              checked={fullBackfill}
              onChange={(e) => setFullBackfill(e.target.checked)}
              disabled={batchBusy || running}
              className="h-3.5 w-3.5 accent-accent"
            />
            全銘柄フル取得（初回/復旧・約100〜150分）
          </label>

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={onRunBatch}
              disabled={batchBusy || running}
              className="rounded-md border border-hairline bg-surface-2 px-3 py-1.5 text-[13px] font-medium hover:bg-surface-3 disabled:opacity-50"
            >
              {batchBusy ? "起動中…" : "バッチを今すぐ実行"}
            </button>
            {running && (
              <button
                type="button"
                onClick={onStopBatch}
                disabled={stopBusy || batchStatus?.stop_requested}
                className="rounded-md border border-hairline bg-surface-2 px-3 py-1.5 text-[13px] font-medium text-down hover:bg-surface-3 disabled:opacity-50"
              >
                {stopBusy ? "停止要求中…" : "停止"}
              </button>
            )}
          </div>

          {running && (
            <p className="mt-2 text-[12px] text-ink-muted">
              <span className="text-up">● 実行中</span>（
              {batchStatus?.full_backfill ? "全銘柄フル" : "差分"}）
              {batchStatus?.current_job ? `・${batchStatus.current_job}` : ""}・経過{" "}
              {elapsedMin(batchStatus?.started_at ?? null) ?? "?"} 分
              {batchStatus?.stop_requested ? "・停止待ち（今のジョブ完了後）" : ""}
            </p>
          )}
          {batchNote && <p className="mt-2 text-[12px] text-ink-muted">{batchNote}</p>}
        </Card>

        {/* EDINET 差分タグ付け（テーマタグ段階C・ADR-056）。夜間と同じ差分を run_jobs で 1 回。 */}
        <Card title="EDINET 差分タグ付け">
          <p className="mb-2 text-[12px] text-ink-muted">
            EDINET 有報「事業の内容」を取り込み（提出日クロール差分）→ JP テーマを grounded
            タグ付けまで 1 回走らせる。EDINET API キーの設定が必要なのだ。初回の全銘柄バックフィル
            （約15ヶ月遡及・LLM コスト大）は <code>app.scripts.backfill_edinet</code>{" "}
            で手動実行するのだ。
          </p>
          <button
            type="button"
            onClick={onRunEdinet}
            disabled={edinetBusy || running}
            className="rounded-md border border-hairline bg-surface-2 px-3 py-1.5 text-[13px] font-medium hover:bg-surface-3 disabled:opacity-50"
          >
            {edinetBusy ? "起動中…" : "EDINET 差分を今すぐ実行"}
          </button>
          {edinetNote && <p className="mt-2 text-[12px] text-ink-muted">{edinetNote}</p>}
        </Card>

        {/* Discord 疎通テスト（冪等回避＝毎回飛ぶ・ADR-011） */}
        <Card title="Discord 通知">
          <p className="mb-2 text-[12px] text-ink-muted">
            Discord Webhook に単発のテストメッセージを送って、通知が届くか確認する。 dossier の
            digest を待たずに今すぐ疎通確認できるのだ。
          </p>
          <button
            type="button"
            onClick={onDiscordTest}
            disabled={discordBusy}
            className="rounded-md border border-hairline bg-surface-2 px-3 py-1.5 text-[13px] font-medium hover:bg-surface-3 disabled:opacity-50"
          >
            {discordBusy ? "送信中…" : "テスト通知を送る"}
          </button>
          {discordNote && <p className="mt-2 text-[12px] text-ink-muted">{discordNote}</p>}
        </Card>

        {/* J-Quants 疎通テスト（認証ピング・DB 非依存・ADR-008/011/036） */}
        <Card title="J-Quants 疎通">
          <p className="mb-2 text-[12px] text-ink-muted">
            J-Quants V2 に認証ピングを 1 発投げて、API キーが通って株価データを取れるか確認する。 DB
            には触らないので、初回デプロイ前の確認に使えるのだ。
          </p>
          <button
            type="button"
            onClick={onJquantsTest}
            disabled={jquantsBusy}
            className="rounded-md border border-hairline bg-surface-2 px-3 py-1.5 text-[13px] font-medium hover:bg-surface-3 disabled:opacity-50"
          >
            {jquantsBusy ? "確認中…" : "疎通を確認する"}
          </button>
          {jquantsNote && <p className="mt-2 text-[12px] text-ink-muted">{jquantsNote}</p>}
        </Card>
      </div>
    </>
  );
}
