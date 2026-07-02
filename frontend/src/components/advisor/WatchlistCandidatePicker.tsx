"use client";

// AI が提示したウォッチ候補のチェックリスト（ADR-080・screens.md 相談チャット）。
// propose_watchlist が返した候補を assistant バブルの下に出し、ユーザーが選んで watchlist に追加する
// （追加は UI 側＝AI は watchlist を書かない・承認制の精神＝ADR-009）。フローティングと /advisor の
// 両方で同じ ChatConversation から描かれるので、これ 1 個で両画面に出る。
// - GET /watchlist でライブ照合し、既に入っている銘柄は ✓ 追加済み（押せない）にする。
// - 追加は postWatchlist(code, reason)。reason は watchlist の note に焼く（冪等 do_nothing・ADR-080）。
// ※ feature が内部 GET するのは「常駐 Advisor チャット」の明示例外（frontend-component-pattern・
//    ADR-024）。読み取りは read-only フック useApi 経由に閉じる。

import type { WatchlistCandidate } from "@/lib/api";
import { getWatchlist, postWatchlist } from "@/lib/api";
import { useApi } from "@/lib/use-api";
import Link from "next/link";
import { useCallback, useMemo, useState } from "react";

type Props = {
  candidates: WatchlistCandidate[];
};

export function WatchlistCandidatePicker({ candidates }: Props) {
  // 既存 watchlist をライブ照合（読み取り専用・useApi）。失敗しても候補は出す（追加は冪等）。
  const { data: watchlist } = useApi((s) => getWatchlist(s), []);
  // チェック状態（未指定は「watchlist 未登録なら既定チェック」）／楽観的に追加済みにした code。
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const [added, setAdded] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const existing = useMemo(() => new Set((watchlist?.items ?? []).map((i) => i.code)), [watchlist]);
  const inWatchlist = useCallback(
    (code: string) => existing.has(code) || added.has(code),
    [existing, added],
  );
  const isChecked = useCallback(
    (code: string) => checked[code] ?? !inWatchlist(code),
    [checked, inWatchlist],
  );

  // 追加対象＝未登録かつチェック済み。
  const selected = useMemo(
    () => candidates.filter((c) => !inWatchlist(c.code) && isChecked(c.code)),
    [candidates, inWatchlist, isChecked],
  );

  const toggle = useCallback((code: string) => {
    setChecked((m) => ({ ...m, [code]: !(m[code] ?? true) }));
  }, []);

  const addSelected = useCallback(async () => {
    if (selected.length === 0 || busy) return;
    setBusy(true);
    setError(null);
    const done: string[] = [];
    const failed: string[] = [];
    for (const c of selected) {
      try {
        await postWatchlist(c.code, c.reason || undefined);
        done.push(c.code);
      } catch {
        failed.push(c.code);
      }
    }
    if (done.length > 0) {
      setAdded((s) => {
        const next = new Set(s);
        for (const code of done) next.add(code);
        return next;
      });
    }
    if (failed.length > 0) setError(`追加できなかった銘柄: ${failed.join(" / ")}`);
    setBusy(false);
  }, [selected, busy]);

  if (candidates.length === 0) return null;

  return (
    <div className="flex flex-col gap-1 rounded-md border border-hairline bg-surface-2 p-2">
      <div className="flex items-center gap-1.5 text-[11px] text-ink-muted">
        🔖 ウォッチ候補（AI 提示）
        <span className="ml-auto text-ink-subtle">{candidates.length} 件</span>
      </div>

      <ul className="flex flex-col">
        {candidates.map((c) => {
          const inList = inWatchlist(c.code);
          return (
            <li key={c.code} className="flex items-center gap-2 py-0.5">
              {inList ? (
                <span className="grid h-3.5 w-3.5 place-items-center text-[11px] text-up">✓</span>
              ) : (
                <input
                  type="checkbox"
                  checked={isChecked(c.code)}
                  onChange={() => toggle(c.code)}
                  disabled={busy}
                  className="h-3.5 w-3.5 accent-accent"
                  aria-label={`${c.company_name || c.code} をウォッチ候補に選ぶ`}
                />
              )}
              <span className="num text-[11px] text-ink-muted tabular-nums">{c.code}</span>
              <span className="shrink-0 text-[12px] text-ink">{c.company_name || c.code}</span>
              {c.reason && (
                <span className="truncate text-[11px] text-ink-subtle" title={c.reason}>
                  {c.reason}
                </span>
              )}
              {inList && (
                <span className="ml-auto shrink-0 text-[10px] text-ink-subtle">追加済み</span>
              )}
            </li>
          );
        })}
      </ul>

      {error && <div className="text-[11px] text-down">⚠ {error}</div>}

      <div className="flex items-center gap-2 pt-0.5">
        <button
          type="button"
          onClick={addSelected}
          disabled={selected.length === 0 || busy}
          className="rounded-md bg-accent px-2 py-1 text-[11px] text-white disabled:opacity-50"
        >
          {busy ? "追加中…" : `選択 ${selected.length} 件を追加`}
        </button>
        <Link
          href="/watchlist"
          className="ml-auto rounded-md border border-hairline px-2 py-1 text-[11px] text-accent hover:bg-surface-1"
        >
          ウォッチリスト
        </Link>
      </div>
    </div>
  );
}
