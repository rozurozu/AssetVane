"use client";

// Policy ページ（screens.md §3・spec §9.2）。
// 現在の投資方針（core / rationale 分離・GET /policy）を読んで PolicyEditor で編集・保存。
// DB には触れない。データ取得はすべて lib/api.ts 経由（ADR-005）。

import { PolicyEditor } from "@/components/policy/PolicyEditor";
import { type Policy, getPolicy } from "@/lib/api";
import { useEffect, useState } from "react";

export default function PolicyPage() {
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getPolicy()
      .then(setPolicy)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  return (
    <>
      <div className="mb-3">
        <div className="font-semibold text-[20px] tracking-[-0.4px]">投資方針（Policy）</div>
        <div className="mt-0.5 text-[12px] text-ink-muted">
          単一の方針を育てる（版管理機構なし＝ADR-013）。
          {policy?.updated_at && (
            <span className="num ml-1">最終更新 {policy.updated_at.slice(0, 10)}</span>
          )}
        </div>
      </div>

      {error && (
        <div className="rounded-lg border border-hairline bg-surface-1 p-3 text-[13px] text-down">
          ⚠ 方針の取得に失敗: {error}
          <div className="mt-1 text-[12px] text-ink-subtle">
            backend が起動しているか確認するのだ。
          </div>
        </div>
      )}
      {!error && policy === null && (
        <div className="rounded-lg border border-hairline bg-surface-1 p-3 text-[13px] text-ink-subtle">
          読み込み中…
        </div>
      )}
      {!error && policy && <PolicyEditor policy={policy} onSaved={setPolicy} />}
    </>
  );
}
