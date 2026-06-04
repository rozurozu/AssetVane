// 共有 UI プリミティブ: Card（screens.md・density-first）。
// 罫線で区切り、余白は 12px に絞る。各ページで local 再定義しない（frontend-component-pattern）。
// 純表示プリミティブなので Server Component のまま（DB に触れない・ADR-005 と無矛盾）。

import type { ReactNode } from "react";

type Props = {
  title: ReactNode;
  meta?: string; // ヘッダ右の鮮度・遅延注記など
  link?: ReactNode; // ヘッダ右のリンク（"すべて" 等）
  children: ReactNode;
};

export function Card({ title, meta, link, children }: Props) {
  return (
    <section className="rounded-lg border border-hairline bg-surface-1">
      <div className="flex items-center justify-between border-hairline border-b px-3 py-2">
        <h2 className="font-semibold text-[14px] tracking-[-0.1px]">{title}</h2>
        {meta && <span className="text-[11px] text-ink-subtle">{meta}</span>}
        {link}
      </div>
      <div className="p-3">{children}</div>
    </section>
  );
}
