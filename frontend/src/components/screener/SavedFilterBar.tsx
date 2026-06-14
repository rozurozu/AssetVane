"use client";

// 保存スクリーニング条件のバー（screens.md #2・ADR-031）。
// 読み込み（select）・新規保存・上書き・削除を持つ feature（mutation 所有・結果は親へコールバック）。
// 保存対象は「現在適用中の条件（appliedCriteria）」。DB に触れず lib/api.ts 経由（ADR-005）。

import { inputCls } from "@/components/ui/Field";
import {
  type SavedFilter,
  type ScreenCriteria,
  deleteFilter,
  postFilter,
  putFilter,
} from "@/lib/api";
import { useState } from "react";

type Props = {
  filters: SavedFilter[];
  appliedCriteria: ScreenCriteria;
  onLoad: (criteria: ScreenCriteria) => void;
  onSaved: (f: SavedFilter) => void;
  onDeleted: (id: number) => void;
};

const btnCls =
  "rounded-md border border-hairline px-3 py-1.5 text-[12px] text-ink-muted hover:bg-surface-2 disabled:opacity-50";

export function SavedFilterBar({ filters, appliedCriteria, onLoad, onSaved, onDeleted }: Props) {
  const [selectedId, setSelectedId] = useState<number | "">("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  function handleSelect(idStr: string) {
    const id = idStr === "" ? "" : Number(idStr);
    setSelectedId(id);
    if (id === "") return;
    const f = filters.find((x) => x.id === id);
    if (f) {
      setName(f.name);
      onLoad(f.criteria);
    }
  }

  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setErr(null);
    try {
      await fn();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const handleCreate = () =>
    run(async () => {
      const f = await postFilter({ name: name.trim(), criteria: appliedCriteria });
      onSaved(f);
      setSelectedId(f.id);
    });

  const handleUpdate = () =>
    run(async () => {
      if (selectedId === "") return;
      const f = await putFilter(selectedId, { name: name.trim(), criteria: appliedCriteria });
      onSaved(f);
    });

  const handleDelete = () =>
    run(async () => {
      if (selectedId === "") return;
      await deleteFilter(selectedId);
      onDeleted(selectedId);
      setSelectedId("");
      setName("");
    });

  return (
    <div className="flex flex-wrap items-center gap-2">
      <select
        className={`${inputCls} w-auto`}
        value={selectedId}
        onChange={(e) => handleSelect(e.target.value)}
      >
        <option value="">保存フィルタを読み込む…</option>
        {filters.map((f) => (
          <option key={f.id} value={f.id}>
            {f.name}
          </option>
        ))}
      </select>

      <input
        className={`${inputCls} w-44`}
        placeholder="フィルタ名"
        value={name}
        onChange={(e) => setName(e.target.value)}
      />
      <button
        type="button"
        className={btnCls}
        disabled={busy || !name.trim()}
        onClick={handleCreate}
      >
        名前を付けて保存
      </button>
      {selectedId !== "" && (
        <>
          <button
            type="button"
            className={btnCls}
            disabled={busy || !name.trim()}
            onClick={handleUpdate}
          >
            上書き
          </button>
          <button
            type="button"
            className={`${btnCls} text-down`}
            disabled={busy}
            onClick={handleDelete}
          >
            削除
          </button>
        </>
      )}
      {err && <span className="text-[12px] text-down">⚠ {err}</span>}
    </div>
  );
}
