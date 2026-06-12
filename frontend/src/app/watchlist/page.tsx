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
  updateWatchlistInterval,
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

  // 調査/削除の失敗メッセージ（一覧上部に表示・DossierSection の actionErr と対称＝
  // tasks/review-2026-06-12.md C-13）。次の操作開始時にクリアする。
  const [actionErr, setActionErr] = useState<string | null>(null);

  // 調査間隔の保存中（PATCH 飛行中）の id 集合。調査/削除とは独立に扱う。
  const [intervalBusyIds, setIntervalBusyIds] = useState<Set<number>>(new Set());

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
  // 失敗時は行を残し、一覧上部にエラーを表示する（C-13・別途リトライ可）。
  async function onInvestigate(item: WatchlistItem) {
    setBusy(item.id, true);
    setActionErr(null);
    try {
      const res = await investigateStock(item.code);
      setItems((prev) =>
        (prev ?? []).map((w) =>
          w.code === item.code
            ? { ...w, last_investigated_at: res.dossier.last_investigated_at, stale: false }
            : w,
        ),
      );
    } catch (e) {
      setActionErr(`調査に失敗（${item.code}）: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(item.id, false);
    }
  }

  // 調査間隔を更新（ADR-033・PATCH /watchlist/{code}）。成功後はレスポンスの
  // WatchlistItem で当該行を差し替える（backend が再算出した stale も反映＝楽観更新でなく確定値）。
  async function onChangeInterval(item: WatchlistItem, days: number) {
    if (days < 1 || days === item.interval_days) return;
    setIntervalBusyIds((prev) => new Set(prev).add(item.id));
    try {
      const updated = await updateWatchlistInterval(item.code, days);
      setItems((prev) => (prev ?? []).map((w) => (w.id === item.id ? updated : w)));
    } catch {
      // 失敗時は行を変えない（別途やり直し可）。
    } finally {
      setIntervalBusyIds((prev) => {
        const next = new Set(prev);
        next.delete(item.id);
        return next;
      });
    }
  }

  // 削除（存在しない id でも 200・楽観的に行を除去）。
  // 失敗時は行を残し、一覧上部にエラーを表示する（C-13）。
  async function onRemove(item: WatchlistItem) {
    setBusy(item.id, true);
    setActionErr(null);
    try {
      await removeWatchlist(item.id);
      setItems((prev) => (prev ?? []).filter((w) => w.id !== item.id));
    } catch (e) {
      setActionErr(`削除に失敗（${item.code}）: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(item.id, false);
    }
  }

  return (
    <>
      <div className="mb-3">
        <div className="font-semibold text-[20px] tracking-[-0.4px]">Watchlist</div>
        <div className="mt-0.5 text-[12px] text-ink-muted">
          監視銘柄を夜間に軽く調査するのだ。銘柄ごとの調査間隔より長く放置されたものは警告色で再調査を促すのだ。
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
        {/* 調査/削除の失敗を一覧上部に表示（DossierSection と対称・C-13）。 */}
        {actionErr && (
          <div className="border-hairline-soft border-b px-3 py-2 text-[12px] text-down">
            ⚠ {actionErr}
          </div>
        )}
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
                { label: "調査間隔" },
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
                      {/* stale（per-row interval_days 超過・backend 算出）または未調査を警告バッジで示す。 */}
                      {(w.stale || w.last_investigated_at == null) && (
                        <span className="rounded-sm bg-surface-2 px-1.5 py-0.5 text-[11px] text-warning">
                          {w.last_investigated_at == null ? "未調査" : "要再調査"}
                        </span>
                      )}
                    </Td>
                    <Td>
                      <IntervalControl
                        value={w.interval_days}
                        busy={intervalBusyIds.has(w.id)}
                        onChange={(days) => onChangeInterval(w, days)}
                      />
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

// 調査間隔のプリセット（ラベル → 日数）。任意整数は下の input で入れる。
const INTERVAL_PRESETS: { label: string; days: number }[] = [
  { label: "毎日", days: 1 },
  { label: "週", days: 7 },
  { label: "月", days: 30 },
];

type IntervalControlProps = {
  value: number; // 現在の interval_days（backend 確定値）
  busy: boolean; // PATCH 飛行中（操作を無効化）
  onChange: (days: number) => void; // 確定時に親へ通知（親が PATCH＋行差し替え）
};

// 行ごとの調査間隔コントロール。プリセット（毎日/週/月）＋任意整数入力。
// 任意入力は確定（Enter / blur）で onChange を呼ぶ。編集中の文字列はローカルに持ち、
// 親の value が変わったら追従する（操作起点の更新は親が所有＝frontend-component-pattern (c)）。
function IntervalControl({ value, busy, onChange }: IntervalControlProps) {
  const [draft, setDraft] = useState(String(value));

  // 親の確定値が変わったら入力欄も同期（PATCH 成功 / 別経路の更新）。
  useEffect(() => {
    setDraft(String(value));
  }, [value]);

  // 任意入力の確定。空・非整数・現在値と同じなら何もせず draft を戻す。
  function commit() {
    const n = Number.parseInt(draft, 10);
    if (Number.isNaN(n) || n < 1) {
      setDraft(String(value));
      return;
    }
    if (n === value) return;
    onChange(n);
  }

  return (
    <span className="inline-flex items-center gap-1">
      {INTERVAL_PRESETS.map((p) => {
        const active = p.days === value;
        return (
          <button
            key={p.days}
            type="button"
            onClick={() => onChange(p.days)}
            disabled={busy || active}
            className={`rounded-md px-1.5 py-0.5 text-[11px] ${
              active
                ? "bg-surface-2 text-accent"
                : "text-ink-muted hover:bg-surface-2 hover:text-ink disabled:text-ink-subtle"
            }`}
          >
            {p.label}
          </button>
        );
      })}
      <input
        type="number"
        min={1}
        value={draft}
        disabled={busy}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") e.currentTarget.blur();
        }}
        aria-label="調査間隔（日）"
        className="w-12 rounded-md border border-hairline bg-canvas px-1 py-0.5 text-right text-[11px] text-ink outline-none focus:border-accent disabled:text-ink-subtle"
      />
      <span className="text-[11px] text-ink-subtle">日</span>
    </span>
  );
}
