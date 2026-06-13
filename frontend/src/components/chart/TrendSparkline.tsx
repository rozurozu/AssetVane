"use client";

// 資産推移スパークライン（Dashboard / Portfolio 共用・review-2026-06-12 §3）。点列を gradient fill 付きの
// 折れ線で描く。以前は app/page.tsx の trendPath・portfolio/page.tsx の trendSvg・FundNavSparkline が
// 「点列→min/max 正規化→SVG path 文字列」の同型ロジックを各々手書きしていた（3 重複）。path 構築を純関数
// buildSparklinePath に集約し、資産推移の見た目（accent 単色＋末端 dot＋任意の罫線/日付フッタ）はこの
// コンポーネントに一本化した（frontend-component-pattern「繰り返し構造はコンポーネント抽出」）。
// 色は DESIGN.md トークン（accent / hairline-soft）。生色は使わない。

import { useId } from "react";

/** スパークライン path の構築オプション。padTop/padBottom は描画域の上下余白（px）。 */
type PathOpts = { width: number; height: number; padTop: number; padBottom: number };

/**
 * 数値点列から SVG path（d）と末端座標を組む純関数（review-2026-06-12 §3）。
 * 点が 2 未満なら null。y は上下 padding を除いた描画域に min..max を正規化して写す。
 * FundNavSparkline・TrendSparkline の双方から使う（重複していた構築ロジックの単一の正本）。
 */
export function buildSparklinePath(
  values: number[],
  { width, height, padTop, padBottom }: PathOpts,
): { d: string; lastX: number; lastY: number } | null {
  if (values.length < 2) return null;
  const minV = Math.min(...values);
  const maxV = Math.max(...values);
  const range = maxV - minV || 1;
  const span = height - padTop - padBottom;
  const yOf = (v: number) => height - ((v - minV) / range) * span - padBottom;
  const d = values
    .map(
      (v, i) =>
        `${i === 0 ? "M" : "L"}${((i / (values.length - 1)) * width).toFixed(1)},${yOf(v).toFixed(1)}`,
    )
    .join(" ");
  return { d, lastX: width, lastY: yOf(values[values.length - 1]) };
}

// 資産推移は横幅いっぱい（preserveAspectRatio=none）に伸ばすため width は固定 720 で viewBox 基準にする。
const W = 720;

type Props = {
  /** 総資産などの点列（null 除去済み・2 点以上を想定。2 未満は何も描かない）。 */
  values: number[];
  /** path 描画高さ（px・既定 120）。viewBox 高さは下端まで塗る余白 +10。 */
  height?: number;
  /** 上下余白（px・既定 10）。 */
  padY?: number;
  /** 水平罫線の y 座標（px・既定なし）。 */
  gridLines?: number[];
  /** 下部の開始/終了ラベル（任意）。 */
  footer?: { start?: string; end?: string };
  ariaLabel?: string;
};

/** 資産推移の gradient fill スパークライン（Dashboard / Portfolio 共用）。 */
export function TrendSparkline({
  values,
  height = 120,
  padY = 10,
  gridLines = [],
  footer,
  ariaLabel = "資産推移",
}: Props) {
  // 同一ページに複数描画しても gradient が衝突しないよう id を一意化する。
  const gradId = useId();
  const path = buildSparklinePath(values, { width: W, height, padTop: padY, padBottom: padY });
  if (!path) return null;
  const viewH = height + 10; // 下端まで塗るための余白（従来の 130 / 110 と同値）。

  return (
    <>
      <svg
        role="img"
        viewBox={`0 0 ${W} ${viewH}`}
        width="100%"
        height={viewH}
        preserveAspectRatio="none"
        aria-label={ariaLabel}
      >
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--color-accent)" stopOpacity={0.22} />
            <stop offset="100%" stopColor="var(--color-accent)" stopOpacity={0} />
          </linearGradient>
        </defs>
        {gridLines.map((gy) => (
          <line key={gy} x1="0" y1={gy} x2={W} y2={gy} stroke="var(--color-hairline-soft)" />
        ))}
        <path d={`${path.d} L${W},${viewH} L0,${viewH} Z`} fill={`url(#${gradId})`} />
        <path d={path.d} fill="none" stroke="var(--color-accent)" strokeWidth={1.8} />
        <circle cx={path.lastX} cy={path.lastY} r={3} fill="var(--color-accent)" />
      </svg>
      {footer && (
        <div className="num mt-1.5 flex justify-between text-[11px] text-ink-subtle">
          <span>{footer.start}</span>
          <span>{footer.end}</span>
        </div>
      )}
    </>
  );
}
