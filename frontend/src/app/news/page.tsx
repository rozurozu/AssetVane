"use client";

// ニュース一覧（ADR-047・news 統合コーパス＝銘柄/セクター/市場の 3 層）。
// 上部に手入力フォーム（NewsPasteForm）、下に一覧（NewsList）。level タブ＋期間プリセットで絞る。
// 投入/削除で書き換わるため useApi ではなく useState で持つ（frontend-component-pattern (c)・操作起点）。
// 初回・タブ/期間変更は useEffect で取得（AbortSignal 対応）。データは lib/api.ts 経由（ADR-005）。

import { NewsList } from "@/components/news/NewsList";
import { NewsPasteForm } from "@/components/news/NewsPasteForm";
import { StatusBlock } from "@/components/ui/StatusBlock";
import { type NewsItem, deleteNews, getNews } from "@/lib/api";
import { useEffect, useState } from "react";

// level 切替タブ（全 / 銘柄 / セクター / 市場）。値 undefined は全 level（検索ボックスは作らない）。
const LEVEL_TABS: { key: string; label: string; value: string | undefined }[] = [
  { key: "all", label: "全", value: undefined },
  { key: "stock", label: "銘柄", value: "stock" },
  { key: "sector", label: "セクター", value: "sector" },
  { key: "market", label: "市場", value: "market" },
];

// 期間プリセット（日数 → since。0 は「全期間」で since を付けない）。
const SINCE_PRESETS: { key: string; label: string; days: number }[] = [
  { key: "7", label: "7日", days: 7 },
  { key: "30", label: "30日", days: 30 },
  { key: "90", label: "90日", days: 90 },
];

// 日数前の日付を 'YYYY-MM-DD' で返す（since の算出）。
function sinceFromDays(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

export default function NewsPage() {
  // 投入/削除で書き換わるため useState（初回・絞り込みは useEffect で取得）。
  const [items, setItems] = useState<NewsItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // 絞り込み: level タブ（undefined=全）と期間プリセットの日数。
  const [level, setLevel] = useState<string | undefined>(undefined);
  const [sinceDays, setSinceDays] = useState<number>(30);

  // 削除中の id 集合（行ボタンを無効化）。
  const [busyIds, setBusyIds] = useState<Set<number>>(new Set());

  const since = sinceFromDays(sinceDays);

  useEffect(() => {
    let ignore = false;
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);
    getNews({ level, since }, ctrl.signal)
      .then((r) => {
        if (!ignore) setItems(r.items);
      })
      .catch((e) => {
        if (ignore || ctrl.signal.aborted) return;
        setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!ignore) setLoading(false);
      });
    return () => {
      ignore = true;
      ctrl.abort();
    };
  }, [level, since]);

  // 削除（source='user' のみ・楽観的に行を除去）。
  async function handleDelete(id: number) {
    setBusyIds((prev) => new Set(prev).add(id));
    try {
      await deleteNews(id);
      setItems((prev) => prev?.filter((n) => n.id !== id) ?? prev);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  }

  return (
    <>
      <div className="mb-3">
        <div className="font-semibold text-[20px] tracking-[-0.4px]">News</div>
        <div className="mt-0.5 text-[12px] text-ink-muted">
          銘柄・セクター・市場の 3 層に分けたニュース統合コーパスなのだ。本文を貼ると AI
          が要約して取り込むのだ。
        </div>
      </div>

      {/* 手入力フォーム（投入後はフォーム上に即時追加）。 */}
      <div className="mb-3">
        <NewsPasteForm onDone={(item) => setItems((prev) => [item, ...(prev ?? [])])} />
      </div>

      {/* level 切替タブ＋期間プリセット。アクティブは surface-2 へ lift（DESIGN.md）。 */}
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <div className="flex gap-1">
          {LEVEL_TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              onClick={() => setLevel(t.value)}
              className={`rounded-md px-2.5 py-1 text-[12px] ${
                level === t.value
                  ? "bg-surface-2 font-semibold text-ink"
                  : "text-ink-muted hover:bg-surface-2 hover:text-ink"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="flex gap-1">
          {SINCE_PRESETS.map((p) => (
            <button
              key={p.key}
              type="button"
              onClick={() => setSinceDays(p.days)}
              className={`rounded-md px-2.5 py-1 text-[12px] ${
                sinceDays === p.days
                  ? "bg-surface-2 font-semibold text-ink"
                  : "text-ink-muted hover:bg-surface-2 hover:text-ink"
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      <section className="rounded-lg border border-hairline bg-surface-1">
        <StatusBlock
          loading={loading}
          error={error}
          empty={items?.length === 0}
          className="p-4"
          errorHint="backend 起動を確認するのだ。"
          emptyText="この条件のニュースはまだ無いのだ。"
        >
          <NewsList items={items ?? []} busyIds={busyIds} onDelete={handleDelete} />
        </StatusBlock>
      </section>
    </>
  );
}
