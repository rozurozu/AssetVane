"use client";

import { Card } from "@/components/ui/Card";
import { inputCls, labelCls } from "@/components/ui/Field";
import { StatusBlock } from "@/components/ui/StatusBlock";
import {
  type EdinetdbConfig,
  type EdinetdbPlan,
  getEdinetdbConfig,
  sendEdinetdbTest,
  updateEdinetdbConfig,
} from "@/lib/api";
import { useCallback, useEffect, useState } from "react";

// EDINET DB（edinetdb.jp）接続設定カード（api_key/plan を DB+WebUI で管理・ADR-064）。
// JquantsSettings ミラー: api_key は write-only（空送信で据え置き）、plan はドロップダウン。
// 疎通テストは POST /diagnostics/edinetdb-test（sendEdinetdbTest）。公式 EDINET（env の
// edinet_api_key・テーマタグ段階C）とは**別系統**＝#2 売掛/在庫の質の構造化財務取得に使う。

const btnCls =
  "rounded-md border border-hairline bg-surface-2 px-3 py-1.5 text-[13px] font-medium hover:bg-surface-3 disabled:opacity-50";

// プランと簡潔なヒント（ADR-064・services/edinetdb_config._PLAN_LIMITS と 1:1）。
const PLAN_OPTIONS: { value: EdinetdbPlan; label: string }[] = [
  { value: "free", label: "Free（日100・月600 リクエスト）" },
  { value: "pro", label: "Pro（上限拡大・契約時に実値確認）" },
];

function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** EDINET DB 接続設定カード（api_key・plan・疎通テストを 1 枚に集約・ADR-064）。 */
export function EdinetdbSettings() {
  const [cfg, setCfg] = useState<EdinetdbConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [apiKey, setApiKey] = useState("");
  const [plan, setPlan] = useState<EdinetdbPlan>("free");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const c = await getEdinetdbConfig();
      setCfg(c);
      setPlan((c.plan as EdinetdbPlan) ?? "free");
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
      const c = await updateEdinetdbConfig({
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
      const r = await sendEdinetdbTest();
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
    <Card title="EDINET DB 設定">
      <p className="mb-2 text-[12px] text-ink-muted">
        第三者サービス edinetdb.jp の API キー（X-API-Key）と契約プラン。公式
        EDINET（金融庁）とは別物で、
        #2「売掛金・在庫の質」の構造化財務取得に使う（ADR-064）。プランはレート目安（Free
        は日100・月600）
        を決めるが、実際の予算はレスポンスの残量ヘッダで管理する。疎通テストは保存後に確認できるのだ。
      </p>
      <StatusBlock loading={loading} error={error}>
        {cfg && (
          <div className="grid gap-2">
            <div className="flex items-center justify-between">
              <span
                className={cfg.configured ? "text-[11px] text-up" : "text-[11px] text-ink-subtle"}
              >
                {cfg.configured ? "● 設定済み（有効）" : "○ 未設定（#2 取得は静かに skip）"}
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
                  onChange={(e) => setPlan(e.target.value as EdinetdbPlan)}
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
