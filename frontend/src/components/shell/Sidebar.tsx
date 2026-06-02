import { nav } from "@/lib/mock-data";

// サイドバー 220px / surface-1。アクティブは lift（surface-2）＋左に青の inset bar で示す
// （青の面塗りはしない＝DESIGN.md）。ナビ項目は Phase 進行で増える（screens.md §2）。
export function Sidebar() {
  return (
    <aside className="flex flex-col border-hairline border-r bg-surface-1">
      <div className="flex h-12 items-center gap-2 border-hairline border-b px-3.5">
        <span className="grid h-5 w-5 place-items-center rounded-md bg-accent font-bold text-[12px] text-white">
          ⚲
        </span>
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
            {section.items.map((item) => (
              <button
                type="button"
                key={item.label}
                className={`flex w-full items-center gap-2.5 rounded-md px-2.5 py-1.5 text-left text-[13px] ${
                  "active" in item && item.active
                    ? "bg-surface-2 font-semibold text-ink shadow-[inset_2px_0_0_var(--color-accent)]"
                    : "text-ink-muted hover:bg-surface-2 hover:text-ink"
                }`}
              >
                <span className="w-[15px] text-center opacity-85">{item.icon}</span>
                {item.label}
                {"phase" in item && item.phase && (
                  <span className="ml-auto text-[10px] text-ink-subtle">{item.phase}</span>
                )}
              </button>
            ))}
          </div>
        ))}
      </nav>

      <div className="border-hairline border-t px-3.5 py-2.5 text-[11px] text-ink-subtle">
        単一ユーザー / LAN / 提示のみ
      </div>
    </aside>
  );
}
