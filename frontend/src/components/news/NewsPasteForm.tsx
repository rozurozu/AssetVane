"use client";

// ニュース手入力フォーム（ADR-047・/news ページ上部）。本文（必須）＋URL・銘柄コード（任意）を
// ingestNews へ投げ、backend が本文を AI 要約して NewsItem を返す（要約失敗時 502・detail を表示）。
// 成功で onDone(item) を呼んで親に通知しフォームをリセットする。
// 入力スタイルは inputCls/labelCls に一本化（frontend-component-pattern）。手本: TransactionForm。

import { inputCls, labelCls } from "@/components/ui/Field";
import { type NewsItem, ingestNews } from "@/lib/api";
import { useState } from "react";

type Props = {
  onDone: (item: NewsItem) => void;
};

export function NewsPasteForm({ onDone }: Props) {
  const [text, setText] = useState("");
  const [url, setUrl] = useState("");
  const [code, setCode] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 本文が空ならエラー（URL・銘柄コードは任意）。
  function validate(): string | null {
    if (!text.trim()) return "本文を入力するのだ";
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
      // 任意項目は空文字を null に倒して送る（api.ts 側で ?? null も効くが意図を明示）。
      const item = await ingestNews({
        text: text.trim(),
        url: url.trim() || null,
        code: code.trim() || null,
      });
      // 成功でフォームをリセットし、親へ追加分を通知（要約失敗は 502 で catch に落ちる）。
      setText("");
      setUrl("");
      setCode("");
      onDone(item);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="space-y-3 rounded-lg border border-hairline bg-surface-1 p-3"
    >
      <div>
        <label htmlFor="news-text" className={labelCls}>
          本文（必須）
        </label>
        <textarea
          id="news-text"
          rows={3}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="ニュース本文を貼り付けるのだ。AI が要約して取り込むのだ。"
          className={inputCls}
        />
      </div>

      <div className="grid grid-cols-2 gap-2">
        <div>
          <label htmlFor="news-url" className={labelCls}>
            URL（任意）
          </label>
          <input
            id="news-url"
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://…"
            className={inputCls}
            autoComplete="off"
          />
        </div>
        <div>
          <label htmlFor="news-code" className={labelCls}>
            銘柄コード（任意）
          </label>
          <input
            id="news-code"
            type="text"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="例: 7203"
            className={inputCls}
            autoComplete="off"
          />
        </div>
      </div>

      {error && (
        <div className="rounded-md bg-down-weak px-3 py-2 text-[13px] text-down">{error}</div>
      )}

      <button
        type="submit"
        disabled={submitting}
        className="rounded-md bg-accent px-4 py-1.5 font-semibold text-[13px] text-white disabled:cursor-not-allowed disabled:bg-surface-2 disabled:text-ink-subtle"
      >
        {submitting ? "要約中…" : "ニュースを取り込むのだ"}
      </button>
    </form>
  );
}
