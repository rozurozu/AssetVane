"use client";

// スクリーニングのフィルタパネル（screens.md #2・ADR-031）。
// draft 条件を親から受け、入力を onChange で返す（feature は GET しない＝親が screenStocks を持つ）。
// 配当利回り・業種内パーセンタイルは UI で %、内部 0..1（ADR-008）。適用は onApply。

import { inputCls, labelCls } from "@/components/ui/Field";
import type { ScreenCriteria } from "@/lib/api";
import { fromPctStr, toNum, toPctStr } from "@/lib/format";

type Props = {
  draft: ScreenCriteria;
  onChange: (next: ScreenCriteria) => void;
  onApply: () => void;
  onReset: () => void;
};

const SORT_OPTS: { value: NonNullable<ScreenCriteria["sort_by"]>; label: string }[] = [
  { value: "market_cap", label: "時価総額" },
  { value: "per", label: "PER" },
  { value: "pbr", label: "PBR" },
  { value: "dividend_yield", label: "配当利回り" },
  { value: "per_sector_pctile", label: "業種内PER順位" },
  { value: "code", label: "コード" },
];

export function ScreenerFilters({ draft, onChange, onApply, onReset }: Props) {
  // 1 キーだけ差し替えた新 draft を親へ返す（不変更新）。
  function set<K extends keyof ScreenCriteria>(key: K, value: ScreenCriteria[K]) {
    const next = { ...draft };
    if (value === undefined || value === "") delete next[key];
    else next[key] = value;
    onChange(next);
  }

  // 数値レンジ入力（min/max ペア）。文字列 → number|undefined に正規化して set。
  function numField(label: string, key: keyof ScreenCriteria, step?: string) {
    return (
      <label className="flex flex-col">
        <span className={labelCls}>{label}</span>
        <input
          type="number"
          step={step}
          className={inputCls}
          value={draft[key] === undefined ? "" : String(draft[key])}
          onChange={(e) => set(key, toNum(e.target.value) as ScreenCriteria[typeof key])}
        />
      </label>
    );
  }

  // 利回り（0..1 を % で入出力）。
  function yieldField(label: string, key: "dividend_yield_min" | "dividend_yield_max") {
    return (
      <label className="flex flex-col">
        <span className={labelCls}>{label}</span>
        <input
          type="number"
          step="0.1"
          className={inputCls}
          value={toPctStr(draft[key] ?? null)}
          onChange={(e) => set(key, fromPctStr(e.target.value) ?? undefined)}
        />
      </label>
    );
  }

  return (
    <form
      className="rounded-lg border border-hairline bg-surface-1 p-3"
      onSubmit={(e) => {
        e.preventDefault();
        onApply();
      }}
    >
      <label className="mb-2 flex flex-col">
        <span className={labelCls}>銘柄名・コードで検索</span>
        <input
          className={inputCls}
          placeholder="例: トヨタ / 7203"
          value={draft.q ?? ""}
          onChange={(e) => set("q", e.target.value || undefined)}
        />
      </label>

      <div className="grid grid-cols-2 gap-x-3 gap-y-2 sm:grid-cols-4 lg:grid-cols-6">
        {numField("PER 下限", "per_min", "0.1")}
        {numField("PER 上限", "per_max", "0.1")}
        {numField("PBR 下限", "pbr_min", "0.1")}
        {numField("PBR 上限", "pbr_max", "0.1")}
        {yieldField("配当利回り下限 %", "dividend_yield_min")}
        {yieldField("配当利回り上限 %", "dividend_yield_max")}
        {numField("時価総額 上位N社", "market_cap_rank_max")}
        <label className="flex flex-col">
          <span className={labelCls}>業種内PER 安い割合 % 以下</span>
          <input
            type="number"
            step="1"
            className={inputCls}
            value={toPctStr(draft.per_sector_pctile_max ?? null)}
            onChange={(e) => set("per_sector_pctile_max", fromPctStr(e.target.value) ?? undefined)}
          />
        </label>
        <label className="flex flex-col">
          <span className={labelCls}>33業種コード</span>
          <input
            className={inputCls}
            value={draft.sector33_code ?? ""}
            onChange={(e) => set("sector33_code", e.target.value || undefined)}
          />
        </label>
        <label className="flex flex-col">
          <span className={labelCls}>市場コード</span>
          <input
            className={inputCls}
            value={draft.market_code ?? ""}
            onChange={(e) => set("market_code", e.target.value || undefined)}
          />
        </label>
        <label className="flex flex-col">
          <span className={labelCls}>並び替え</span>
          <select
            className={inputCls}
            value={draft.sort_by ?? "market_cap"}
            onChange={(e) => set("sort_by", e.target.value as ScreenCriteria["sort_by"])}
          >
            {SORT_OPTS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col">
          <span className={labelCls}>順序</span>
          <select
            className={inputCls}
            value={draft.sort_dir ?? "desc"}
            onChange={(e) => set("sort_dir", e.target.value as ScreenCriteria["sort_dir"])}
          >
            <option value="desc">降順</option>
            <option value="asc">昇順</option>
          </select>
        </label>
      </div>

      <div className="mt-3 flex items-center gap-3">
        <label className="flex items-center gap-1.5 text-[12px] text-ink-muted">
          <input
            type="checkbox"
            checked={draft.exclude_etf ?? false}
            onChange={(e) => set("exclude_etf", e.target.checked || undefined)}
          />
          ETF/REIT を除外
        </label>
        <div className="ml-auto flex gap-2">
          <button
            type="button"
            onClick={onReset}
            className="rounded-md border border-hairline px-3 py-1.5 text-[12px] text-ink-muted hover:bg-surface-2"
          >
            条件クリア
          </button>
          <button
            type="submit"
            className="rounded-md bg-accent px-3 py-1.5 text-[12px] font-semibold text-white hover:opacity-90"
          >
            絞り込む
          </button>
        </div>
      </div>
    </form>
  );
}
