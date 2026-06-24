"use client";

import { Card } from "@/components/ui/Card";
import { inputCls, labelCls } from "@/components/ui/Field";
import { StatusBlock } from "@/components/ui/StatusBlock";
import {
  type JquantsConfig,
  type JquantsPlan,
  getJquantsConfig,
  sendJquantsTest,
  updateJquantsConfig,
} from "@/lib/api";
import { useCallback, useEffect, useState } from "react";

// J-Quants 接続設定カード（api_key/plan を DB+WebUI で管理・ADR-061）。
// EmbeddingCard（LlmSettings）と同型: api_key は write-only（空送信で据え置き）、plan は
// ドロップダウン。疎通テストは既存の POST /diagnostics/jquants-test（sendJquantsTest）を流用する。

const btnCls =
  "rounded-md border border-hairline bg-surface-2 px-3 py-1.5 text-[13px] font-medium hover:bg-surface-3 disabled:opacity-50";

// プランと簡潔なヒント（docs/jquants.md のプラン表・ADR-008/061）。値は JquantsPlan と 1:1。
const PLAN_OPTIONS: { value: JquantsPlan; label: string }[] = [
  { value: "free", label: "Free（12週遅延・TOPIX 403・5req/分）" },
  { value: "light", label: "Light（遅延なし・60req/分）" },
  { value: "standard", label: "Standard（遅延なし・120req/分）" },
  { value: "premium", label: "Premium（遅延なし・500req/分）" },
];

function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** J-Quants 接続設定カード（api_key・plan・疎通テストを 1 枚に集約・ADR-061）。 */
export function JquantsSettings() {
  const [cfg, setCfg] = useState<JquantsConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [apiKey, setApiKey] = useState("");
  const [plan, setPlan] = useState<JquantsPlan>("free");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const c = await getJquantsConfig();
      setCfg(c);
      setPlan((c.plan as JquantsPlan) ?? "free");
    } catch (e) {
      setError(errText(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function onSave() {
    setBusy(true);
    setNote(null);
    try {
      const c = await updateJquantsConfig({
        api_key: apiKey, // 空は backend が据え置き（write-only）
        plan,
      });
      setCfg(c);
      setApiKey("");
      setNote("保存したのだ ✅");
    } catch (e) {
      setNote(errText(e));
    } finally {
      setBusy(false);
    }
  }

  async function onTest() {
    setBusy(true);
    setNote(null);
    try {
      const r = await sendJquantsTest();
      if (!r.configured) {
        setNote("API キーが未設定なのだ（上で保存してから確認するのだ）。");
      } else if (!r.ok) {
        setNote(`疎通NG ⚠ ${r.detail}`);
      } else {
        setNote(`疎通OK ✅ ${r.detail}`);
      }
    } catch (e) {
      setNote(errText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="J-Quants 設定">
      <p className="mb-2 text-[12px] text-ink-muted">
        日本株データ源 J-Quants V2 の API キーと契約プラン。env ではなく DB
        で管理する（ADR-061）。プランはスロットル間隔（取得速度）を決め、Free は 12 週遅延・TOPIX
        403 の既知制限がある。疎通テストは保存後に確認できるのだ。
      </p>
      <StatusBlock loading={loading} error={error}>
        {cfg && (
          <div className="grid gap-2">
            <div className="flex items-center justify-between">
              <span
                className={cfg.configured ? "text-[11px] text-up" : "text-[11px] text-ink-subtle"}
              >
                {cfg.configured ? "● 設定済み（有効）" : "○ 未設定（株価取得は機能オフ）"}
              </span>
            </div>
            <div className="grid gap-2 sm:grid-cols-2">
              <div>
                <span className={labelCls}>
                  API キー（現在: {cfg.has_api_key ? cfg.api_key_masked : "未設定"}
                  ・変更時のみ入力）
                </span>
                <input
                  className={inputCls}
                  type="password"
                  placeholder="変更しないなら空のまま"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                />
              </div>
              <div>
                <span className={labelCls}>プラン</span>
                <select
                  className={inputCls}
                  value={plan}
                  onChange={(e) => setPlan(e.target.value as JquantsPlan)}
                >
                  {PLAN_OPTIONS.map((p) => (
                    <option key={p.value} value={p.value}>
                      {p.label}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <button type="button" className={btnCls} onClick={onSave} disabled={busy}>
                {busy ? "処理中…" : "保存"}
              </button>
              <button type="button" className={btnCls} onClick={onTest} disabled={busy}>
                疎通テスト
              </button>
              {note && <span className="text-[12px] text-ink-muted">{note}</span>}
            </div>
          </div>
        )}
      </StatusBlock>
    </Card>
  );
}
