"use client";

// 投信 NAV 推移スパークライン（ADR-054）。getFundNavSeries の点列を SVG path にして小さく描く。
// path 構築は資産推移と共通の純関数 buildSparklinePath を使う（review-2026-06-12 §3・重複解消）。
// 行内ミニ表示で up/down 色分け＋小さい末端 dot という見た目は資産推移（accent 単色・大サイズ）と役割が
// 違うため、TrendSparkline コンポーネント自体は使わず純関数だけを共有する。
//
// データ所有の例外メモ: frontend-component-pattern は「GET はページが持つ」を規約とするが、NAV 推移は
// 保有 1 行ごとに ISIN を指定して取る軽量データで、保有テーブルの外では使わない。各行で自前取得した方が
// 親の state を肥大させないため、自己完結 feature の明示例外として取得をこの中に閉じる（DB 非依存・lib/api.ts 経由）。

import { buildSparklinePath } from "@/components/chart/TrendSparkline";
import { getFundNavSeries } from "@/lib/api";
import { useApi } from "@/lib/use-api";

// スパークラインに使う点数（直近 N 点・基準価額の推移をざっくり見せるだけ）。
const NAV_POINT_LIMIT = 60;
const W = 160;
const H = 32;

export function FundNavSparkline({ isin }: { isin: string }) {
  const { data, error, loading } = useApi(
    (s) => getFundNavSeries(isin, NAV_POINT_LIMIT, s),
    [isin],
  );

  if (loading) return <span className="text-[11px] text-ink-subtle">…</span>;
  if (error || !data) return <span className="text-[11px] text-ink-subtle">—</span>;

  // nav が null の点は描画から落とす（前後を直線で繋ぐ）。
  const vals = data.map((p) => p.nav).filter((v): v is number => v != null);
  const path = buildSparklinePath(vals, { width: W, height: H, padTop: 3, padBottom: 3 });
  if (!path) return <span className="text-[11px] text-ink-subtle">—</span>;

  // 期間の騰落で線色を上げ下げ（NAV 上昇=up・下落=down）。vals は 2 点以上が保証される（path 非 null）。
  const stroke = vals[vals.length - 1] >= vals[0] ? "var(--color-up)" : "var(--color-down)";

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
