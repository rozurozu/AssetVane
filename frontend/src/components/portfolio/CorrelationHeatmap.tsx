// 相関ヒートマップ（screens.md #5・phase2-spec.md §6）。
// CorrelationMatrix を SVG グリッドで描画。
// 正相関（+1）= accent 寄り・負相関（-1）= down 寄り・0 付近は中立（surface-2 相当）。
// 対角（自己相関=1.0）は除外表示。density-first・数値は num クラス。

import type { CorrelationMatrix } from "@/lib/api";

// -1..1 の相関係数を「accent↔中立↔down」の RGB 補間で色変換する（CSS変数を使えないため JS で計算）。
function corrToColor(v: number): string {
  // accent = #0099ff（青）/ down = #ef4444（赤）/ neutral = #1c1c1c（surface-2 相当）
  if (v >= 0) {
    // 0（neutral）→ +1（accent 青）
    const t = v;
    const r = Math.round(0x1c + (0x00 - 0x1c) * t);
    const g = Math.round(0x1c + (0x99 - 0x1c) * t);
    const b = Math.round(0x1c + (0xff - 0x1c) * t);
    return `rgb(${r},${g},${b})`;
  }
  // 0（neutral）→ -1（down 赤）
  const t = -v;
  const r = Math.round(0x1c + (0xef - 0x1c) * t);
  const g = Math.round(0x1c + (0x44 - 0x1c) * t);
  const b = Math.round(0x1c + (0x44 - 0x1c) * t);
  return `rgb(${r},${g},${b})`;
}

type Props = {
  data: CorrelationMatrix;
};

export function CorrelationHeatmap({ data }: Props) {
  const { codes, labels, matrix } = data;
  const n = codes.length;

  // 2銘柄未満では意味のある相関が出ないのだ。
  if (n < 2) {
    return (
      <div className="py-4 text-center text-[13px] text-ink-subtle">
        相関は 2 銘柄以上で表示されるのだ
      </div>
    );
  }

  const CELL = 36; // 1 セルの px
  const LABEL_W = 52; // 左ラベル幅
  const LABEL_H = 52; // 上ラベル高
  const svgW = LABEL_W + n * CELL;
  const svgH = LABEL_H + n * CELL;

  return (
    <div className="overflow-x-auto">
      <svg
        role="img"
        aria-label="保有銘柄の相関ヒートマップ"
        width={svgW}
        height={svgH}
        viewBox={`0 0 ${svgW} ${svgH}`}
        style={{ maxWidth: "100%" }}
      >
        {/* 上部 列ラベル（コード）*/}
        {codes.map((code, j) => (
          <text
            key={`col-${code}`}
            x={LABEL_W + j * CELL + CELL / 2}
            y={LABEL_H - 6}
            textAnchor="middle"
            fontSize={10}
            fill="var(--color-ink-muted)"
            style={{ fontFeatureSettings: '"tnum"' }}
          >
            {code}
          </text>
        ))}

        {/* 左側 行ラベル（company_name の先頭 5 文字 or コード）*/}
        {codes.map((code, i) => (
          <text
            key={`row-${code}`}
            x={LABEL_W - 4}
            y={LABEL_H + i * CELL + CELL / 2 + 4}
            textAnchor="end"
            fontSize={10}
            fill="var(--color-ink-muted)"
          >
            {labels[i] ? labels[i].slice(0, 5) : code}
          </text>
        ))}

        {/* セルグリッド */}
        {matrix.map((row, i) =>
          row.map((v, j) => {
            const isDiag = i === j;
            const cx = LABEL_W + j * CELL;
            const cy = LABEL_H + i * CELL;
            const cellColor = isDiag ? "var(--color-surface-2)" : corrToColor(v);
            const textColor = isDiag
              ? "var(--color-ink-subtle)"
              : Math.abs(v) > 0.5
                ? "var(--color-ink)"
                : "var(--color-ink-muted)";
            return (
              // biome-ignore lint/suspicious/noArrayIndexKey: 固定グリッドのため index キーで問題なし
              <g key={`${i}-${j}`}>
                <rect
                  x={cx + 1}
                  y={cy + 1}
                  width={CELL - 2}
                  height={CELL - 2}
                  fill={cellColor}
                  rx={2}
                />
                {!isDiag && (
                  <text
                    x={cx + CELL / 2}
                    y={cy + CELL / 2 + 4}
                    textAnchor="middle"
                    fontSize={10}
                    fill={textColor}
                    style={{ fontFeatureSettings: '"tnum"' }}
                  >
                    {v.toFixed(2)}
                  </text>
                )}
              </g>
            );
          }),
        )}
      </svg>
      {/* 凡例 */}
      <div className="mt-1 flex items-center gap-2 text-[11px] text-ink-subtle">
        <span
          className="inline-block h-2.5 w-4 rounded-sm"
          style={{ background: corrToColor(-1) }}
        />
        <span>−1 負相関</span>
        <span
          className="ml-2 inline-block h-2.5 w-4 rounded-sm"
          style={{ background: corrToColor(0) }}
        />
        <span>0 無相関</span>
        <span
          className="ml-2 inline-block h-2.5 w-4 rounded-sm"
          style={{ background: corrToColor(1) }}
        />
        <span>+1 正相関</span>
      </div>
    </div>
  );
}
