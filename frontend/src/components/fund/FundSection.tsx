"use client";

// 投資信託セクション（ADR-054）。Portfolio ページの「投資信託」タブ本体。
// 保有一覧（FundHoldingsTable）＋取引入力（FundTransactionForm）＋取引履歴（FundTransactionHistory）を束ねる。
// 株の AssetInputPanel/TransactionHistory と同じく自己完結 feature の明示例外として、funds マスタと
// fund-holdings の初回 GET をここで持ち、以降は mutation が返す FundHolding[] / 新規 Fund で setState 更新する
// （操作起点の更新には useApi が合わない＝frontend-component-pattern (c)）。DB 非依存・lib/api.ts 経由（ADR-005）。

import { FundHoldingsTable } from "@/components/fund/FundHoldingsTable";
import { FundTransactionForm } from "@/components/fund/FundTransactionForm";
import { FundTransactionHistory } from "@/components/fund/FundTransactionHistory";
import { Card } from "@/components/ui/Card";
import { StatusBlock } from "@/components/ui/StatusBlock";
import { type Fund, type FundHolding, getFundHoldings, getFunds } from "@/lib/api";
import { useEffect, useState } from "react";

export function FundSection({ portfolioId }: { portfolioId: number }) {
  // funds マスタ（datalist 候補・ISIN→名称補完）と保有。どちらも mutation で書き換わるので useState 保持。
  const [funds, setFunds] = useState<Fund[]>([]);
  const [holdings, setHoldings] = useState<FundHolding[] | null>(null);
  const [holdingsErr, setHoldingsErr] = useState<string | null>(null);

  // 初回ロード（funds は portfolio 非依存・holdings は portfolio 依存）。
  useEffect(() => {
    getFunds()
      .then(setFunds)
      .catch(() => {});
  }, []);

  useEffect(() => {
    setHoldings(null);
    setHoldingsErr(null);
    getFundHoldings(portfolioId)
      .then(setHoldings)
      .catch((e) => setHoldingsErr(e instanceof Error ? e.message : String(e)));
  }, [portfolioId]);

  // 取引（新規・編集・削除）後に backend が返す最新保有で差し替える。
  function handleHoldingsChange(updated: FundHolding[]) {
    setHoldings(updated);
  }

  // 未登録 ISIN を新規登録したら datalist 候補に足す（重複は無視）。
  function handleFundCreated(fund: Fund) {
    setFunds((prev) => (prev.some((f) => f.isin === fund.isin) ? prev : [...prev, fund]));
  }

  return (
    <div className="space-y-3">
      {/* 保有一覧 */}
      <Card title="投信 保有">
        <StatusBlock
          loading={holdings === null}
          error={holdingsErr}
          empty={holdings?.length === 0}
          emptyText="投信の保有がないのだ。下の入力フォームから取引を記録するのだ。"
        >
          {holdings && holdings.length > 0 && <FundHoldingsTable holdings={holdings} />}
        </StatusBlock>
      </Card>

      {/* 取引入力 */}
      <Card title="投信 取引を記録するのだ">
        <FundTransactionForm
          portfolioId={portfolioId}
          funds={funds}
          onDone={handleHoldingsChange}
          onFundCreated={handleFundCreated}
        />
      </Card>

      {/* 取引履歴（一覧＋インライン編集＋削除）*/}
      <FundTransactionHistory
        portfolioId={portfolioId}
        funds={funds}
        onHoldingsChange={handleHoldingsChange}
        onFundCreated={handleFundCreated}
      />
    </div>
  );
}
