"use client";

// 投信取引履歴（ADR-054）。株の TransactionHistory をミラー。
// fund-transactions が一次データ（fund-holdings はそこから導出）。一覧（新しい順）＋インライン編集＋削除。
// mutation 後に返ってくる FundHolding[] を onHoldingsChange で親へ伝播する。
//
// データ所有の例外メモ: TransactionHistory と同じく「初回 GET → 自前の編集/削除 mutation で書き換わる」
// データで、このタブの外では使わない。自己完結 feature の明示例外として取得も mutation もこの中に閉じる
// （DB 非依存・lib/api.ts 経由＝ADR-005）。FundTransaction は名称を持たないので funds マスタで補完する。

import { FundTransactionForm } from "@/components/fund/FundTransactionForm";
import { Card } from "@/components/ui/Card";
import { DataTable, Td } from "@/components/ui/DataTable";
import { StatusBlock } from "@/components/ui/StatusBlock";
import {
  type Fund,
  type FundHolding,
  type FundTransaction,
  deleteFundTransaction,
  getFundTransactions,
} from "@/lib/api";
import { fmtJpy } from "@/lib/format";
import { useApi } from "@/lib/use-api";
import { useEffect, useState } from "react";

type Props = {
  portfolioId: number;
  funds: Fund[]; // ISIN → 名称の補完・編集フォームの候補
  onHoldingsChange?: (holdings: FundHolding[]) => void; // 編集・削除で再計算された保有を親へ伝播
  onFundCreated?: (fund: Fund) => void; // 編集フォーム内で未登録 ISIN を登録したとき親へ通知
  reloadKey?: number; // 親（上部フォーム）起点で履歴一覧の再取得を促すトリガ（#28）
};

export function FundTransactionHistory({
  portfolioId,
  funds,
  onHoldingsChange,
  onFundCreated,
  reloadKey,
}: Props) {
  // 初回ロードは useApi で取り、以降は mutation 成功時に setState で取り直す（操作起点の更新＝(c)）。
  // reloadKey が変わる（親の上部フォームが記録した）ときも再取得する（#28）。
  const { data, error, loading } = useApi(
    (s) => getFundTransactions(portfolioId, s),
    [portfolioId, reloadKey],
  );
  const [txns, setTxns] = useState<FundTransaction[] | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [mutErr, setMutErr] = useState<string | null>(null);

  useEffect(() => {
    setTxns(data);
    setEditingId(null);
  }, [data]);

  const list = txns ?? data;

  /** ISIN → マスタ名称（無ければ ISIN をそのまま）。 */
  function fundName(isin: string): string {
    return funds.find((f) => f.isin === isin)?.name ?? isin;
  }

  async function refetch() {
    try {
      setTxns(await getFundTransactions(portfolioId));
    } catch (e) {
      setMutErr(e instanceof Error ? e.message : String(e));
    }
  }

  async function handleDelete(t: FundTransaction) {
    const name = fundName(t.isin);
    if (
      !window.confirm(
        `${t.traded_at} の ${name}（${t.side === "buy" ? "買い" : "売り"} ${t.units}口）を削除するのだ？`,
      )
    ) {
      return;
    }
    setMutErr(null);
    setDeletingId(t.id);
    try {
      const holdings = await deleteFundTransaction(t.id);
      onHoldingsChange?.(holdings);
      await refetch();
    } catch (e) {
      setMutErr(e instanceof Error ? e.message : String(e));
    } finally {
      setDeletingId(null);
    }
  }

  async function handleEditDone(holdings: FundHolding[]) {
    onHoldingsChange?.(holdings);
    setEditingId(null);
    await refetch();
  }

  return (
    <Card title="投信 取引履歴">
      {mutErr && <div className="mb-2 text-[13px] text-down">⚠ {mutErr}</div>}
      <StatusBlock
        loading={loading}
        error={error}
        empty={list?.length === 0}
        emptyText="投信の取引履歴がないのだ。上の入力フォームから記録するのだ。"
      >
        {list && (
          <DataTable
            columns={[
              { label: "約定日" },
              { label: "売買" },
              { label: "投信" },
              { label: "口数", right: true },
              { label: "基準価額", right: true },
              { label: "手数料", right: true },
              { label: "概算金額", right: true },
              { label: "操作" },
            ]}
          >
            {list.map((t) => {
              const isBuy = t.side === "buy";
              const editingThis = editingId === t.id;
              // 概算金額は「基準価額 × 口数 ÷ 10,000」（price は 10,000 口あたり）。表示用の目安。
              const gross = (t.price * t.units) / 10000;
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
                    <span className="text-[13px]">{fundName(t.isin)}</span>{" "}
                    <span className="num text-[11px] text-ink-subtle">{t.isin}</span>
                  </Td>
                  <Td right className="num">
                    {t.units.toLocaleString("ja-JP")}
                  </Td>
                  <Td right className="num text-ink-muted">
                    {fmtJpy(t.price)}
                  </Td>
                  <Td right className="num text-ink-muted">
                    {t.fee != null ? fmtJpy(t.fee) : "—"}
                  </Td>
                  <Td right className="num font-semibold">
                    {fmtJpy(gross)}
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
                        <FundTransactionForm
                          portfolioId={portfolioId}
                          funds={funds}
                          initial={t}
                          transactionId={t.id}
                          onDone={handleEditDone}
                          onFundCreated={onFundCreated}
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
