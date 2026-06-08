"use client";

// 投信 NAV 推移スパークライン（ADR-054）。getFundNavSeries の点列を SVG path にして小さく描く。
// Portfolio ページ本体の資産推移スパークライン（page.tsx の trendSvg）と同じ作りをミラーする。
//
// データ所有の例外メモ: frontend-component-pattern は「GET はページが持つ」を規約とするが、NAV 推移は
// 保有 1 行ごとに ISIN を指定して取る軽量データで、保有テーブルの外では使わない。各行で自前取得した方が
// 親の state を肥大させないため、自己完結 feature の明示例外として取得をこの中に閉じる（DB 非依存・lib/api.ts 経由）。

import { type FundNavPoint, getFundNavSeries } from "@/lib/api";
import { useApi } from "@/lib/use-api";

// スパークラインに使う点数（直近 N 点・基準価額の推移をざっくり見せるだけ）。
const NAV_POINT_LIMIT = 60;
const W = 160;
const H = 32;

/** NAV 点列から SVG path（d）と末端座標を組む。点が 2 未満なら null。 */
function buildPath(points: FundNavPoint[]): { d: string; lastX: number; lastY: number } | null {
  const vals = points.map((p) => p.nav).filter((v): v is number => v != null);
  if (vals.length < 2) return null;
  const minV = Math.min(...vals);
  const maxV = Math.max(...vals);
  const range = maxV - minV || 1;
  // nav が null の点は描画から落とす（前後を直線で繋ぐ）。
  const drawable = points.filter((p): p is FundNavPoint & { nav: number } => p.nav != null);
  const d = drawable
    .map((p, i) => {
      const x = (i / (drawable.length - 1)) * W;
      const y = H - ((p.nav - minV) / range) * (H - 6) - 3;
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const lastV = drawable[drawable.length - 1].nav;
  return { d, lastX: W, lastY: H - ((lastV - minV) / range) * (H - 6) - 3 };
}

export function FundNavSparkline({ isin }: { isin: string }) {
  const { data, error, loading } = useApi(
    (s) => getFundNavSeries(isin, NAV_POINT_LIMIT, s),
    [isin],
  );

  if (loading) return <span className="text-[11px] text-ink-subtle">…</span>;
  if (error || !data) return <span className="text-[11px] text-ink-subtle">—</span>;

  const path = buildPath(data);
  if (!path) return <span className="text-[11px] text-ink-subtle">—</span>;

  // 期間の騰落で線色を上げ下げ（NAV 上昇=up・下落=down）。
  const first = data.find((p) => p.nav != null)?.nav ?? null;
  const last = [...data].reverse().find((p) => p.nav != null)?.nav ?? null;
  const up = first != null && last != null ? last >= first : true;
  const stroke = up ? "var(--color-up)" : "var(--color-down)";

  return (
    <svg
      role="img"
      viewBox={`0 0 ${W} ${H}`}
      width={W}
      height={H}
      preserveAspectRatio="none"
      aria-label="NAV 推移"
    >
      <path d={path.d} fill="none" stroke={stroke} strokeWidth={1.4} />
      <circle cx={path.lastX} cy={path.lastY} r={1.8} fill={stroke} />
    </svg>
  );
}
