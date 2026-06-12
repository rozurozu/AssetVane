"use client";

// 米株取引履歴（Phase 7(B-2)・ADR-055/057）。投信 FundTransactionHistory のミラー。
// us-transactions が一次データ。一覧（新しい順）＋インライン編集（C-14＝tasks/review-2026-06-12.md）＋削除。
// mutation 後に onHoldingsChange で保有を親へ伝播。
//
// データ所有の例外メモ: 「初回 GET → 自前の編集/削除 mutation で書き換わる」データで、このタブの外では
// 使わない。自己完結 feature の明示例外として取得・mutation ともここに閉じる（DB 非依存・lib/api.ts 経由＝ADR-005）。

import { Card } from "@/components/ui/Card";
import { DataTable, Td } from "@/components/ui/DataTable";
import { StatusBlock } from "@/components/ui/StatusBlock";
import { UsTransactionForm } from "@/components/us-holdings/UsTransactionForm";
import {
  type UsHolding,
  type UsTransaction,
  deleteUsTransaction,
  listUsTransactions,
} from "@/lib/api";
import { fmtUsd } from "@/lib/format";
import { useApi } from "@/lib/use-api";
import { useEffect, useState } from "react";

type Props = {
  onHoldingsChange?: (holdings: UsHolding[]) => void;
};

export function UsTransactionHistory({ onHoldingsChange }: Props) {
  // 初回ロードは useApi で取り、以降は mutation 成功時に再 fetch（操作起点の更新＝frontend-component-pattern (c)）。
  const { data, error, loading } = useApi((s) => listUsTransactions(s), []);
  const [txns, setTxns] = useState<UsTransaction[] | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [mutErr, setMutErr] = useState<string | null>(null);

  useEffect(() => {
    setTxns(data);
    setEditingId(null);
  }, [data]);

  const list = txns ?? data;

  async function refetch() {
    try {
      setTxns(await listUsTransactions());
    } catch (e) {
      setMutErr(e instanceof Error ? e.message : String(e));
    }
  }

  async function handleDelete(t: UsTransaction) {
    const label = `${t.traded_at} の ${t.symbol}（${t.side === "buy" ? "買い" : "売り"} ${t.shares}株）`;
    if (!window.confirm(`${label} を削除するのだ？`)) return;
    setMutErr(null);
    setDeletingId(t.id);
    try {
      const holdings = await deleteUsTransaction(t.id);
      onHoldingsChange?.(holdings);
      await refetch();
    } catch (e) {
      setMutErr(e instanceof Error ? e.message : String(e));
    } finally {
      setDeletingId(null);
    }
  }

  async function handleEditDone(holdings: UsHolding[]) {
    onHoldingsChange?.(holdings);
    setEditingId(null);
    await refetch();
  }

  return (
    <Card title="米国株 取引履歴">
      {mutErr && <div className="mb-2 text-[13px] text-down">⚠ {mutErr}</div>}
      <StatusBlock
        loading={loading}
        error={error}
        empty={list?.length === 0}
        emptyText="米国株の取引履歴がないのだ。上の入力フォームから記録するのだ。"
      >
        {list && (
          <DataTable
            columns={[
              { label: "約定日" },
              { label: "売買" },
              { label: "銘柄" },
              { label: "株数", right: true },
              { label: "単価(USD)", right: true },
              { label: "FXレート", right: true },
              { label: "手数料", right: true },
              { label: "操作" },
            ]}
          >
            {list.map((t) => {
              const isBuy = t.side === "buy";
              const editingThis = editingId === t.id;
              return [
                <tr key={t.id} className="hover:[&>td]:bg-surface-2">
                  <Td>
                    <span className="num text-[12px] text-ink-muted">{t.traded_at}</span>
                  </Td>
                  <Td>
                    <span className={`font-semibold ${isBuy ? "text-up" : "text-down"}`}>
                      {isBuy ? "買い" : "売り"}
                    </span>
                  </Td>
                  <Td>
                    <span className="num font-semibold text-accent">{t.symbol}</span>{" "}
                    <span className="text-[12px] text-ink-muted">{t.company_name ?? ""}</span>
                    {t.note && <span className="block text-[11px] text-ink-subtle">{t.note}</span>}
                  </Td>
                  <Td right className="num">
                    {t.shares.toLocaleString("en-US")}
                  </Td>
                  <Td right className="num text-ink-muted">
                    {fmtUsd(t.price)}
                  </Td>
                  <Td right className="num text-ink-muted">
                    {t.fx_rate != null ? `¥${t.fx_rate.toFixed(2)}` : "—"}
                  </Td>
                  <Td right className="num text-ink-muted">
                    {t.fee != null ? fmtUsd(t.fee) : "—"}
                  </Td>
                  <Td>
                    <div className="flex gap-1">
                      <button
                        type="button"
                        onClick={() => {
                          setMutErr(null);
                          setEditingId(editingThis ? null : t.id);
                        }}
                        className="rounded-md px-2 py-1 text-[12px] text-ink-muted hover:text-ink"
                      >
                        {editingThis ? "閉じる" : "編集"}
                      </button>
                      <button
                        type="button"
                        onClick={() => handleDelete(t)}
                        disabled={deletingId === t.id}
                        className="rounded-md px-2 py-1 text-[12px] text-down hover:text-ink disabled:opacity-50"
                      >
                        {deletingId === t.id ? "削除中…" : "削除"}
                      </button>
                    </div>
                  </Td>
                </tr>,
                editingThis ? (
                  <tr key={`${t.id}-edit`}>
                    <td colSpan={8} className="border-hairline-soft border-b p-3">
                      <div className="rounded-md border border-hairline bg-canvas p-3">
                        <div className="mb-2 font-medium text-[13px]">取引を編集</div>
                        <UsTransactionForm
                          initial={t}
                          transactionId={t.id}
                          onDone={handleEditDone}
                          onCancel={() => setEditingId(null)}
                        />
                      </div>
                    </td>
                  </tr>
                ) : null,
              ];
            })}
          </DataTable>
        )}
      </StatusBlock>
    </Card>
  );
}
