// 取引入力フォーム（screens.md #6・phase2-spec.md §6）。
// side（buy=text-up / sell=text-down トグル）・code・shares・price・traded_at・fee（任意）。
// 送信は postTransaction → 成功で onDone(result.holdings) を呼んで呼び元に通知。
// フォーム入力スタイル: bg-canvas border-hairline focus:border-accent（DESIGN.md）。

"use client";

import { inputCls, labelCls } from "@/components/ui/Field";
import {
  type HoldingsResponse,
  type Stock,
  type TransactionInput,
  postTransaction,
} from "@/lib/api";
import { useState } from "react";

type Props = {
  portfolioId: number;
  stocks: Stock[];
  onDone: (holdings: HoldingsResponse) => void;
};

type FormState = {
  side: "buy" | "sell";
  code: string;
  shares: string;
  price: string;
  traded_at: string;
  fee: string;
};

const INITIAL: FormState = {
  side: "buy",
  code: "",
  shares: "",
  price: "",
  traded_at: new Date().toISOString().slice(0, 10), // 今日の日付を既定値に
  fee: "",
};

export function TransactionForm({ portfolioId, stocks, onDone }: Props) {
  const [form, setForm] = useState<FormState>(INITIAL);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function set<K extends keyof FormState>(k: K, v: FormState[K]) {
    setForm((prev) => ({ ...prev, [k]: v }));
  }

  // バリデーション: 必須項目の空チェックと正数確認
  function validate(): string | null {
    if (!form.code.trim()) return "銘柄コードを入力するのだ";
    if (!form.shares.trim() || Number(form.shares) <= 0) return "株数は正数を入力するのだ";
    if (!form.price.trim() || Number(form.price) <= 0) return "価格は正数を入力するのだ";
    if (!form.traded_at) return "約定日を入力するのだ";
    if (form.fee && Number(form.fee) < 0) return "手数料は 0 以上を入力するのだ";
    return null;
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const err = validate();
    if (err) {
      setError(err);
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      const input: TransactionInput = {
        portfolio_id: portfolioId,
        code: form.code.trim(),
        side: form.side,
        shares: Number(form.shares),
        price: Number(form.price),
        fee: form.fee ? Number(form.fee) : null,
        traded_at: form.traded_at,
      };
      const result = await postTransaction(input);
      setForm(INITIAL);
      onDone(result.holdings);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      {/* side トグル（buy=up 色 / sell=down 色）*/}
      <div>
        <div className={labelCls}>売買</div>
        <div className="flex gap-1">
          {(["buy", "sell"] as const).map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => set("side", s)}
              className={`rounded-md border px-4 py-1.5 font-semibold text-[13px] transition-colors ${
                form.side === s
                  ? s === "buy"
                    ? "border-up bg-up-weak text-up"
                    : "border-down bg-down-weak text-down"
                  : "border-hairline text-ink-muted hover:bg-surface-2"
              }`}
            >
              {s === "buy" ? "買い" : "売り"}
            </button>
          ))}
        </div>
      </div>

      {/* 銘柄コード（datalist で候補補完）*/}
      <div>
        <label htmlFor="tx-code" className={labelCls}>
          銘柄コード
        </label>
        <input
          id="tx-code"
          type="text"
          list="tx-code-list"
          value={form.code}
          onChange={(e) => set("code", e.target.value)}
          placeholder="例: 7203"
          className={inputCls}
          autoComplete="off"
        />
        <datalist id="tx-code-list">
          {stocks.map((s) => (
            <option key={s.code} value={s.code}>
              {s.company_name ?? s.code}
            </option>
          ))}
        </datalist>
      </div>

      {/* 株数・単価 横並び */}
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label htmlFor="tx-shares" className={labelCls}>
            株数
          </label>
          <input
            id="tx-shares"
            type="number"
            min="1"
            step="1"
            value={form.shares}
            onChange={(e) => set("shares", e.target.value)}
            placeholder="100"
            className={inputCls}
          />
        </div>
        <div>
          <label htmlFor="tx-price" className={labelCls}>
            約定単価（円）
          </label>
          <input
            id="tx-price"
            type="number"
            min="0.01"
            step="0.01"
            value={form.price}
            onChange={(e) => set("price", e.target.value)}
            placeholder="2500.00"
            className={inputCls}
          />
        </div>
      </div>

      {/* 約定日・手数料 横並び */}
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label htmlFor="tx-date" className={labelCls}>
            約定日
          </label>
          <input
            id="tx-date"
            type="date"
            value={form.traded_at}
            onChange={(e) => set("traded_at", e.target.value)}
            className={inputCls}
          />
        </div>
        <div>
          <label htmlFor="tx-fee" className={labelCls}>
            手数料（円・任意）
          </label>
          <input
            id="tx-fee"
            type="number"
            min="0"
            step="0.01"
            value={form.fee}
            onChange={(e) => set("fee", e.target.value)}
            placeholder="0"
            className={inputCls}
          />
        </div>
      </div>

      {/* エラー表示 */}
      {error && (
        <div className="rounded-md bg-down-weak px-3 py-2 text-[13px] text-down">{error}</div>
      )}

      {/* 送信ボタン */}
      <button
        type="submit"
        disabled={submitting}
        className="w-full rounded-md border border-accent bg-accent px-4 py-2 font-semibold text-[13px] text-white disabled:cursor-not-allowed disabled:opacity-50"
      >
        {submitting ? "送信中…" : "取引を記録するのだ"}
      </button>
    </form>
  );
}
