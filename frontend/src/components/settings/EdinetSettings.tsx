"use client";

import { Card } from "@/components/ui/Card";
import { inputCls, labelCls } from "@/components/ui/Field";
import { StatusBlock } from "@/components/ui/StatusBlock";
import { type EdinetConfig, getEdinetConfig, sendEdinetTest, updateEdinetConfig } from "@/lib/api";
import { useCallback, useEffect, useState } from "react";

// 公式 EDINET（api.edinet-fsa.go.jp）接続設定カード（api_key を DB+WebUI で管理・ADR-087）。
// EdinetdbSettings ミラー: api_key は write-only（空送信で据え置き）。plan は無い（公式 EDINET は
// 回数クォータ無し）。疎通テストは POST /diagnostics/edinet-test（sendEdinetTest）。第三者 edinetdb.jp
// （下の「EDINET DB 設定」）とは**別系統**＝有報「事業の内容」テキスト源（テーマタグ段階C）。

const btnCls =
  "rounded-md border border-hairline bg-surface-2 px-3 py-1.5 text-[13px] font-medium hover:bg-surface-3 disabled:opacity-50";

function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** 公式 EDINET 接続設定カード（api_key・疎通テストを 1 枚に集約・ADR-087）。 */
export function EdinetSettings() {
  const [cfg, setCfg] = useState<EdinetConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const c = await getEdinetConfig();
      setCfg(c);
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
      const c = await updateEdinetConfig({ api_key: apiKey }); // 空は backend が据え置き（write-only）
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
      const r = await sendEdinetTest();
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
    <Card title="EDINET（公式）設定">
      <p className="mb-2 text-[12px] text-ink-muted">
        金融庁 EDINET API v2 の Subscription-Key（無料登録）。有報「事業の内容」を取り込む
        テーマタグ段階C に使う（ADR-056/087）。第三者サービス edinetdb.jp（下の「EDINET DB
        設定」）とは別物なので、edinetdb の <code>edb_</code>{" "}
        キーをここに入れないこと。未設定なら段階C 取得は静かに skip
        する。疎通テストは保存後に確認できるのだ。
      </p>
      <StatusBlock loading={loading} error={error}>
        {cfg && (
          <div className="grid gap-2">
            <div className="flex items-center justify-between">
              <span
                className={cfg.configured ? "text-[11px] text-up" : "text-[11px] text-ink-subtle"}
              >
                {cfg.configured ? "● 設定済み（有効）" : "○ 未設定（段階C 取得は静かに skip）"}
              </span>
            </div>
            <div>
              <span className={labelCls}>
                API キー（現在: {cfg.has_api_key ? cfg.api_key_masked : "未設定"}・変更時のみ入力）
              </span>
              <input
                className={inputCls}
                type="password"
                placeholder="変更しないなら空のまま"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
              />
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
