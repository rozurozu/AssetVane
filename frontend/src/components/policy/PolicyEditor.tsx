"use client";

// 投資方針エディタ（screens.md §3・spec §9.2）。
// 構造化コア（チップ/グリッド・編集可）＋ rationale（引用調テキストエリア）。
// 比率系（target_cash_ratio / max_position_weight / sector_caps）は 0..1 を ×100 して % 表示、
// 保存時 ÷100（ADR-008）。保存は putPolicy（core=承認制 lever / rationale=即時・ADR-013）。
// DB には触れない。データは lib/api.ts 経由のみ（ADR-005）。

import { inputCls, labelCls } from "@/components/ui/Field";
import { openAdvisorChat } from "@/lib/advisor-bus";
import { type Policy, type PolicyCore, type PolicyUpdate, putPolicy } from "@/lib/api";
import { fromPctStr, toPctStr } from "@/lib/format";
import { useState } from "react";

type Props = {
  policy: Policy;
  onSaved: (p: Policy) => void;
};

export function PolicyEditor({ policy, onSaved }: Props) {
  const c = policy.core;
  const [risk, setRisk] = useState(c.risk_tolerance ?? "");
  const [horizon, setHorizon] = useState(c.time_horizon ?? "");
  const [cashPct, setCashPct] = useState(toPctStr(c.target_cash_ratio));
  const [maxPosPct, setMaxPosPct] = useState(toPctStr(c.max_position_weight));
  const [returnPct, setReturnPct] = useState(toPctStr(c.target_return));
  const [noLeverage, setNoLeverage] = useState(c.no_leverage);
  // sector_caps / exclusions はカンマ区切りの簡易入力（コード:％ ／ コード）。
  const [sectorCaps, setSectorCaps] = useState(
    Object.entries(c.sector_caps)
      .map(([code, w]) => `${code}:${Math.round(w * 1000) / 10}`)
      .join(", "),
  );
  const [exclusions, setExclusions] = useState(c.exclusions.join(", "));
  const [rationale, setRationale] = useState(policy.rationale ?? "");

  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState(false);

  function parseSectorCaps(s: string): Record<string, number> {
    const out: Record<string, number> = {};
    for (const part of s.split(",")) {
      const [code, pct] = part.split(":").map((x) => x.trim());
      if (!code) continue;
      const v = fromPctStr(pct ?? "");
      if (v != null) out[code] = v;
    }
    return out;
  }

  async function handleSave() {
    setSaving(true);
    setErr(null);
    setOk(false);
    try {
      // 0..1 へ戻して送る（UI のみ %・spec §9.2）。core 全体を送る（部分更新でも整合）。
      const core: Partial<PolicyCore> = {
        risk_tolerance: risk.trim() || null,
        time_horizon: horizon.trim() || null,
        target_cash_ratio: fromPctStr(cashPct),
        max_position_weight: fromPctStr(maxPosPct),
        target_return: fromPctStr(returnPct),
        no_leverage: noLeverage,
        sector_caps: parseSectorCaps(sectorCaps),
        exclusions: exclusions
          .split(",")
          .map((x) => x.trim())
          .filter(Boolean),
      };
      const update: PolicyUpdate = { core, rationale };
      const saved = await putPolicy(update);
      onSaved(saved);
      setOk(true);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-3">
      {/* rationale（自由文の理念・引用調・即時更新＝ADR-013）。 */}
      <section className="rounded-lg border border-hairline bg-surface-1">
        <div className="flex items-center justify-between border-hairline border-b px-3 py-2">
          <h2 className="font-semibold text-[14px] tracking-[-0.1px]">理念（rationale）</h2>
          <span className="text-[11px] text-ink-subtle">引用調・即時反映</span>
        </div>
        <div className="p-3">
          <textarea
            value={rationale}
            onChange={(e) => setRationale(e.target.value)}
            rows={4}
            placeholder="攻めるが退場はしない。資産が小さいうちは短期にリスクを取る…"
            className={`${inputCls} resize-y border-l-2 border-l-accent`}
          />
        </div>
      </section>

      {/* 構造化コア（定量レバー・最適化制約に効く＝承認制レバー・チップ/グリッド）。 */}
      <section className="rounded-lg border border-hairline bg-surface-1">
        <div className="flex items-center justify-between border-hairline border-b px-3 py-2">
          <h2 className="font-semibold text-[14px] tracking-[-0.1px]">構造化コア</h2>
          <span className="text-[11px] text-ink-subtle">比率は % 入力（内部は 0..1）</span>
        </div>
        <div className="grid grid-cols-3 gap-3 p-3 max-[800px]:grid-cols-2 max-[520px]:grid-cols-1">
          <div>
            <label htmlFor="p-risk" className={labelCls}>
              リスク許容度
            </label>
            <select
              id="p-risk"
              value={risk}
              onChange={(e) => setRisk(e.target.value)}
              className={inputCls}
            >
              <option value="">—</option>
              <option value="低">低</option>
              <option value="中">中</option>
              <option value="高">高</option>
            </select>
          </div>
          <div>
            <label htmlFor="p-horizon" className={labelCls}>
              時間軸
            </label>
            <select
              id="p-horizon"
              value={horizon}
              onChange={(e) => setHorizon(e.target.value)}
              className={inputCls}
            >
              <option value="">—</option>
              <option value="短">短</option>
              <option value="中">中</option>
              <option value="長">長</option>
            </select>
          </div>
          <div>
            <label htmlFor="p-cash" className={labelCls}>
              現金目標（%）
            </label>
            <input
              id="p-cash"
              type="number"
              min="0"
              max="100"
              step="0.1"
              value={cashPct}
              onChange={(e) => setCashPct(e.target.value)}
              placeholder="25"
              className={`num ${inputCls}`}
            />
          </div>
          <div>
            <label htmlFor="p-maxpos" className={labelCls}>
              1 銘柄上限（%）
            </label>
            <input
              id="p-maxpos"
              type="number"
              min="0"
              max="100"
              step="0.1"
              value={maxPosPct}
              onChange={(e) => setMaxPosPct(e.target.value)}
              placeholder="15"
              className={`num ${inputCls}`}
            />
          </div>
          <div>
            <label htmlFor="p-return" className={labelCls}>
              目標リターン（%・年・任意）
            </label>
            <input
              id="p-return"
              type="number"
              step="0.1"
              value={returnPct}
              onChange={(e) => setReturnPct(e.target.value)}
              placeholder="15"
              className={`num ${inputCls}`}
            />
          </div>
          <div>
            <span className={labelCls}>レバレッジ</span>
            <label
              htmlFor="p-noleverage"
              className="flex h-[34px] items-center gap-2 text-[13px] text-ink"
            >
              <input
                id="p-noleverage"
                type="checkbox"
                checked={noLeverage}
                onChange={(e) => setNoLeverage(e.target.checked)}
              />
              <span className={noLeverage ? "text-warning" : "text-ink-muted"}>
                使わない（ゼロカット許容）
              </span>
            </label>
          </div>
          <div className="col-span-full">
            <label htmlFor="p-sector" className={labelCls}>
              業種上限（sector33コード:% をカンマ区切り）
            </label>
            <input
              id="p-sector"
              type="text"
              value={sectorCaps}
              onChange={(e) => setSectorCaps(e.target.value)}
              placeholder="3650:30, 5250:20"
              className={inputCls}
            />
          </div>
          <div className="col-span-full">
            <label htmlFor="p-excl" className={labelCls}>
              除外銘柄（コードをカンマ区切り）
            </label>
            <input
              id="p-excl"
              type="text"
              value={exclusions}
              onChange={(e) => setExclusions(e.target.value)}
              placeholder="7203, 9984"
              className={inputCls}
            />
          </div>
        </div>
      </section>

      {err && (
        <div className="rounded-md bg-down-weak px-3 py-2 text-[13px] text-down">⚠ {err}</div>
      )}
      {ok && (
        <div className="rounded-md bg-up-weak px-3 py-2 text-[13px] text-up">保存したのだ。</div>
      )}

      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={handleSave}
          disabled={saving}
          className="rounded-md border border-accent bg-accent px-4 py-1.5 font-semibold text-[13px] text-white disabled:opacity-50"
        >
          {saving ? "保存中…" : "保存するのだ"}
        </button>
        {/* チャットで調整する導線（AdvisorChat を開くだけ・spec §9.2）。 */}
        <button
          type="button"
          onClick={() => openAdvisorChat()}
          className="rounded-md border border-hairline px-4 py-1.5 text-[13px] text-ink-muted hover:bg-surface-2 hover:text-ink"
        >
          🧠 チャットで調整 →
        </button>
      </div>
      <div className="text-[11px] text-ink-subtle">
        構造化コア（最適化に効くレバー）の変更はチャット経由だと承認制、ここでの直接保存は即時反映（ADR-013）。
      </div>
    </div>
  );
}
