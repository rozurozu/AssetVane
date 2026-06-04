// 共有 UI プリミティブ: DataTable（罫線テーブル）と Td（セル）。
// 全ページで重複していた thead マッピング（h-8 罫線・uppercase ヘッダ）を一本化する
// （frontend-component-pattern）。density-first・DESIGN.md トークン。
// セルの細かな装飾は Td の className で足す（数値色・font-semibold 等）。

import type { ReactNode } from "react";

/** 列定義。right=true で右寄せ（数値列）。 */
export type Column = { label: string; right?: boolean };

type TableProps = {
  columns: Column[];
  children: ReactNode; // <tbody> の中身（行）
};

export function DataTable({ columns, children }: TableProps) {
  return (
    <table className="w-full border-collapse">
      <thead>
        <tr>
          {columns.map((c) => (
            <th
              key={c.label}
              className={`h-8 border-hairline border-b px-2.5 font-medium text-[11px] text-ink-muted uppercase tracking-[0.3px] ${
                c.right ? "text-right" : "text-left"
              }`}
            >
              {c.label}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>{children}</tbody>
    </table>
  );
}

type TdProps = {
  children: ReactNode;
  right?: boolean;
  className?: string; // 数値色・font-semibold 等の追加装飾
};

export function Td({ children, right, className = "" }: TdProps) {
  return (
    <td
      className={`h-[34px] border-hairline-soft border-b px-2.5 text-[13px] ${
        right ? "text-right" : ""
      } ${className}`.trim()}
    >
      {children}
    </td>
  );
}
