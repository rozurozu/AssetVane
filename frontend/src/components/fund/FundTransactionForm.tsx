// 投信取引入力フォーム（ADR-054）。株の TransactionForm をミラー。新規／編集兼用。
// 銘柄は funds マスタから datalist 選択。未登録 ISIN のときは名称欄に入力すると、送信時に
// 先に postFund（POST /funds）でマスタ登録してから buy 取引を流す。
// side（buy=text-up / sell=text-down トグル）・isin・units（口数）・price（基準価額）・traded_at・fee（任意）。
// transactionId なし＝新規（postFundTransaction）。あり＝編集（putFundTransaction）。
// 送信成功で onDone(holdings) を呼ぶ（新規・編集とも FundHolding[] を返す＝backend 再計算済み）。

"use client";

import { inputCls, labelCls } from "@/components/ui/Field";
import {
  type Fund,
  type FundHolding,
  type FundTransaction,
  type FundTransactionInput,
  postFund,
  postFundTransaction,
  putFundTransaction,
} from "@/lib/api";
import { useEffect, useState } from "react";

type Props = {
  portfolioId: number;
  funds: Fund[]; // datalist の候補（登録済みマスタ）
  onDone: (holdings: FundHolding[]) => void;
  onFundCreated?: (fund: Fund) => void; // 未登録 ISIN を新規登録したとき親へ通知（funds 候補更新用）
  initial?: FundTransaction; // 編集時の既存取引値（無ければ新規）
  transactionId?: number; // 指定時＝編集モード（putFundTransaction を使う）
  onCancel?: () => void; // 編集モードでキャンセルしたとき呼ぶ
};

type FormState = {
  side: "buy" | "sell";
  isin: string;
  name: string; // 未登録 ISIN を新規登録するときの名称
  assoc_code: string; // 協会コード（新規登録時は必須・NAV 取得に使う）
  units: string;
  price: string;
  traded_at: string;
  fee: string;
};

function initialState(): FormState {
  return {
    side: "buy",
    isin: "",
    name: "",
    assoc_code: "",
    units: "",
    price: "",
    traded_at: new Date().toISOString().slice(0, 10), // 今日の日付を既定値に
    fee: "",
  };
}

/** FundTransaction（API の型）を編集フォームの FormState に変換する。 */
function toFormState(t: FundTransaction): FormState {
  return {
    side: t.side,
    isin: t.isin,
    name: "",
    assoc_code: "",
    units: String(t.units),
    price: String(t.price),
    traded_at: t.traded_at,
    fee: t.fee != null ? String(t.fee) : "",
  };
}

export function FundTransactionForm({
  portfolioId,
  funds,
  onDone,
  onFundCreated,
  initial,
  transactionId,
  onCancel,
}: Props) {
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

  // 入力中の ISIN が既存マスタにあるか（無ければ名称欄を出して新規登録に倒す）。
  const knownFund = funds.find((f) => f.isin === form.isin.trim());
  const needsRegister = !editing && form.isin.trim() !== "" && knownFund == null;

  function validate(): string | null {
    if (!form.isin.trim()) return "ISIN を入力するのだ";
    if (needsRegister && !form.name.trim()) return "未登録の投信なので名称を入力するのだ";
    // 協会コードは NAV 自動取得（associFundCd）に必須。新規登録時は空不可（backend も 422）。
    if (needsRegister && !form.assoc_code.trim()) return "協会コードは NAV 取得に必須なのだ";
    if (!form.units.trim() || Number(form.units) <= 0) return "口数は正数を入力するのだ";
    if (!form.price.trim() || Number(form.price) <= 0) return "基準価額は正数を入力するのだ";
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
      const isin = form.isin.trim();
      // 未登録 ISIN は先にマスタ登録してから取引（POST /funds → そのまま buy）。
      if (needsRegister) {
        const created = await postFund({
          isin,
          name: form.name.trim(),
          assoc_code: form.assoc_code.trim(),
        });
        onFundCreated?.(created);
      }
      const input: FundTransactionInput = {
        portfolio_id: portfolioId,
        isin,
        side: form.side,
        units: Number(form.units),
        price: Number(form.price),
        fee: form.fee ? Number(form.fee) : null,
        traded_at: form.traded_at,
      };
      // 編集モードは update（フォームは onCancel が片付ける）。新規は post（連続入力できるよう初期化）。
      const holdings = editing
        ? await putFundTransaction(transactionId, input)
        : await postFundTransaction(input);
      if (!editing) setForm(initialState());
      onDone(holdings);
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

      {/* ISIN（datalist でマスタ候補補完）*/}
      <div>
        <label htmlFor="ftx-isin" className={labelCls}>
          ISIN（投信）
        </label>
        <input
          id="ftx-isin"
          type="text"
          list="ftx-isin-list"
          value={form.isin}
          onChange={(e) => set("isin", e.target.value)}
          placeholder="例: JP90C000H1T1"
          className={inputCls}
          autoComplete="off"
          disabled={editing} // 編集時は対象投信を変えない（株の code と同じ扱い）
        />
        <datalist id="ftx-isin-list">
          {funds.map((f) => (
            <option key={f.isin} value={f.isin}>
              {f.name}
            </option>
          ))}
        </datalist>
        {knownFund && <div className="mt-0.5 text-[11px] text-ink-subtle">{knownFund.name}</div>}
      </div>

      {/* 未登録 ISIN のときだけ名称・協会コードを出す（その場でマスタ登録するのだ）*/}
      {needsRegister && (
        <div className="rounded-md border border-hairline bg-canvas p-2.5">
          <div className="mb-1.5 text-[11px] text-ink-muted">
            未登録の投信なのだ。名称と協会コードを入れると登録してから取引するのだ（協会コードは NAV
            自動取得に必須なのだ）。
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label htmlFor="ftx-name" className={labelCls}>
                名称
              </label>
              <input
                id="ftx-name"
                type="text"
                value={form.name}
                onChange={(e) => set("name", e.target.value)}
                placeholder="例: eMAXIS Slim 全世界株式"
                className={inputCls}
              />
            </div>
            <div>
              <label htmlFor="ftx-assoc" className={labelCls}>
                協会コード（必須・NAV 取得）
              </label>
              <input
                id="ftx-assoc"
                type="text"
                value={form.assoc_code}
                onChange={(e) => set("assoc_code", e.target.value)}
                placeholder="0331418A"
                className={inputCls}
              />
            </div>
          </div>
        </div>
      )}

      {/* 口数・基準価額 横並び */}
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label htmlFor="ftx-units" className={labelCls}>
            口数
          </label>
          <input
            id="ftx-units"
            type="number"
            min="0"
            step="0.0001"
            value={form.units}
            onChange={(e) => set("units", e.target.value)}
            placeholder="12345.6789"
            className={inputCls}
          />
        </div>
        <div>
          <label htmlFor="ftx-price" className={labelCls}>
            約定基準価額（10,000 口あたり円）
          </label>
          <input
            id="ftx-price"
            type="number"
            min="0.01"
            step="0.01"
            value={form.price}
            onChange={(e) => set("price", e.target.value)}
            placeholder="12345"
            className={inputCls}
          />
        </div>
      </div>

      {/* 約定日・手数料 横並び */}
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label htmlFor="ftx-date" className={labelCls}>
            約定日
          </label>
          <input
            id="ftx-date"
            type="date"
            value={form.traded_at}
            onChange={(e) => set("traded_at", e.target.value)}
            className={inputCls}
          />
        </div>
        <div>
          <label htmlFor="ftx-fee" className={labelCls}>
            手数料（円・任意）
          </label>
          <input
            id="ftx-fee"
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
