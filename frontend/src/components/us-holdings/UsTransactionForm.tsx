"use client";

// 米株取引入力フォーム（Phase 7(B-2)・ADR-055/057）。投信 FundTransactionForm のミラー。新規／編集兼用。
// symbol・side（buy/sell）・shares・price(USD)・traded_at・fx_rate（任意）・fee（USD・任意＝C-12）・note（任意）。
// fx_rate 省略時はサーバが約定日レートを解決（未取得なら 400「FX レート未取得」を表示）。
// transactionId なし＝新規（addUsTransaction）。あり＝編集（updateUsTransaction＝C-14・
// tasks/review-2026-06-12.md）。送信成功で onDone(holdings) を呼ぶ（新規・編集とも UsHolding[] を返す
// ＝backend 再計算済み）。

import { inputCls, labelCls } from "@/components/ui/Field";
import {
  type UsHolding,
  type UsTransaction,
  type UsTransactionInput,
  addUsTransaction,
  updateUsTransaction,
} from "@/lib/api";
import { useEffect, useState } from "react";

type Props = {
  onDone: (holdings: UsHolding[]) => void;
  initial?: UsTransaction; // 編集時の既存取引値（無ければ新規）
  transactionId?: number; // 指定時＝編集モード（updateUsTransaction を使う）
  onCancel?: () => void; // 編集モードでキャンセルしたとき呼ぶ
};

type FormState = {
  side: "buy" | "sell";
  symbol: string;
  shares: string;
  price: string; // USD
  traded_at: string; // YYYY-MM-DD
  fx_rate: string; // USDJPY（任意・空欄でサーバ解決）
  fee: string; // 手数料（USD・任意＝C-12）
  note: string;
};

function initialState(): FormState {
  return {
    side: "buy",
    symbol: "",
    shares: "",
    price: "",
    traded_at: new Date().toISOString().slice(0, 10),
    fx_rate: "",
    fee: "",
    note: "",
  };
}

/** UsTransaction（API の型）を編集フォームの FormState に変換する。 */
function toFormState(t: UsTransaction): FormState {
  return {
    side: t.side,
    symbol: t.symbol,
    shares: String(t.shares),
    price: String(t.price),
    traded_at: t.traded_at,
    fx_rate: t.fx_rate != null ? String(t.fx_rate) : "",
    fee: t.fee != null ? String(t.fee) : "",
    note: t.note ?? "",
  };
}

function validate(form: FormState): string | null {
  if (!form.symbol.trim()) return "ティッカーシンボルを入力するのだ";
  if (!form.shares.trim() || Number(form.shares) <= 0) return "株数は正数を入力するのだ";
  if (!form.price.trim() || Number(form.price) <= 0) return "約定単価(USD)は正数を入力するのだ";
  if (!form.traded_at) return "約定日を入力するのだ";
  if (form.fx_rate.trim() !== "" && Number(form.fx_rate) <= 0) {
    return "FX レートは正数を入力するのだ（空欄でサーバが解決）";
  }
  if (form.fee.trim() !== "" && Number(form.fee) < 0) return "手数料は 0 以上を入力するのだ";
  return null;
}

export function UsTransactionForm({ onDone, initial, transactionId, onCancel }: Props) {
  const editing = transactionId != null;
  const [form, setForm] = useState<FormState>(initial ? toFormState(initial) : initialState());
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // initial が差し替わったら（別の行を編集し始めたら）フォームに反映する。
  useEffect(() => {
    if (initial) setForm(toFormState(initial));
  }, [initial]);

  function set<K extends keyof FormState>(k: K, v: FormState[K]) {
    setForm((prev) => ({ ...prev, [k]: v }));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const err = validate(form);
    if (err) {
      setError(err);
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      const input: UsTransactionInput = {
        symbol: form.symbol.trim().toUpperCase(),
        side: form.side,
        shares: Number(form.shares),
        price: Number(form.price),
        fee: form.fee.trim() !== "" ? Number(form.fee) : null,
        traded_at: form.traded_at,
        fx_rate: form.fx_rate.trim() !== "" ? Number(form.fx_rate) : null,
        note: form.note.trim() || null,
      };
      // 編集モードは update（フォームは onCancel が片付ける）。新規は add（連続入力できるよう初期化）。
      const holdings = editing
        ? await updateUsTransaction(transactionId, input)
        : await addUsTransaction(input);
      if (!editing) setForm(initialState());
      onDone(holdings);
    } catch (e) {
      // 400「FX レート未取得」もここで表示（ApiError.message に detail が入る）。
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

      {/* ティッカーシンボル */}
      <div>
        <label htmlFor="ustx-symbol" className={labelCls}>
          ティッカーシンボル
        </label>
        <input
          id="ustx-symbol"
          type="text"
          value={form.symbol}
          onChange={(e) => set("symbol", e.target.value)}
          placeholder="例: AAPL"
          className={inputCls}
          autoComplete="off"
          disabled={editing} // 編集時は対象銘柄を変えない（投信の isin と同じ扱い）
        />
      </div>

      {/* 株数・約定単価 横並び */}
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label htmlFor="ustx-shares" className={labelCls}>
            株数
          </label>
          <input
            id="ustx-shares"
            type="number"
            min="0.0001"
            step="0.0001"
            value={form.shares}
            onChange={(e) => set("shares", e.target.value)}
            placeholder="10"
            className={inputCls}
          />
        </div>
        <div>
          <label htmlFor="ustx-price" className={labelCls}>
            約定単価（USD）
          </label>
          <input
            id="ustx-price"
            type="number"
            min="0.01"
            step="0.01"
            value={form.price}
            onChange={(e) => set("price", e.target.value)}
            placeholder="185.50"
            className={inputCls}
          />
        </div>
      </div>

      {/* 約定日・FX レート 横並び */}
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label htmlFor="ustx-date" className={labelCls}>
            約定日
          </label>
          <input
            id="ustx-date"
            type="date"
            value={form.traded_at}
            onChange={(e) => set("traded_at", e.target.value)}
            className={inputCls}
          />
        </div>
        <div>
          <label htmlFor="ustx-fx" className={labelCls}>
            USDJPY レート（任意）
          </label>
          <input
            id="ustx-fx"
            type="number"
            min="0.01"
            step="0.01"
            value={form.fx_rate}
            onChange={(e) => set("fx_rate", e.target.value)}
            placeholder="空欄ならサーバが約定日レートを使用"
            className={inputCls}
          />
        </div>
      </div>

      {/* 手数料・メモ 横並び（fee は C-12＝tasks/review-2026-06-12.md・投信フォームのミラー）*/}
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label htmlFor="ustx-fee" className={labelCls}>
            手数料（USD・任意）
          </label>
          <input
            id="ustx-fee"
            type="number"
            min="0"
            step="0.01"
            value={form.fee}
            onChange={(e) => set("fee", e.target.value)}
            placeholder="0"
            className={inputCls}
          />
        </div>
        <div>
          <label htmlFor="ustx-note" className={labelCls}>
            メモ（任意）
          </label>
          <input
            id="ustx-note"
            type="text"
            value={form.note}
            onChange={(e) => set("note", e.target.value)}
            placeholder="例: S&P500 積み立て"
            className={inputCls}
          />
        </div>
      </div>

      {/* エラー表示（400「FX レート未取得」もここに表示） */}
      {error && (
        <div className="rounded-md bg-down-weak px-3 py-2 text-[13px] text-down">{error}</div>
      )}

      {/* 送信ボタン（編集時はキャンセルも並べる）*/}
      <div className="flex gap-2">
        <button
          type="submit"
          disabled={submitting}
          className="flex-1 rounded-md border border-accent bg-accent px-4 py-2 font-semibold text-[13px] text-white disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting
            ? editing
              ? "更新中…"
              : "送信中…"
            : editing
              ? "更新するのだ"
              : "取引を記録するのだ"}
        </button>
        {editing && onCancel && (
          <button
            type="button"
            onClick={onCancel}
            className="rounded-md border border-hairline px-4 py-2 text-[13px] text-ink-muted hover:text-ink"
          >
            キャンセル
          </button>
        )}
      </div>
    </form>
  );
}
