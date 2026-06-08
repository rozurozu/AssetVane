"use client";

import { type HealthResponse, getHealth } from "@/lib/api";
import { useEffect, useState } from "react";

// topbar 48px / canvas。検索・データ鮮度バッジ・日付に加えて、backend(/health) への
// 疎通バッジを出す（CORS / 接続可否の最小チェック。失敗しても画面は壊さない）。
// マウント時に 1 回確認したあと HEALTH_POLL_MS ごとに再チェックし、down→ok へ自己回復する
// （Pi 冷間起動の偽陽性を防ぐ）。失敗時は解決済み URL 付きで console.error する（ADR-038）。
// 取得は lib/api.ts の getHealth() に集約（生 fetch を散らさない・ADR-005）。getHealth 側で
// 5s タイムアウトが効く（無応答でも赤に倒れる）。

type Health = "checking" | "ok" | "down";

/** /health の再チェック間隔（ミリ秒・ADR-038）。down→ok の自己回復はこの周期で起きる。 */
const HEALTH_POLL_MS = 30000;

export function Topbar() {
  const [health, setHealth] = useState<Health>("checking");
  // /health の本体を保持し、ADR-028 の warn バナー（llm_cost）を読む。別ポーリングは足さない。
  const [data, setData] = useState<HealthResponse | null>(null);

  useEffect(() => {
    let alive = true;
    // 進行中の fetch を保持し、アンマウント時に中断する（getHealth は signal を受ける）。
    let inflight: AbortController | null = null;

    const check = () => {
      inflight?.abort(); // 前回が走っていれば中断（重複防止）
      const ctrl = new AbortController();
      inflight = ctrl;
      getHealth(ctrl.signal)
        .then((res) => {
          if (alive) {
            setHealth("ok");
            setData(res);
          }
        })
        .catch((error) => {
          // この呼び出し自身が中断された（アンマウント or 次の check による差し替え）なら握る。
          if (!alive || ctrl.signal.aborted) return;
          // error.message に解決済み URL が載っている（lib/api.ts・ADR-038）。
          console.error("[health] backend 未接続", error);
          setHealth("down");
        });
    };

    check(); // 初回は即時
    const id = setInterval(check, HEALTH_POLL_MS); // 以降は定期再チェック（自己回復）

    return () => {
      alive = false;
      clearInterval(id);
      inflight?.abort();
    };
  }, []);

  const badge = {
    checking: { text: "backend 確認中…", color: "var(--color-ink-subtle)" },
    ok: { text: "backend OK", color: "var(--color-up)" },
    down: { text: "backend 未接続", color: "var(--color-down)" },
  }[health];

  // ADR-028: warn かつ当月コスト超過のときだけバナーを出す（block は別経路に任せる＝warn 限定）。
  const costWarn = data?.llm_cost?.mode === "warn" && data.llm_cost.exceeded ? data.llm_cost : null;

  return (
    <>
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
      {costWarn && (
        <div className="sticky top-12 z-20 flex items-center gap-1.5 border-warning border-b border-l-2 border-l-warning bg-canvas px-4 py-1.5 text-[12px] text-warning leading-[1.5]">
          <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-warning" />
          ⚠️ LLM 月額コストが上限（$
          {costWarn.limit_usd.toFixed(2)}）を超過しています（当月 $
          {costWarn.month_total_usd.toFixed(2)}
          ・warn 設定で応答は継続）。AI 呼び出しコストにご注意ください。
        </div>
      )}
    </>
  );
}
