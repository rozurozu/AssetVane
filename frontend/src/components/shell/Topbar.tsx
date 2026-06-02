"use client";

import { useEffect, useState } from "react";

// topbar 48px / canvas。検索・データ鮮度バッジ・日付に加えて、backend(/health) への
// 疎通を 1 回確認して出す（CORS が効いているかの最小チェック。失敗しても画面は壊さない）。
const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

type Health = "checking" | "ok" | "down";

export function Topbar() {
  const [health, setHealth] = useState<Health>("checking");

  useEffect(() => {
    let alive = true;
    fetch(`${API}/health`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(() => alive && setHealth("ok"))
      .catch(() => alive && setHealth("down"));
    return () => {
      alive = false;
    };
  }, []);

  const badge = {
    checking: { text: "backend 確認中…", color: "var(--color-ink-subtle)" },
    ok: { text: "backend OK", color: "var(--color-up)" },
    down: { text: "backend 未接続", color: "var(--color-down)" },
  }[health];

  return (
    <div className="sticky top-0 z-30 flex h-12 items-center gap-3.5 border-hairline border-b bg-canvas px-4">
      <div className="flex max-w-[340px] flex-1 items-center gap-2 rounded-md border border-hairline bg-surface-1 px-2.5 py-1.5 text-[13px] text-ink-subtle">
        🔍 銘柄を検索（コード・名称）…
      </div>

      <span
        className="flex items-center gap-1.5 rounded-md border border-hairline bg-surface-1 px-2 py-1 text-[12px]"
        style={{ color: badge.color }}
      >
        <span className="h-1.5 w-1.5 rounded-full" style={{ background: badge.color }} />
        {badge.text}
      </span>

      <span className="ml-auto flex items-center gap-1.5 rounded-sm border border-hairline bg-surface-1 px-2 py-1 text-[12px] text-warning">
        <span className="h-1.5 w-1.5 rounded-full bg-warning" />
        Free・株価12週遅延（〜2026-03-09）
      </span>
      <span className="num text-[13px] text-ink-muted">2026-06-02 (月)</span>
    </div>
  );
}
