"use client";

import appIcon from "@/app/icon.png";
import { nav } from "@/lib/mock-data";
import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";

// サイドバー 220px / surface-1。アクティブは lift（surface-2）＋左に青の inset bar で示す
// （青の面塗りはしない＝DESIGN.md）。ナビ項目は Phase 進行で増える（screens.md §2）。
// href があるものは Link で遷移、無いもの（未投入 Phase）は非活性ボタンのまま。
const ACTIVE = "bg-surface-2 font-semibold text-ink shadow-[inset_2px_0_0_var(--color-accent)]";
const IDLE = "text-ink-muted hover:bg-surface-2 hover:text-ink";
const ROW = "flex w-full items-center gap-2.5 rounded-md px-2.5 py-1.5 text-left text-[13px]";

function isActive(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
}

function NavRowContent({ icon, label, phase }: { icon: string; label: string; phase?: string }) {
  return (
    <>
      <span className="w-[15px] text-center opacity-85">{icon}</span>
      {label}
      {phase && <span className="ml-auto text-[10px] text-ink-subtle">{phase}</span>}
    </>
  );
}

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="flex flex-col border-hairline border-r bg-surface-1">
      <div className="flex h-12 items-center gap-2 border-hairline border-b px-3.5">
        {/* ブランドマーク＝アプリアイコン（app/icon.png をそのまま流用＝画像の一元管理） */}
        <Image src={appIcon} alt="AssetVane" width={20} height={20} className="h-5 w-5" priority />
        <b className="font-semibold text-[14px] tracking-[-0.2px]">AssetVane</b>
      </div>

      <nav className="flex flex-1 flex-col gap-px overflow-auto p-2">
        {nav.map((section, i) => (
          // biome-ignore lint/suspicious/noArrayIndexKey: 静的なナビ定義で並びは不変
          <div key={i} className="flex flex-col gap-px">
            {section.group && (
              <div className="px-2 pt-2.5 pb-1 font-medium text-[11px] text-ink-subtle uppercase tracking-[0.3px]">
                {section.group}
              </div>
            )}
            {section.items.map((item) => {
              const href = "href" in item ? item.href : undefined;
              const phase = "phase" in item ? item.phase : undefined;
              return href ? (
                <Link
                  key={item.label}
                  href={href}
                  className={`${ROW} ${isActive(pathname, href) ? ACTIVE : IDLE}`}
                >
                  <NavRowContent icon={item.icon} label={item.label} phase={phase} />
                </Link>
              ) : (
                <button
                  type="button"
                  key={item.label}
                  disabled
                  className={`${ROW} cursor-default text-ink-subtle`}
                >
                  <NavRowContent icon={item.icon} label={item.label} phase={phase} />
                </button>
              );
            })}
          </div>
        ))}
      </nav>

      <div className="border-hairline border-t px-3.5 py-2.5 text-[11px] text-ink-subtle">
        単一ユーザー / LAN / 提示のみ
      </div>
    </aside>
  );
}
