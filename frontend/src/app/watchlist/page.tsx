"use client";

// Watchlist 一覧（screens.md #11・phase4-spec.md §6）。監視銘柄を最終調査日つきで並べ、
// stale（21 日超過・backend 算出＝L-22）を警告色のバッジで示し、「調査/再調査」ボタン
// （investigateStock・同期＝L-23 でローディング）と削除、銘柄追加 UI を持つ。
// Dashboard モック watchlist の実配線版。データは lib/api.ts 経由（ADR-005）。density-first。

import { DataTable, Td } from "@/components/ui/DataTable";
import { inputCls } from "@/components/ui/Field";
import { StatusBlock } from "@/components/ui/StatusBlock";
import {
  type WatchlistItem,
  addWatchlist,
  getWatchlist,
  investigateStock,
  removeWatchlist,
} from "@/lib/api";
import Link from "next/link";
import { useEffect, useState } from "react";

export default function WatchlistPage() {
  // 操作（追加/削除/調査）で書き換わるため useApi ではなく useState で持つ
  // （frontend-component-pattern (c)・操作起点の更新）。初回は useEffect で取得。
  const [items, setItems] = useState<WatchlistItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // 銘柄追加フォーム。
  const [code, setCode] = useState("");
  const [adding, setAdding] = useState(false);
  const [addErr, setAddErr] = useState<string | null>(null);

  // 行ごとの「処理中」状態（調査中/削除中の id 集合）。
  const [busyIds, setBusyIds] = useState<Set<number>>(new Set());

  useEffect(() => {
    let ignore = false;
    const ctrl = new AbortController();
    getWatchlist(ctrl.signal)
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
  }, []);

  // id を busy 集合に出し入れするヘルパ。
  function setBusy(id: number, on: boolean) {
    setBusyIds((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  // 銘柄を追加（重複でも backend は既存行を 200 で返す＝重複表示にしない）。
  async function onAdd() {
    const c = code.trim();
    if (c === "") return;
    setAdding(true);
    setAddErr(null);
    try {
      const item = await addWatchlist(c);
      // 既存（重複）なら差し替え、新規なら先頭に積む。
      setItems((prev) => {
        const base = prev ?? [];
        const rest = base.filter((w) => w.id !== item.id);
        return [item, ...rest];
      });
      setCode("");
    } catch (e) {
      setAddErr(e instanceof Error ? e.message : String(e));
    } finally {
      setAdding(false);
    }
  }

  // 調査/再調査（同期・完了まで待つ＝L-23）。完了後に当該行の最終調査日を更新。
  async function onInvestigate(item: WatchlistItem) {
    setBusy(item.id, true);
    try {
      const res = await investigateStock(item.code);
      setItems((prev) =>
        (prev ?? []).map((w) =>
          w.code === item.code
            ? { ...w, last_investigated_at: res.dossier.last_investigated_at, stale: false }
            : w,
        ),
      );
    } catch {
      // 失敗時は何もしない（行は残す・別途リトライ可）。
    } finally {
      setBusy(item.id, false);
    }
  }

  // 削除（存在しない id でも 200・楽観的に行を除去）。
  async function onRemove(item: WatchlistItem) {
    setBusy(item.id, true);
    try {
      await removeWatchlist(item.id);
      setItems((prev) => (prev ?? []).filter((w) => w.id !== item.id));
    } catch {
      setBusy(item.id, false);
    }
  }

  return (
    <>
      <div className="mb-3">
        <div className="font-semibold text-[20px] tracking-[-0.4px]">Watchlist</div>
        <div className="mt-0.5 text-[12px] text-ink-muted">
          監視銘柄を夜間に軽く調査するのだ。21
          日以上調査されていないものは警告色で再調査を促すのだ。
        </div>
      </div>

      {/* 銘柄追加 UI（code 入力 → addWatchlist）。 */}
      <div className="mb-3 flex items-end gap-2 rounded-lg border border-hairline bg-surface-1 p-3">
        <div className="w-40">
          <label htmlFor="wl-code" className="mb-0.5 block text-[11px] text-ink-muted">
            銘柄コード
          </label>
          <input
            id="wl-code"
            className={inputCls}
            value={code}
            onChange={(e) => setCode(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") onAdd();
            }}
            placeholder="例: 7203"
          />
        </div>
        <button
          type="button"
          onClick={onAdd}
          disabled={adding || code.trim() === ""}
          className="rounded-md bg-accent px-3 py-1.5 text-[13px] text-white disabled:bg-surface-2 disabled:text-ink-subtle"
        >
          {adding ? "追加中…" : "追加"}
        </button>
        {addErr && <span className="text-[12px] text-down">⚠ {addErr}</span>}
      </div>

      <section className="rounded-lg border border-hairline bg-surface-1">
        <StatusBlock
          loading={loading}
          error={error}
          empty={items?.length === 0}
          className="p-4"
          errorHint="backend 起動を確認するのだ。"
          emptyText="まだ監視銘柄がないのだ。上のフォームから追加するのだ。"
        >
          {items && (
            <DataTable
              columns={[
                { label: "コード / 銘柄" },
                { label: "最終調査", right: true },
                { label: "" },
                { label: "", right: true },
              ]}
            >
              {items.map((w) => {
                const busy = busyIds.has(w.id);
                return (
                  <tr key={w.id} className="hover:[&>td]:bg-surface-2">
                    <Td>
                      <Link href={`/stocks/${w.code}`} className="hover:text-accent">
                        <span className="num font-semibold text-accent">{w.code}</span>{" "}
                        <span className="text-[12px] text-ink-muted">{w.company_name ?? "—"}</span>
                      </Link>
                      {w.note && <span className="ml-2 text-[12px] text-ink-subtle">{w.note}</span>}
                    </Td>
                    <Td right>
                      {w.last_investigated_at ? (
                        <span
                          className={`num text-[12px] ${w.stale ? "text-warning" : "text-ink-subtle"}`}
                        >
                          {w.last_investigated_at.slice(0, 10)}
                        </span>
                      ) : (
                        <span className="text-[12px] text-ink-subtle">未調査</span>
                      )}
                    </Td>
                    <Td>
                      {/* stale（21 日超過・backend 算出）または未調査を警告バッジで示す。 */}
                      {(w.stale || w.last_investigated_at == null) && (
                        <span className="rounded-sm bg-surface-2 px-1.5 py-0.5 text-[11px] text-warning">
                          {w.last_investigated_at == null ? "未調査" : "要再調査"}
                        </span>
                      )}
                    </Td>
                    <Td right>
                      <span className="inline-flex items-center gap-1">
                        <button
                          type="button"
                          onClick={() => onInvestigate(w)}
                          disabled={busy}
                          className="rounded-md bg-surface-2 px-2 py-1 text-[12px] text-ink hover:text-accent disabled:text-ink-subtle"
                        >
                          {busy ? "調査中…" : w.last_investigated_at == null ? "調査" : "再調査"}
                        </button>
                        <button
                          type="button"
                          onClick={() => onRemove(w)}
                          disabled={busy}
                          className="rounded-md px-2 py-1 text-[12px] text-ink-subtle hover:text-down disabled:text-ink-subtle"
                        >
                          削除
                        </button>
                      </span>
                    </Td>
                  </tr>
                );
              })}
            </DataTable>
          )}
        </StatusBlock>
      </section>
    </>
  );
}
