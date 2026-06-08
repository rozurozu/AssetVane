"use client";

// 取引履歴タブ（screens.md #5・phase2-spec.md §6）。/portfolio?tab=history。
// transactions が一次データ（holdings はそこから自動導出＝ADR-019）。よって「保有の修正」は
// 取引の編集・削除として実現する。一覧（新しい順）＋インライン編集（TransactionForm 兼用）＋削除。
//
// データ所有の例外メモ: frontend-component-pattern は「GET はページが持つ／feature は props で受ける」
// を規約とするが、AssetInputPanel（入力タブ）と同じく、この履歴は「初回 GET → 自前の編集/削除 mutation で
// 書き換わる」データで、かつこのタブの外では使わない。Portfolio ページ本体へ state を染み出させない
// ため、自己完結 feature の明示例外として取得も mutation もこの中に閉じる。DB には触れず lib/api.ts 経由（ADR-005）。

import { TransactionForm } from "@/components/portfolio/TransactionForm";
import { Card } from "@/components/ui/Card";
import { DataTable, Td } from "@/components/ui/DataTable";
import { StatusBlock } from "@/components/ui/StatusBlock";
import {
  type HoldingsResponse,
  type Stock,
  type Transaction,
  deleteTransaction,
  getTransactions,
} from "@/lib/api";
import { fmtJpy } from "@/lib/format";
import { useApi } from "@/lib/use-api";
import { useEffect, useState } from "react";

type Props = {
  portfolioId: number;
  stocks: Stock[];
  onHoldingsChange?: (holdings: HoldingsResponse) => void; // 編集・削除で再計算された保有を親へ伝播
};

export function TransactionHistory({ portfolioId, stocks, onHoldingsChange }: Props) {
  // 初回ロードは useApi で取り、以降は mutation 成功時に setState で差し替える折衷
  // （操作起点の更新には useApi が合わないため＝frontend-component-pattern (c)）。
  const { data, error, loading } = useApi((s) => getTransactions(portfolioId, s), [portfolioId]);
  const [txns, setTxns] = useState<Transaction[] | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [mutErr, setMutErr] = useState<string | null>(null);

  // useApi の結果を編集可能な local state に移す（portfolio 切替で取り直し）。
  useEffect(() => {
    setTxns(data);
    setEditingId(null);
  }, [data]);

  const list = txns ?? data;

  // 編集・削除後に最新の一覧を取り直す（holdings 再計算は別途 onHoldingsChange で伝播済み）。
  async function refetch() {
    try {
      setTxns(await getTransactions(portfolioId));
    } catch (e) {
      setMutErr(e instanceof Error ? e.message : String(e));
    }
  }

  async function handleDelete(t: Transaction) {
    const name = t.company_name ?? t.code;
    if (
      !window.confirm(
        `${t.traded_at} の ${name}（${t.side === "buy" ? "買い" : "売り"} ${t.shares}株）を削除するのだ？`,
      )
    ) {
      return;
    }
    setMutErr(null);
    setDeletingId(t.id);
    try {
      const result = await deleteTransaction(t.id);
      onHoldingsChange?.(result.holdings);
      await refetch();
    } catch (e) {
      setMutErr(e instanceof Error ? e.message : String(e));
    } finally {
      setDeletingId(null);
    }
  }

  // 編集フォームの保存成功（TransactionForm が holdings を返す）→ 親へ伝播＋一覧再取得＋フォームを閉じる。
  async function handleEditDone(holdings: HoldingsResponse) {
    onHoldingsChange?.(holdings);
    setEditingId(null);
    await refetch();
  }

  return (
    <Card title="取引履歴">
      {mutErr && <div className="mb-2 text-[13px] text-down">⚠ {mutErr}</div>}
      <StatusBlock
        loading={loading}
        error={error}
        empty={list?.length === 0}
        emptyText="取引履歴がないのだ。「入力」タブから取引を記録するのだ。"
      >
        {list && (
          <DataTable
            columns={[
              { label: "約定日" },
              { label: "売買" },
              { label: "コード / 銘柄" },
              { label: "株数", right: true },
              { label: "単価", right: true },
              { label: "手数料", right: true },
              { label: "概算金額", right: true },
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
                    <span className="num font-semibold text-accent">{t.code}</span>{" "}
                    <span className="text-[12px] text-ink-muted">{t.company_name ?? "—"}</span>
                  </Td>
                  <Td right className="num">
                    {t.shares.toLocaleString("ja-JP")}
                  </Td>
                  <Td right className="num text-ink-muted">
                    {fmtJpy(t.price)}
                  </Td>
                  <Td right className="num text-ink-muted">
                    {t.fee != null ? fmtJpy(t.fee) : "—"}
                  </Td>
                  <Td right className="num font-semibold">
                    {fmtJpy(t.shares * t.price)}
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
                        <TransactionForm
                          portfolioId={portfolioId}
                          stocks={stocks}
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
