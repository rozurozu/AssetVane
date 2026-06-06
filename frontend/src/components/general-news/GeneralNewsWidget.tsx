// 一般ニュース widget（ADR-034）。銘柄に紐づかない市況・マクロ・世界情勢をカテゴリ別に眺める。
// data を props で受け取り描画するだけ（自身では fetch しない＝frontend-component-pattern）。
// 純表示なので装飾は DESIGN.md トークンで合わせる（生色は使わない）。

import { Card } from "@/components/ui/Card";
import type { GeneralNewsResponse } from "@/lib/api";

type Props = {
  data: GeneralNewsResponse | null;
};

export function GeneralNewsWidget({ data }: Props) {
  // 1 件でも記事があるカテゴリのみ表示（空カテゴリは畳む）。
  const categories = (data?.categories ?? []).filter((c) => c.items.length > 0);

  return (
    <Card title="一般ニュース" meta="市況・マクロ・世界情勢">
      {categories.length === 0 ? (
        <div className="py-4 text-center text-[13px] text-ink-subtle">
          まだニュースがないのだ。夜間バッチで取得されるのだ。
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {categories.map((cat) => (
            <div key={cat.label}>
              <div className="mb-1.5 font-medium text-[11px] text-ink-muted uppercase tracking-[0.2px]">
                {cat.label}
              </div>
              <ul className="flex flex-col">
                {cat.items.map((item) => (
                  <li
                    key={item.url}
                    className="border-hairline-soft border-b py-1.5 last:border-b-0"
                  >
                    <a
                      href={item.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-[13px] text-ink leading-[1.4] hover:text-accent"
                    >
                      {item.title ?? item.url}
                    </a>
                    {item.summary && (
                      <p className="mt-0.5 text-[12px] text-ink-muted leading-[1.45]">
                        {item.summary}
                      </p>
                    )}
                    {item.published_at && (
                      <span className="num mt-0.5 block text-[11px] text-ink-subtle">
                        {item.published_at}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
