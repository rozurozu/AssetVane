// 整形ヘルパの集約（frontend-component-pattern）。各ページ・コンポーネントで再定義しない。
// 比率・weight は内部 0..1・UI でのみ ×100 して %（ADR-008）。計算はしない（表示整形のみ）。

/** 円整形。null/undefined は "—"。例: 1234567 → "¥1,234,567"。 */
export function fmtJpy(v: number | null | undefined): string {
  if (v == null) return "—";
  return `¥${v.toLocaleString("ja-JP", { maximumFractionDigits: 0 })}`;
}

/** 0..1 → "X%"。null/undefined は "—"。digits は小数桁（既定 1・ダッシュボードの整数表示は digits=0）。 */
export function pct(v: number | null | undefined, digits = 1): string {
  if (v == null) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

/** 0..1 → 符号付き "+X%" / "-X%"（delta 表示用・digits 既定 1）。 */
export function deltaPct(v: number, digits = 1): string {
  const s = (v * 100).toFixed(digits);
  return v >= 0 ? `+${s}%` : `${s}%`;
}

/** 0..1 → 編集用の数値文字列（"%" 記号なし）。null は ""。例: 0.255 → "25.5"（PolicyEditor）。 */
export function toPctStr(v: number | null | undefined): string {
  return v == null ? "" : String(Math.round(v * 1000) / 10);
}

/** 編集入力（"%" 値の文字列）→ 0..1。空・非数は null（PolicyEditor）。 */
export function fromPctStr(s: string): number | null {
  const t = s.trim();
  if (t === "") return null;
  const n = Number(t);
  if (Number.isNaN(n)) return null;
  return n / 100;
}

/** PER/PBR 等の倍率。null は "—"。例: 12.345 → "12.3"（digits 既定 1・スクリーナー）。 */
export function fmtRatio(v: number | null | undefined, digits = 1): string {
  if (v == null) return "—";
  return v.toFixed(digits);
}

/** 時価総額（円）を兆/億で簡潔表示。null は "—"。例: 2.5e12 → "2.5兆円"・5e10 → "500億円"。 */
export function fmtMarketCap(v: number | null | undefined): string {
  if (v == null) return "—";
  const oku = v / 1e8; // 億円単位
  if (oku >= 10000) return `${(oku / 10000).toFixed(1)}兆円`;
  return `${Math.round(oku).toLocaleString("ja-JP")}億円`;
}

/** 編集入力の文字列 → number | undefined（空・非数は undefined・スクリーナーのレンジ入力）。 */
export function toNum(s: string): number | undefined {
  const t = s.trim();
  if (t === "") return undefined;
  const n = Number(t);
  return Number.isNaN(n) ? undefined : n;
}

/** ドル整形（米株・ADR-055・B-1 は USD 表示）。null/undefined は "—"。例: 1234.5 → "$1,235"。 */
export function fmtUsd(v: number | null | undefined): string {
  if (v == null) return "—";
  return `$${v.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}

/** 米株時価総額（USD）を T/B/M で簡潔表示（米株・ADR-055）。null は "—"。例: 2.5e12 → "$2.5T"・5e9 → "$5.0B"。 */
export function fmtMarketCapUsd(v: number | null | undefined): string {
  if (v == null) return "—";
  if (v >= 1e12) return `$${(v / 1e12).toFixed(1)}T`;
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
  return `$${Math.round(v).toLocaleString("en-US")}`;
}
