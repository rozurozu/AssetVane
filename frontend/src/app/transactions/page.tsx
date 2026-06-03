"use client";

// 取引入力ページ（screens.md #6・phase2-spec.md §6）。
// 取引フォーム（TransactionForm）＋現金入力（getCash/putCash）＋外部資産 CRUD。
// Portfolio ページ内タブ方式（OPEN-D 推奨）に倣い、このルートは同じフォーム群を薄く提供。
// DB には触れない。データ取得はすべて lib/api.ts 経由（ADR-005）。

import { TransactionForm } from "@/components/portfolio/TransactionForm";
import {
  type Cash,
  type ExternalAsset,
  type ExternalAssetInput,
  type HoldingsResponse,
  type Stock,
  createExternalAsset,
  deleteExternalAsset,
  getCash,
  getExternalAssets,
  getPortfolios,
  getStocks,
  putCash,
  updateExternalAsset,
} from "@/lib/api";
import { useEffect, useState } from "react";

// --- 汎用 UI ---
function Card({
  title,
  children,
}: {
  title: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border border-hairline bg-surface-1">
      <div className="border-hairline border-b px-3 py-2">
        <h2 className="font-semibold text-[14px] tracking-[-0.1px]">{title}</h2>
      </div>
      <div className="p-3">{children}</div>
    </section>
  );
}

// --- 外部資産フォーム（新規・編集共用）---
function ExternalAssetForm({
  initial,
  onSave,
  onCancel,
}: {
  initial?: ExternalAsset;
  onSave: (input: ExternalAssetInput) => Promise<void>;
  onCancel: () => void;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [category, setCategory] = useState(initial?.category ?? "");
  const [value, setValue] = useState(initial?.value != null ? String(initial.value) : "");
  const [proxySymbol, setProxySymbol] = useState(initial?.proxy_symbol ?? "");
  const [monthly, setMonthly] = useState(
    initial?.monthly_contribution != null ? String(initial.monthly_contribution) : "",
  );
  const [asOf, setAsOf] = useState(initial?.as_of ?? "");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const inputCls =
    "w-full rounded-md border border-hairline bg-canvas px-2.5 py-1.5 text-[13px] text-ink outline-none focus:border-accent";
  const labelCls = "block text-[11px] text-ink-muted mb-0.5";

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) {
      setErr("名称を入力するのだ");
      return;
    }
    if (!value || Number(value) < 0) {
      setErr("評価額を入力するのだ");
      return;
    }
    setSaving(true);
    setErr(null);
    try {
      await onSave({
        name: name.trim(),
        category: category.trim() || null,
        value: Number(value),
        proxy_symbol: proxySymbol.trim() || null,
        monthly_contribution: monthly ? Number(monthly) : null,
        as_of: asOf || null,
      });
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={handleSave} className="space-y-2">
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label htmlFor="ea-name" className={labelCls}>
            名称
          </label>
          <input
            id="ea-name"
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="オルカン"
            className={inputCls}
          />
        </div>
        <div>
          <label htmlFor="ea-cat" className={labelCls}>
            カテゴリ（任意）
          </label>
          <input
            id="ea-cat"
            type="text"
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            placeholder="投信"
            className={inputCls}
          />
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label htmlFor="ea-value" className={labelCls}>
            評価額（円）
          </label>
          <input
            id="ea-value"
            type="number"
            min="0"
            step="0.01"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="200000"
            className={inputCls}
          />
        </div>
        <div>
          <label htmlFor="ea-monthly" className={labelCls}>
            毎月積立（円・任意）
          </label>
          <input
            id="ea-monthly"
            type="number"
            min="0"
            step="1"
            value={monthly}
            onChange={(e) => setMonthly(e.target.value)}
            placeholder="30000"
            className={inputCls}
          />
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label htmlFor="ea-proxy" className={labelCls}>
            proxy シンボル（任意）
          </label>
          <input
            id="ea-proxy"
            type="text"
            value={proxySymbol}
            onChange={(e) => setProxySymbol(e.target.value)}
            placeholder="^GSPC"
            className={inputCls}
          />
        </div>
        <div>
          <label htmlFor="ea-asof" className={labelCls}>
            基準日（任意）
          </label>
          <input
            id="ea-asof"
            type="date"
            value={asOf}
            onChange={(e) => setAsOf(e.target.value)}
            className={inputCls}
          />
        </div>
      </div>
      {err && <div className="rounded-md bg-down-weak px-3 py-2 text-[13px] text-down">{err}</div>}
      <div className="flex gap-2">
        <button
          type="submit"
          disabled={saving}
          className="rounded-md border border-accent bg-accent px-4 py-1.5 font-semibold text-[13px] text-white disabled:opacity-50"
        >
          {saving ? "保存中…" : "保存するのだ"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded-md border border-hairline px-4 py-1.5 text-[13px] text-ink-muted hover:text-ink"
        >
          キャンセル
        </button>
      </div>
    </form>
  );
}

export default function TransactionsPage() {
  const [portfolioId, setPortfolioId] = useState<number | null>(null);
  const [stocks, setStocks] = useState<Stock[]>([]);
  const [lastHoldings, setLastHoldings] = useState<HoldingsResponse | null>(null);

  // 現金
  const [cash, setCash] = useState<Cash | null>(null);
  const [cashNone, setCashNone] = useState(false); // 404 = 未設定
  const [cashInput, setCashInput] = useState("");
  const [cashSaving, setCashSaving] = useState(false);
  const [cashErr, setCashErr] = useState<string | null>(null);

  // 外部資産
  const [externalAssets, setExternalAssets] = useState<ExternalAsset[]>([]);
  const [externalErr, setExternalErr] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<number | "new" | null>(null);

  useEffect(() => {
    getPortfolios()
      .then((ps) => setPortfolioId(ps.length > 0 ? ps[0].portfolio_id : 1))
      .catch(() => setPortfolioId(1));

    getStocks()
      .then(setStocks)
      .catch(() => {});

    getCash()
      .then((c) => {
        setCash(c);
        setCashInput(String(c.balance));
      })
      .catch((e) => {
        // 404（未設定）は正常。それ以外はエラー表示。
        if (e instanceof Error && e.message.includes("404")) {
          setCashNone(true);
        } else {
          setCashErr(e instanceof Error ? e.message : String(e));
        }
      });

    getExternalAssets()
      .then(setExternalAssets)
      .catch((e) => setExternalErr(e instanceof Error ? e.message : String(e)));
  }, []);

  async function handleCashSave() {
    if (!cashInput || Number(cashInput) < 0) {
      setCashErr("0 以上の金額を入力するのだ");
      return;
    }
    setCashSaving(true);
    setCashErr(null);
    try {
      const updated = await putCash(Number(cashInput));
      setCash(updated);
      setCashNone(false);
    } catch (e) {
      setCashErr(e instanceof Error ? e.message : String(e));
    } finally {
      setCashSaving(false);
    }
  }

  async function handleExternalSave(id: number | "new", input: ExternalAssetInput) {
    if (id === "new") {
      const created = await createExternalAsset(input);
      setExternalAssets((prev) => [...prev, created]);
    } else {
      const updated = await updateExternalAsset(id, input);
      setExternalAssets((prev) => prev.map((a) => (a.id === id ? updated : a)));
    }
    setEditingId(null);
  }

  async function handleExternalDelete(id: number) {
    await deleteExternalAsset(id);
    setExternalAssets((prev) => prev.filter((a) => a.id !== id));
  }

  const inputCls =
    "rounded-md border border-hairline bg-canvas px-2.5 py-1.5 text-[13px] text-ink outline-none focus:border-accent";

  return (
    <>
      <div className="mb-3">
        <div className="font-semibold text-[20px] tracking-[-0.4px]">取引入力</div>
        <div className="mt-0.5 text-[12px] text-ink-muted">
          取引記録・現金残高・外部資産（投信等）の管理（screens.md #6）
        </div>
      </div>

      <div className="space-y-3">
        {/* 取引フォーム */}
        <Card title="取引を記録するのだ">
          {portfolioId != null ? (
            <div className="space-y-2">
              <TransactionForm
                portfolioId={portfolioId}
                stocks={stocks}
                onDone={(h) => setLastHoldings(h)}
              />
              {lastHoldings && (
                <div className="rounded-md border border-hairline bg-canvas px-3 py-2 text-[12px] text-ink-muted">
                  記録完了。保有 {lastHoldings.holdings.length} 銘柄に更新されたのだ。
                </div>
              )}
            </div>
          ) : (
            <div className="text-[13px] text-ink-subtle">ポートフォリオを読み込み中…</div>
          )}
        </Card>

        {/* 現金残高 */}
        <Card title="現金残高">
          {cashErr && <div className="mb-2 text-[13px] text-down">⚠ {cashErr}</div>}
          <div className="flex items-end gap-2">
            <div>
              <div className="mb-0.5 text-[11px] text-ink-muted">
                {cashNone
                  ? "未設定（初回入力）"
                  : cash?.updated_at
                    ? `最終更新: ${cash.updated_at.slice(0, 10)}`
                    : "現在残高"}
              </div>
              <div className="flex items-center gap-1">
                <span className="text-[13px] text-ink-muted">¥</span>
                <input
                  type="number"
                  min="0"
                  step="1"
                  value={cashInput}
                  onChange={(e) => setCashInput(e.target.value)}
                  placeholder="980000"
                  className={`${inputCls} w-40`}
                />
              </div>
            </div>
            <button
              type="button"
              onClick={handleCashSave}
              disabled={cashSaving}
              className="rounded-md border border-accent bg-accent px-4 py-1.5 font-semibold text-[13px] text-white disabled:opacity-50"
            >
              {cashSaving ? "保存中…" : "更新するのだ"}
            </button>
          </div>
          {cash && !cashNone && (
            <div className="num mt-2 text-[13px]">
              現在:{" "}
              <span className="font-semibold">
                ¥{cash.balance.toLocaleString("ja-JP", { maximumFractionDigits: 0 })}
              </span>
            </div>
          )}
        </Card>

        {/* 外部資産 CRUD */}
        <Card title="外部資産（投信・コモディティ等）">
          {externalErr && <div className="mb-2 text-[13px] text-down">⚠ {externalErr}</div>}

          {externalAssets.length === 0 && editingId !== "new" && (
            <div className="mb-2 text-[13px] text-ink-subtle">外部資産はないのだ。</div>
          )}

          {/* 一覧 */}
          {externalAssets.length > 0 && (
            <table className="mb-3 w-full border-collapse">
              <thead>
                <tr>
                  {["名称", "カテゴリ", "評価額", "積立/月", "基準日", "操作"].map((h) => (
                    <th
                      key={h}
                      className="h-8 border-hairline border-b px-2.5 text-left font-medium text-[11px] text-ink-muted"
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {externalAssets.map((a) => (
                  <tr key={a.id} className="hover:[&>td]:bg-surface-2">
                    <td className="h-[34px] border-hairline-soft border-b px-2.5 text-[13px] font-semibold">
                      {a.name}
                    </td>
                    <td className="h-[34px] border-hairline-soft border-b px-2.5 text-[12px] text-ink-muted">
                      {a.category ?? "—"}
                    </td>
                    <td className="num h-[34px] border-hairline-soft border-b px-2.5 text-[13px]">
                      ¥{a.value.toLocaleString("ja-JP")}
                    </td>
                    <td className="num h-[34px] border-hairline-soft border-b px-2.5 text-[12px] text-ink-muted">
                      {a.monthly_contribution != null
                        ? `¥${a.monthly_contribution.toLocaleString("ja-JP")}`
                        : "—"}
                    </td>
                    <td className="num h-[34px] border-hairline-soft border-b px-2.5 text-[12px] text-ink-muted">
                      {a.as_of ?? "—"}
                    </td>
                    <td className="h-[34px] border-hairline-soft border-b px-2.5">
                      <div className="flex gap-1">
                        <button
                          type="button"
                          onClick={() => setEditingId(a.id)}
                          className="rounded-md px-2 py-1 text-[12px] text-ink-muted hover:text-ink"
                        >
                          編集
                        </button>
                        <button
                          type="button"
                          onClick={() => handleExternalDelete(a.id)}
                          className="rounded-md px-2 py-1 text-[12px] text-down hover:text-ink"
                        >
                          削除
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {/* 編集フォーム */}
          {editingId !== null && editingId !== "new" && (
            <div className="mb-3 rounded-md border border-hairline bg-canvas p-3">
              <div className="mb-2 font-medium text-[13px]">編集中</div>
              <ExternalAssetForm
                initial={externalAssets.find((a) => a.id === editingId)}
                onSave={(input) => handleExternalSave(editingId, input)}
                onCancel={() => setEditingId(null)}
              />
            </div>
          )}

          {/* 新規追加フォーム */}
          {editingId === "new" && (
            <div className="mb-3 rounded-md border border-hairline bg-canvas p-3">
              <div className="mb-2 font-medium text-[13px]">新規追加</div>
              <ExternalAssetForm
                onSave={(input) => handleExternalSave("new", input)}
                onCancel={() => setEditingId(null)}
              />
            </div>
          )}

          <button
            type="button"
            onClick={() => setEditingId("new")}
            className="rounded-md border border-hairline px-3 py-1.5 text-[13px] text-ink-muted hover:bg-surface-2 hover:text-ink"
          >
            ＋ 外部資産を追加するのだ
          </button>
        </Card>
      </div>
    </>
  );
}
