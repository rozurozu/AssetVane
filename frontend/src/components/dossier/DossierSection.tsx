"use client";

// 銘柄詳細内のドシエセクション（screens.md #3「ドシエは銘柄詳細内のセクション」・phase4-spec.md §6）。
// getDossier(code) で取得し、AI 生成 summary_md を react-markdown + rehype-sanitize で安全に描画
// （L-24・AI 生成 markdown の XSS 対策）。ソース台帳（要約＋URL・本文なし＝ADR-020）を一覧。
// 未調査（last_investigated_at===null）は「調査する」ボタンを出し investigateStock で同期調査
// （L-23・完了まで待つためローディング必須）→ 最新ドシエに差し替え。watchlist 追加もここから。
// データは lib/api.ts 経由（ADR-005）。DESIGN.md トークン・density-first。

import { StatusBlock } from "@/components/ui/StatusBlock";
import { type Dossier, getDossier, investigateStock, postWatchlist } from "@/lib/api";
import { useApi } from "@/lib/use-api";
import Link from "next/link";
import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";

type Props = { code: string };

export function DossierSection({ code }: Props) {
  // 初回ロードは useApi（GET）。以降は調査ボタンで書き換わるため useState に移す折衷
  // （frontend-component-pattern (c)・操作起点の更新は useState）。
  const { data, error, loading } = useApi((signal) => getDossier(code, signal), [code]);
  const [dossier, setDossier] = useState<Dossier | null>(null);
  const [investigating, setInvestigating] = useState(false);
  const [actionErr, setActionErr] = useState<string | null>(null);
  // watchlist 追加（成否のフィードバックのみ・一覧は別ページ）。
  const [watching, setWatching] = useState(false);
  const [watchNote, setWatchNote] = useState<string | null>(null);

  // useApi の data（初回）と調査後の useState を合成。後者が優先。
  const current = dossier ?? data;
  const investigated = current?.last_investigated_at != null;
  // サーバのプロセスメモリが「調査中」を示す（ADR-076）。リロード後も GET でこれを読むので、
  // ローカルの投稿中フラグと OR して「調査中…」表示を復元する（二重起動もこれで防ぐ）。
  const serverInvestigating = current?.investigating ?? false;
  const isInvestigating = investigating || serverInvestigating;

  // 完了検知のポーリング（ADR-076）: サーバが調査中を示し、かつこのタブが同期 POST を走らせて
  // いない（＝リロード/別タブの観測者）ときだけ、getDossier を数秒間隔で叩いて完了を待つ。
  // サーバの investigating が false になったら止める（依存に入れて自己終了）。暴走を避けるため
  // ポーリング回数に上限を設ける（数十秒の調査を十分カバーする長さ）。
  useEffect(() => {
    if (!serverInvestigating || investigating) return;
    let polls = 0;
    const MAX_POLLS = 150; // 4s × 150 = 10 分（実運用の数十秒に対し十分な保険）
    const id = setInterval(async () => {
      polls += 1;
      if (polls > MAX_POLLS) {
        clearInterval(id);
        return;
      }
      try {
        setDossier(await getDossier(code));
      } catch {
        // ポーリングの失敗は握りつぶす（次周期で回復・既存表示は維持）。
      }
    }, 4000);
    return () => clearInterval(id);
  }, [code, serverInvestigating, investigating]);

  // 調査を起動（同期・完了まで待つ＝L-23）。完了後の最新ドシエで差し替え。
  async function onInvestigate() {
    setInvestigating(true);
    setActionErr(null);
    try {
      const res = await investigateStock(code);
      setDossier(res.dossier);
    } catch (e) {
      setActionErr(e instanceof Error ? e.message : String(e));
    } finally {
      setInvestigating(false);
    }
  }

  // watchlist へ追加（重複でも backend は既存行を 200 で返す）。
  async function onAddWatchlist() {
    setWatching(true);
    setWatchNote(null);
    try {
      await postWatchlist(code);
      setWatchNote("watchlist に追加したのだ。");
    } catch (e) {
      setWatchNote(e instanceof Error ? e.message : String(e));
    } finally {
      setWatching(false);
    }
  }

  return (
    <section className="rounded-lg border border-hairline bg-surface-1">
      <div className="flex items-center justify-between border-hairline border-b px-3 py-2">
        <h2 className="font-semibold text-[14px] tracking-[-0.1px]">ドシエ（定性調査）</h2>
        <div className="flex items-center gap-2">
          {current && (
            <span className="text-[11px] text-ink-subtle">
              最終調査{" "}
              {current.last_investigated_at ? current.last_investigated_at.slice(0, 10) : "—"}
            </span>
          )}
          {/* この銘柄の知識ノート（アノマリー等）を追加する導線＝/cards に code をプリフィル
              （ADR-062 追補）。ドシエ＝揮発的な事実の要約とは別に、蓄積する解釈的知見を置く場所。 */}
          <Link
            href={`/cards?code=${encodeURIComponent(code)}`}
            className="rounded-md bg-surface-2 px-2 py-1 text-[12px] text-ink hover:text-accent"
          >
            この銘柄のノート
          </Link>
          {/* watchlist 追加（夜の巡回対象に入れる＝screens.md #3） */}
          <button
            type="button"
            onClick={onAddWatchlist}
            disabled={watching}
            className="rounded-md bg-surface-2 px-2 py-1 text-[12px] text-ink hover:text-accent disabled:text-ink-subtle"
          >
            {watching ? "追加中…" : "Watchlist に追加"}
          </button>
        </div>
      </div>

      <div className="p-3">
        <StatusBlock loading={loading} error={error} errorHint="backend 起動を確認するのだ。">
          {/* watchlist 追加のフィードバック */}
          {watchNote && <div className="mb-3 text-[12px] text-ink-subtle">{watchNote}</div>}

          {!investigated ? (
            // 未調査: 調査ボタンのみ（L-23・同期調査→ローディング→再取得）。
            <div className="py-4 text-center">
              <div className="text-[13px] text-ink-subtle">
                {isInvestigating
                  ? "調査中なのだ…（数十秒かかるのだ）"
                  : "この銘柄はまだ調査されていないのだ。"}
              </div>
              <button
                type="button"
                onClick={onInvestigate}
                disabled={isInvestigating}
                className="mt-3 rounded-md bg-accent px-3 py-1.5 text-[13px] text-white disabled:bg-surface-2 disabled:text-ink-subtle"
              >
                {isInvestigating ? "調査中…（数十秒かかるのだ）" : "調査する"}
              </button>
              {actionErr && (
                <div className="mt-2 text-[12px] text-down">⚠ 調査に失敗: {actionErr}</div>
              )}
            </div>
          ) : (
            <>
              {/* AI 生成 markdown の安全描画（rehype-sanitize で XSS 対策・L-24）。
                  本文は text-ink・見出しは色階層・リンクは accent・リストは詰める（DESIGN.md トークン）。 */}
              {current?.summary_md ? (
                <div className="text-[13px] text-ink leading-[1.55] [&_a]:text-accent [&_a:hover]:underline [&_code]:rounded-sm [&_code]:bg-surface-2 [&_code]:px-1 [&_h1]:mt-3 [&_h1]:mb-1 [&_h1]:font-semibold [&_h1]:text-[16px] [&_h1]:text-ink [&_h2]:mt-3 [&_h2]:mb-1 [&_h2]:font-semibold [&_h2]:text-[14px] [&_h2]:text-ink [&_h3]:mt-2 [&_h3]:mb-1 [&_h3]:font-semibold [&_h3]:text-[13px] [&_h3]:text-ink-muted [&_li]:my-0.5 [&_ol]:my-1.5 [&_ol]:list-decimal [&_ol]:pl-5 [&_p]:my-1.5 [&_strong]:font-semibold [&_strong]:text-ink [&_ul]:my-1.5 [&_ul]:list-disc [&_ul]:pl-5">
                  <Markdown rehypePlugins={[rehypeSanitize]}>{current.summary_md}</Markdown>
                </div>
              ) : (
                <div className="text-[13px] text-ink-subtle">調査要約がまだ空なのだ。</div>
              )}

              {/* key_facts（Tool 由来の事実・ADR-014）。obj があれば軽く列挙。 */}
              {current?.key_facts && Object.keys(current.key_facts).length > 0 && (
                <div className="mt-3 flex flex-wrap gap-2 border-hairline-soft border-t pt-3">
                  {Object.entries(current.key_facts).map(([k, v]) => (
                    <span
                      key={k}
                      className="rounded-sm bg-surface-2 px-1.5 py-0.5 text-[12px] text-ink-muted"
                    >
                      <span className="text-ink-subtle">{k}:</span>{" "}
                      <span className="num text-ink">{String(v)}</span>
                    </span>
                  ))}
                </div>
              )}

              {/* ソース台帳（要約＋URL・本文なし＝ADR-020）。hairline-soft 区切りリスト。 */}
              {current && current.sources.length > 0 && (
                <div className="mt-3 border-hairline-soft border-t pt-3">
                  <div className="mb-2 font-medium text-[11px] text-ink-muted uppercase tracking-[0.3px]">
                    ソース（{current.sources.length}）
                  </div>
                  <ul className="flex flex-col">
                    {current.sources.map((s) => (
                      <li key={s.id} className="border-hairline-soft border-b py-2 last:border-b-0">
                        <div className="flex items-center gap-2">
                          <span className="rounded-sm bg-surface-2 px-1.5 py-0.5 text-[11px] text-ink-muted">
                            {s.source_type}
                          </span>
                          <a
                            href={s.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-[13px] text-accent hover:underline"
                          >
                            {s.title ?? s.url}
                          </a>
                          {s.published_at && (
                            <span className="num ml-auto text-[11px] text-ink-subtle">
                              {s.published_at.slice(0, 10)}
                            </span>
                          )}
                        </div>
                        {s.summary && (
                          <div className="mt-1 text-[12px] text-ink-muted leading-[1.45]">
                            {s.summary}
                          </div>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* 再調査（living document を更新）。 */}
              <div className="mt-3 flex items-center gap-3 border-hairline-soft border-t pt-3">
                <button
                  type="button"
                  onClick={onInvestigate}
                  disabled={isInvestigating}
                  className="rounded-md bg-surface-2 px-2.5 py-1 text-[12px] text-ink hover:text-accent disabled:text-ink-subtle"
                >
                  {isInvestigating ? "再調査中…" : "再調査する"}
                </button>
                {actionErr && <span className="text-[12px] text-down">⚠ {actionErr}</span>}
              </div>
            </>
          )}
        </StatusBlock>
      </div>
    </section>
  );
}
