"use client";

import { Card } from "@/components/ui/Card";
import { StatusBlock } from "@/components/ui/StatusBlock";
import { type HealthResponse, getHealth, runBatch, sendDiscordTest } from "@/lib/api";
import { useApi } from "@/lib/use-api";
import { useState } from "react";

// Settings 画面（screens.md #14・phase6-spec §6・ADR-005/007）。
// 通知は backend（Discord）が送るので UI は最小: backend 健全性（/health）の env 詳細表示 ＋
// 夜間バッチ手動起動（runBatch）だけ。Webhook URL・しきい値は .env 固定で UI から編集しない（L-25）。
// しきい値の UI 化（OPEN-M）・通知履歴 UI（OPEN-O）は必要になってから（YAGNI）。

// env キーの日本語ラベル（順序固定・config.py env_status のキーに対応）。
const ENV_LABELS: { key: string; label: string }[] = [
  { key: "jquants_api_key", label: "J-Quants API キー" },
  { key: "llm_api_key", label: "LLM API キー" },
  { key: "discord_webhook_url", label: "Discord Webhook URL（通知）" },
];

export default function SettingsPage() {
  const { data, error, loading } = useApi<HealthResponse>((signal) => getHealth(signal), []);

  // 手動バッチ起動（Dashboard と同じ流儀・mutation 起点は useState＝rule (c)）。
  const [batchBusy, setBatchBusy] = useState(false);
  const [batchNote, setBatchNote] = useState<string | null>(null);

  async function onRunBatch() {
    setBatchBusy(true);
    setBatchNote(null);
    try {
      await runBatch();
      setBatchNote("バッチを起動したのだ。進捗は signals/資産が更新されるまで待つのだ。");
    } catch (e) {
      // 409（実行中）も ApiError の message で拾って表示する。
      setBatchNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBatchBusy(false);
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

  return (
    <>
      <div className="mb-3">
        <div className="font-semibold text-[20px] tracking-[-0.4px]">Settings</div>
        <div className="mt-0.5 text-[12px] text-ink-muted">
          backend の健全性と環境変数の充足、夜間バッチの手動起動。秘密情報は backend の .env のみ。
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
            errorHint="backend が起動しているか、NEXT_PUBLIC_API_BASE_URL を確認するのだ。"
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

        {/* 夜間バッチ手動起動 */}
        <Card title="夜間バッチ">
          <p className="mb-2 text-[12px] text-ink-muted">
            取得 → signals 算出 → 夜の分析AI → Discord 通知（digest）を手動で 1 回走らせる。
            通知が届くには Discord Webhook URL の設定が必要なのだ。
          </p>
          <button
            type="button"
            onClick={onRunBatch}
            disabled={batchBusy}
            className="rounded-md border border-hairline bg-surface-2 px-3 py-1.5 text-[13px] font-medium hover:bg-surface-3 disabled:opacity-50"
          >
            {batchBusy ? "起動中…" : "バッチを今すぐ実行"}
          </button>
          {batchNote && <p className="mt-2 text-[12px] text-ink-muted">{batchNote}</p>}
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
      </div>
    </>
  );
}
