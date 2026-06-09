"use client";

// 米国株スクリーニングのフィルタパネル（screens.md #2 ミラー・ADR-039(B)/ADR-055・提示専用）。
// 日本株 ScreenerFilters のミラー。draft 条件を親から受け、入力を onChange で返す
// （feature は GET しない＝親が screenUsStocks を持つ）。適用は onApply。
// 比率（配当利回り/ROE/各利益率/各成長率/業種内パーセンタイル）は UI で %、内部 0..1（ADR-008）。
// 業種は日本株の sector33_code（数値コード）ではなく gics_sector（Yahoo 英語ラベル）のセレクト。

import { inputCls, labelCls } from "@/components/ui/Field";
import type { UsScreenCriteria } from "@/lib/api";
import { fromPctStr, toNum, toPctStr } from "@/lib/format";

type Props = {
  draft: UsScreenCriteria;
  onChange: (next: UsScreenCriteria) => void;
  onApply: () => void;
  onReset: () => void;
};

// GICS 相当セクター（value=backend の Yahoo 英語ラベル＝完全一致のキー / label=和名表示）。
// backend app/reference/gics_sectors.py の GICS_SECTOR_LABELS_JA と同じ英語キーで揃える（ADR-055）。
const GICS_SECTOR_OPTS: { value: string; label: string }[] = [
  { value: "Technology", label: "情報技術" },
  { value: "Financial Services", label: "金融" },
  { value: "Healthcare", label: "ヘルスケア" },
  { value: "Consumer Cyclical", label: "一般消費財" },
  { value: "Consumer Defensive", label: "生活必需品" },
  { value: "Industrials", label: "資本財・サービス" },
  { value: "Communication Services", label: "通信サービス" },
  { value: "Energy", label: "エネルギー" },
  { value: "Basic Materials", label: "素材" },
  { value: "Utilities", label: "公益事業" },
  { value: "Real Estate", label: "不動産" },
];

// 英語ラベル → 和名（一覧の業種列の表示用・正規化はしない＝backend 値の素引き）。
const GICS_LABEL_JA: Record<string, string> = Object.fromEntries(
  GICS_SECTOR_OPTS.map((o) => [o.value, o.label]),
);

/** Yahoo 英語セクターラベル → 和名表示（未知・null はそのまま/"—"）。一覧の業種列で使う。 */
export function gicsSectorLabel(value: string | null | undefined): string {
  if (!value) return "—";
  return GICS_LABEL_JA[value] ?? value;
}

const SORT_OPTS: { value: NonNullable<UsScreenCriteria["sort_by"]>; label: string }[] = [
  { value: "market_cap", label: "時価総額" },
  { value: "per", label: "PER" },
  { value: "pbr", label: "PBR" },
  { value: "dividend_yield", label: "配当利回り" },
  { value: "roe", label: "ROE" },
  { value: "operating_margin", label: "営業利益率" },
  { value: "net_margin", label: "純利益率" },
  { value: "revenue_growth_yoy", label: "売上成長(YoY)" },
  { value: "op_growth_yoy", label: "営業益成長(YoY)" },
  { value: "profit_growth_yoy", label: "純益成長(YoY)" },
  { value: "eps_growth_yoy", label: "EPS成長(YoY)" },
  { value: "gics_sector_pctile", label: "業種内PER順位" },
  { value: "market_cap_rank", label: "時価総額順位" },
  { value: "symbol", label: "シンボル" },
];

export function UsScreenerFilters({ draft, onChange, onApply, onReset }: Props) {
  // 1 キーだけ差し替えた新 draft を親へ返す（不変更新）。
  function set<K extends keyof UsScreenCriteria>(key: K, value: UsScreenCriteria[K]) {
    const next = { ...draft };
    if (value === undefined || value === "") delete next[key];
    else next[key] = value;
    onChange(next);
  }

  // 数値レンジ入力（min/max ペア）。文字列 → number|undefined に正規化して set。
  function numField(label: string, key: keyof UsScreenCriteria, step?: string) {
    return (
      <label className="flex flex-col">
        <span className={labelCls}>{label}</span>
        <input
          type="number"
          step={step}
          className={inputCls}
          value={draft[key] === undefined ? "" : String(draft[key])}
          onChange={(e) => set(key, toNum(e.target.value) as UsScreenCriteria[typeof key])}
        />
      </label>
    );
  }

  // 比率（0..1 を % で入出力）。配当利回り・ROE・利益率・成長率の min/max ペアで共用。
  function pctField(label: string, key: keyof UsScreenCriteria) {
    return (
      <label className="flex flex-col">
        <span className={labelCls}>{label}</span>
        <input
          type="number"
          step="0.1"
          className={inputCls}
          value={toPctStr((draft[key] as number | undefined) ?? null)}
          onChange={(e) =>
            set(key, (fromPctStr(e.target.value) ?? undefined) as UsScreenCriteria[typeof key])
          }
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
      <div className="grid grid-cols-2 gap-x-3 gap-y-2 sm:grid-cols-4 lg:grid-cols-6">
        {numField("PER 下限", "per_min", "0.1")}
        {numField("PER 上限", "per_max", "0.1")}
        {numField("PBR 下限", "pbr_min", "0.1")}
        {numField("PBR 上限", "pbr_max", "0.1")}
        {pctField("配当利回り下限 %", "dividend_yield_min")}
        {pctField("配当利回り上限 %", "dividend_yield_max")}
        {pctField("ROE 下限 %", "roe_min")}
        {pctField("ROE 上限 %", "roe_max")}
        {pctField("営業利益率下限 %", "operating_margin_min")}
        {pctField("営業利益率上限 %", "operating_margin_max")}
        {pctField("純利益率下限 %", "net_margin_min")}
        {pctField("純利益率上限 %", "net_margin_max")}
        {pctField("売上成長(YoY)下限 %", "revenue_growth_yoy_min")}
        {pctField("売上成長(YoY)上限 %", "revenue_growth_yoy_max")}
        {pctField("純益成長(YoY)下限 %", "profit_growth_yoy_min")}
        {pctField("純益成長(YoY)上限 %", "profit_growth_yoy_max")}
        {numField("時価総額($) 下限", "market_cap_min")}
        {numField("時価総額($) 上限", "market_cap_max")}
        {numField("時価総額 上位N社", "market_cap_rank_max")}
        <label className="flex flex-col">
          <span className={labelCls}>業種内PER 安い割合 % 以下</span>
          <input
            type="number"
            step="1"
            className={inputCls}
            value={toPctStr(draft.gics_sector_pctile_max ?? null)}
            onChange={(e) => set("gics_sector_pctile_max", fromPctStr(e.target.value) ?? undefined)}
          />
        </label>
        <label className="flex flex-col">
          <span className={labelCls}>GICS セクター</span>
          <select
            className={inputCls}
            value={draft.gics_sector ?? ""}
            onChange={(e) => set("gics_sector", e.target.value || undefined)}
          >
            <option value="">（すべて）</option>
            {GICS_SECTOR_OPTS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col">
          <span className={labelCls}>並び替え</span>
          <select
            className={inputCls}
            value={draft.sort_by ?? "market_cap"}
            onChange={(e) => set("sort_by", e.target.value as UsScreenCriteria["sort_by"])}
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
            onChange={(e) => set("sort_dir", e.target.value as UsScreenCriteria["sort_dir"])}
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
          ETF を除外
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
            className="rounded-md bg-accent px-3 py-1.5 font-semibold text-[12px] text-white hover:opacity-90"
          >
            絞り込む
          </button>
        </div>
      </div>
    </form>
  );
}
