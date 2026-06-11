"use client";

// 米国株セクション（Phase 7(B-2)・ADR-055）。Portfolio ページの「米国株」タブ本体。
// 保有一覧（UsHoldingsTable）＋取引入力（UsTransactionForm）＋取引履歴（UsTransactionHistory）を束ねる。
// 投信 FundSection と同じく自己完結 feature の明示例外として、us-holdings の初回 GET をここで持ち、
// 以降は mutation が返す UsHolding[] で setState 更新する（操作起点の更新には useApi が合わない＝
// frontend-component-pattern (c)）。DB 非依存・lib/api.ts 経由（ADR-005）。

import { Card } from "@/components/ui/Card";
import { StatusBlock } from "@/components/ui/StatusBlock";
import { UsHoldingsTable } from "@/components/us-holdings/UsHoldingsTable";
import { UsTransactionForm } from "@/components/us-holdings/UsTransactionForm";
import { UsTransactionHistory } from "@/components/us-holdings/UsTransactionHistory";
import { type UsHolding, getUsHoldings } from "@/lib/api";
import { useEffect, useState } from "react";

export function UsSection() {
  const [holdings, setHoldings] = useState<UsHolding[] | null>(null);
  const [holdingsErr, setHoldingsErr] = useState<string | null>(null);

  // 初回ロード（米株保有は portfolio_id 非依存）。
  useEffect(() => {
    setHoldings(null);
    setHoldingsErr(null);
    getUsHoldings()
      .then(setHoldings)
      .catch((e) => setHoldingsErr(e instanceof Error ? e.message : String(e)));
  }, []);

  // 取引（新規・削除）後に backend が返す最新保有で差し替える。
  function handleHoldingsChange(updated: UsHolding[]) {
    setHoldings(updated);
  }

  return (
    <div className="space-y-3">
      {/* 保有一覧 */}
      <Card title="米国株 保有">
        <StatusBlock
          loading={holdings === null}
          error={holdingsErr}
          empty={holdings?.length === 0}
          emptyText="米国株の保有がないのだ。下の入力フォームから取引を記録するのだ。"
        >
          {holdings && holdings.length > 0 && <UsHoldingsTable holdings={holdings} />}
        </StatusBlock>
      </Card>

      {/* 取引入力 */}
      <Card title="米国株 取引を記録するのだ">
        <UsTransactionForm onDone={handleHoldingsChange} />
      </Card>

      {/* 取引履歴（一覧＋削除） */}
      <UsTransactionHistory onHoldingsChange={handleHoldingsChange} />
    </div>
  );
}
