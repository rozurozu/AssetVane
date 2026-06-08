// ニュース一覧（ADR-047・/news ページ）。取得済み items を props で受けて描画する純表示 feature。
// 列＝日付（published_at）／level・source バッジ／要約（title 優先・無ければ summary）／
// URL（"user://" 始まりはリンク化しない・他は外部リンク）／削除（source==='user' のみ・busy で「削除中…」）。
// DataTable+Td を使う（frontend-component-pattern）。GET はページが持ち、ここは描画＋削除トリガのみ。

import { DataTable, Td } from "@/components/ui/DataTable";
import type { NewsItem } from "@/lib/api";

type Props = {
  items: NewsItem[];
  busyIds: Set<number>; // 削除中の id 集合（行ボタンを無効化）
  onDelete: (id: number) => void;
};

// level の表示ラベル（3 層・未知値はそのまま出す）。
const LEVEL_LABELS: Record<string, string> = {
  stock: "銘柄",
  sector: "セクター",
  market: "市場",
};

// 手入力ニュースの URL スキーム（外部リンク化しない・ADR-047）。
const USER_URL_PREFIX = "user://";

export function NewsList({ items, busyIds, onDelete }: Props) {
  return (
    <DataTable
      columns={[
        { label: "日付" },
        { label: "区分" },
        { label: "要約" },
        { label: "リンク" },
        { label: "", right: true },
      ]}
    >
      {items.map((n) => {
        const busy = busyIds.has(n.id);
        const isUserUrl = n.url.startsWith(USER_URL_PREFIX);
        const headline = n.title ?? n.summary ?? "—";
        return (
          <tr key={n.id} className="hover:[&>td]:bg-surface-2">
            <Td>
              <span className="num text-[12px] text-ink-subtle">
                {n.published_at ? n.published_at.slice(0, 10) : "—"}
              </span>
            </Td>
            <Td>
              <span className="inline-flex items-center gap-1">
                <span className="rounded-sm bg-surface-2 px-1.5 py-0.5 text-[11px] text-ink-muted">
                  {LEVEL_LABELS[n.level] ?? n.level}
                </span>
                {n.source && (
                  <span className="rounded-sm bg-surface-2 px-1.5 py-0.5 text-[11px] text-ink-subtle">
                    {n.source}
                  </span>
                )}
              </span>
            </Td>
            <Td>
              <span className="text-[13px] text-ink">{headline}</span>
            </Td>
            <Td>
              {isUserUrl ? (
                <span className="text-[12px] text-ink-subtle">手入力</span>
              ) : (
                <a
                  href={n.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-[12px] text-accent hover:underline"
                >
                  開く
                </a>
              )}
            </Td>
            <Td right>
              {n.source === "user" && (
                <button
                  type="button"
                  onClick={() => onDelete(n.id)}
                  disabled={busy}
                  className="rounded-md px-2 py-1 text-[12px] text-ink-subtle hover:text-down disabled:text-ink-subtle"
                >
                  {busy ? "削除中…" : "削除"}
                </button>
              )}
            </Td>
          </tr>
        );
      })}
    </DataTable>
  );
}
