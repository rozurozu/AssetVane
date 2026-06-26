"use client";

// 知識カード管理画面（ADR-062・docs/api.md「知識カード」）。
// 非自明な投資知識をカードとして登録し、AI 審査（triage）で status を振り分け、人間が active 化する。
// 設計の規律: 規律は CORE（不変プロンプト）、一般常識は LLM、ここは“非自明な知識”を置く層。
// active 化＝本番助言に効く＝人間の最終承認（ADR-009）。
// データは lib/api 経由のブラウザ fetch のみ（DB に触れない・ADR-005）。density-first・DESIGN.md トークン。
// 操作（追加/審査/有効化/削除/編集）で一覧が書き換わるため useApi ではなく useState で持つ
// （frontend-component-pattern (c)・操作起点の更新）。初回は useEffect で取得する。

import { inputCls, labelCls } from "@/components/ui/Field";
import { StatusBlock } from "@/components/ui/StatusBlock";
import {
  type CardCreateIn,
  type CardLevel,
  type CardOut,
  type CardStatus,
  type TriageOut,
  activateCard,
  deleteCard,
  getCards,
  postCard,
  putCard,
  triageCard,
} from "@/lib/api";
import { useEffect, useState } from "react";

// status フィルタタブ（全＋5 状態）。値 undefined は全件。
const STATUS_TABS: { key: string; label: string; value: CardStatus | undefined }[] = [
  { key: "all", label: "全", value: undefined },
  { key: "draft", label: "draft", value: "draft" },
  { key: "active", label: "active", value: "active" },
  { key: "needs_quant", label: "needs_quant", value: "needs_quant" },
  { key: "to_core", label: "to_core", value: "to_core" },
  { key: "rejected", label: "rejected", value: "rejected" },
];

// status バッジの色（DESIGN.md トークンのみ・生色なし）。
// active=承認済み（up）/ draft=未審査（neutral）/ needs_quant=計算待ち（warning）/
// to_core=昇格候補（accent）/ rejected=却下（down）。
const STATUS_BADGE: Record<string, string> = {
  active: "bg-up-weak text-up",
  draft: "bg-surface-2 text-ink-muted",
  needs_quant: "bg-surface-2 text-warning",
  to_core: "bg-accent-weak text-accent",
  rejected: "bg-down-weak text-down",
};

// level セレクトの選択肢（空=未指定）。backend CardLevel と 1:1。
const LEVEL_OPTIONS: { value: CardLevel | ""; label: string }[] = [
  { value: "", label: "（未指定）" },
  { value: "stock", label: "stock（銘柄）" },
  { value: "sector", label: "sector（業種）" },
  { value: "market", label: "market（市況）" },
  { value: "general", label: "general（一般）" },
];

function StatusBadge({ status }: { status: string }) {
  const cls = STATUS_BADGE[status] ?? "bg-surface-2 text-ink-muted";
  return (
    <span className={`rounded-sm px-1.5 py-0.5 text-[11px] font-medium ${cls}`}>{status}</span>
  );
}

export default function CardsPage() {
  // status フィルタ（undefined = 全件）。
  const [status, setStatus] = useState<CardStatus | undefined>(undefined);

  // 一覧（操作で書き換わるため useState・初回は useEffect で取得）。
  const [items, setItems] = useState<CardOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // 行ごとの操作中 id 集合（審査/有効化/削除/保存で共用・ボタン無効化に使う）。
  const [busyIds, setBusyIds] = useState<Set<number>>(new Set());

  // 操作の失敗メッセージ（一覧上部に表示・watchlist の actionErr と対称）。
  const [actionErr, setActionErr] = useState<string | null>(null);

  // 直近の審査結果（id → TriageOut）。インライン表示用。triage=null（審査不能）は別管理。
  const [triageResults, setTriageResults] = useState<Record<number, TriageOut | null>>({});

  useEffect(() => {
    let ignore = false;
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);
    getCards(status, ctrl.signal)
      .then((rows) => {
        if (!ignore) setItems(rows);
      })
      .catch((e) => {
        if (ignore || ctrl.signal.aborted) return;
        setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!ignore) setLoading(false);
      });
    return () => {
      ignore = true;
      ctrl.abort();
    };
  }, [status]);

  function setBusy(id: number, on: boolean) {
    setBusyIds((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  // 一覧から 1 件を差し替える（審査/有効化/保存のレスポンスで確定値に更新）。
  // status フィルタ中で対象外の status になった場合は一覧から除く。
  function replaceCard(updated: CardOut) {
    setItems((prev) => {
      const base = prev ?? [];
      if (status && updated.status !== status) {
        return base.filter((c) => c.id !== updated.id);
      }
      return base.map((c) => (c.id === updated.id ? updated : c));
    });
  }

  // 追加（postCard→一覧更新）。現在の status フィルタが draft 以外なら先頭に積まない
  // （backend は常に draft で作るため・フィルタと不整合にしない）。
  async function onCreate(input: CardCreateIn) {
    const created = await postCard(input);
    setItems((prev) => {
      const base = prev ?? [];
      if (status && created.status !== status) return base;
      return [created, ...base];
    });
  }

  // AI 審査（triageCard）。triage 結果をインライン表示し、card は確定値で差し替える。
  async function onTriage(card: CardOut) {
    setBusy(card.id, true);
    setActionErr(null);
    try {
      const res = await triageCard(card.id);
      setTriageResults((prev) => ({ ...prev, [card.id]: res.triage }));
      replaceCard(res.card);
    } catch (e) {
      setActionErr(`審査に失敗（#${card.id}）: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(card.id, false);
    }
  }

  // 承認して有効化（activateCard）。確定値で差し替える。
  async function onActivate(card: CardOut) {
    setBusy(card.id, true);
    setActionErr(null);
    try {
      const updated = await activateCard(card.id);
      replaceCard(updated);
    } catch (e) {
      setActionErr(`有効化に失敗（#${card.id}）: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(card.id, false);
    }
  }

  // 削除（deleteCard・204）。成功で行を除去。
  async function onRemove(card: CardOut) {
    if (!window.confirm(`知識カード「${card.title}」を削除していい？`)) return;
    setBusy(card.id, true);
    setActionErr(null);
    try {
      await deleteCard(card.id);
      setItems((prev) => (prev ?? []).filter((c) => c.id !== card.id));
    } catch (e) {
      setActionErr(`削除に失敗（#${card.id}）: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(card.id, false);
    }
  }

  // 編集の保存（putCard・部分更新）。確定値で差し替える。
  async function onSaveEdit(id: number, values: { body: string; always_inject: boolean }) {
    setBusy(id, true);
    setActionErr(null);
    try {
      const updated = await putCard(id, values);
      replaceCard(updated);
    } catch (e) {
      setActionErr(`保存に失敗（#${id}）: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(id, false);
    }
  }

  return (
    <>
      <div className="mb-3">
        <div className="font-semibold text-[20px] tracking-[-0.4px]">知識カード</div>
        <div className="mt-0.5 text-[12px] text-ink-muted">
          非自明な投資知識をここに置く。規律は CORE プロンプト、一般常識は LLM が持つので、ここは
          “非自明な知識” 専用なのだ。「AI 審査」で status
          を振り分け、「承認して有効化」（＝本番助言に効く）は
          人間が最終承認するのだ（ADR-009/062）。
        </div>
      </div>

      {/* 追加フォーム（title/body/when_to_apply/level/source）。送信→postCard→一覧更新。 */}
      <CreateCardForm onCreate={onCreate} />

      {/* status 切替タブ。アクティブは surface-2 へ lift（青の面塗りはしない＝DESIGN.md）。 */}
      <div className="mb-3 flex flex-wrap gap-1">
        {STATUS_TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => setStatus(t.value)}
            className={`rounded-md px-2.5 py-1 text-[12px] ${
              status === t.value
                ? "bg-surface-2 font-semibold text-ink"
                : "text-ink-muted hover:bg-surface-2 hover:text-ink"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* 操作失敗を一覧上部に表示（watchlist actionErr と対称）。 */}
      {actionErr && (
        <div className="mb-3 rounded-md bg-down-weak px-3 py-2 text-[12px] text-down">
          ⚠ {actionErr}
        </div>
      )}

      <StatusBlock
        loading={loading}
        error={error}
        empty={items?.length === 0}
        className="rounded-lg border border-hairline bg-surface-1 p-4"
        errorHint="backend 起動を確認するのだ。"
        emptyText="まだ知識カードがないのだ。上のフォームから追加するのだ。"
      >
        {items && (
          <div className="grid gap-2">
            {items.map((card) => (
              <CardRow
                key={card.id}
                card={card}
                busy={busyIds.has(card.id)}
                triage={card.id in triageResults ? triageResults[card.id] : undefined}
                onTriage={() => onTriage(card)}
                onActivate={() => onActivate(card)}
                onRemove={() => onRemove(card)}
                onSave={(values) => onSaveEdit(card.id, values)}
              />
            ))}
          </div>
        )}
      </StatusBlock>
    </>
  );
}

// --- 追加フォーム（feature 相当・mutation を所有し onCreate で親へ返す）---

type CreateCardFormProps = {
  onCreate: (input: CardCreateIn) => Promise<void>;
};

function CreateCardForm({ onCreate }: CreateCardFormProps) {
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [whenToApply, setWhenToApply] = useState("");
  const [level, setLevel] = useState<CardLevel | "">("");
  const [source, setSource] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const canSubmit = title.trim() !== "" && body.trim() !== "" && !busy;

  async function onSubmit() {
    if (!canSubmit) return;
    setBusy(true);
    setErr(null);
    try {
      await onCreate({
        title: title.trim(),
        body: body.trim(),
        when_to_apply: whenToApply.trim() || null,
        level: level || null,
        source: source.trim() || null,
      });
      // 成功で入力をクリア。
      setTitle("");
      setBody("");
      setWhenToApply("");
      setLevel("");
      setSource("");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mb-3 rounded-lg border border-hairline bg-surface-1 p-3">
      <div className="mb-2 font-semibold text-[13px]">カードを追加</div>
      <div className="grid gap-2">
        <div>
          <label htmlFor="card-title" className={labelCls}>
            タイトル
          </label>
          <input
            id="card-title"
            className={inputCls}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="例: 増配発表直後の押し目は拾い場になりやすい"
          />
        </div>
        <div>
          <label htmlFor="card-body" className={labelCls}>
            本文（非自明な知識の中身）
          </label>
          <textarea
            id="card-body"
            className={`${inputCls} min-h-20 resize-y`}
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder="どういう知識か。AI 審査が active/needs_quant/to_core/rejected を判断する材料になる。"
          />
        </div>
        <div>
          <label htmlFor="card-when" className={labelCls}>
            適用条件（when_to_apply・任意）
          </label>
          <input
            id="card-when"
            className={inputCls}
            value={whenToApply}
            onChange={(e) => setWhenToApply(e.target.value)}
            placeholder="この知識が効く状況。埋め込み検索のキーになる（任意）"
          />
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <div className="w-44">
            <label htmlFor="card-level" className={labelCls}>
              level（任意）
            </label>
            <select
              id="card-level"
              className={inputCls}
              value={level}
              onChange={(e) => setLevel(e.target.value as CardLevel | "")}
            >
              {LEVEL_OPTIONS.map((o) => (
                <option key={o.value || "none"} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>
          <div className="flex-1 min-w-48">
            <label htmlFor="card-source" className={labelCls}>
              source（出所 URL・任意）
            </label>
            <input
              id="card-source"
              className={inputCls}
              value={source}
              onChange={(e) => setSource(e.target.value)}
              placeholder="https://…（任意）"
            />
          </div>
          <button
            type="button"
            onClick={onSubmit}
            disabled={!canSubmit}
            className="rounded-md bg-accent px-3 py-1.5 text-[13px] text-white disabled:bg-surface-2 disabled:text-ink-subtle"
          >
            {busy ? "追加中…" : "追加（draft）"}
          </button>
        </div>
        {err && <span className="text-[12px] text-down">⚠ {err}</span>}
      </div>
    </div>
  );
}

// --- カード 1 行（feature 相当・props で受けて描画、編集フォームを内包）---

type CardRowProps = {
  card: CardOut;
  busy: boolean;
  // undefined=未審査 / null=審査不能（面未設定等） / TriageOut=審査済み。
  triage: TriageOut | null | undefined;
  onTriage: () => void;
  onActivate: () => void;
  onRemove: () => void;
  onSave: (values: { body: string; always_inject: boolean }) => Promise<void>;
};

function CardRow({ card, busy, triage, onTriage, onActivate, onRemove, onSave }: CardRowProps) {
  const [editing, setEditing] = useState(false);
  const [bodyDraft, setBodyDraft] = useState(card.body);
  const [alwaysInject, setAlwaysInject] = useState(card.always_inject);

  // 親の確定値が変わったら編集ドラフトを同期（保存成功・外部更新）。
  useEffect(() => {
    setBodyDraft(card.body);
    setAlwaysInject(card.always_inject);
  }, [card.body, card.always_inject]);

  // draft / needs_quant のとき「承認して有効化」を出す（人間承認で active 化＝ADR-009）。
  const canActivate = card.status === "draft" || card.status === "needs_quant";

  async function onSaveClick() {
    await onSave({ body: bodyDraft, always_inject: alwaysInject });
    setEditing(false);
  }

  return (
    <div className="rounded-lg border border-hairline bg-surface-1 p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <StatusBadge status={card.status} />
            <span className="font-semibold text-[14px]">{card.title}</span>
          </div>
          {/* メタ（level/theme/linked_signal_type/埋め込み/更新日）を控えめに並べる。 */}
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-ink-subtle">
            {card.level && <span>level: {card.level}</span>}
            {card.sector17_code && <span>S17: {card.sector17_code}</span>}
            {card.theme && <span>theme: {card.theme}</span>}
            {card.linked_signal_type && <span>signal: {card.linked_signal_type}</span>}
            {card.always_inject && <span className="text-accent">常時注入</span>}
            <span>{card.embedded_at ? "埋込済" : "未埋込"}</span>
            {card.updated_at && <span>更新 {card.updated_at.slice(0, 10)}</span>}
          </div>
        </div>
      </div>

      {/* 本文（編集中は textarea＋always_inject トグル、通常は読み取り）。 */}
      {editing ? (
        <div className="mt-2 grid gap-2">
          <textarea
            className={`${inputCls} min-h-20 resize-y`}
            value={bodyDraft}
            onChange={(e) => setBodyDraft(e.target.value)}
            aria-label="本文"
          />
          <label className="flex items-center gap-2 text-[12px] text-ink-muted">
            <input
              type="checkbox"
              checked={alwaysInject}
              onChange={(e) => setAlwaysInject(e.target.checked)}
              className="h-3.5 w-3.5 accent-accent"
            />
            常時注入（always_inject）
          </label>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={onSaveClick}
              disabled={busy}
              className="rounded-md bg-accent px-3 py-1.5 text-[12px] text-white disabled:bg-surface-2 disabled:text-ink-subtle"
            >
              {busy ? "保存中…" : "保存"}
            </button>
            <button
              type="button"
              onClick={() => {
                setEditing(false);
                setBodyDraft(card.body);
                setAlwaysInject(card.always_inject);
              }}
              disabled={busy}
              className="rounded-md bg-surface-2 px-3 py-1.5 text-[12px] text-ink-muted hover:text-ink disabled:opacity-50"
            >
              取消
            </button>
          </div>
        </div>
      ) : (
        <>
          <p className="mt-2 whitespace-pre-wrap text-[13px] text-ink">{card.body}</p>
          {card.when_to_apply && (
            <p className="mt-1 text-[12px] text-ink-muted">適用条件: {card.when_to_apply}</p>
          )}
          {card.quant_note && (
            <p className="mt-1 text-[12px] text-warning">要計算メモ: {card.quant_note}</p>
          )}
        </>
      )}

      {/* 審査結果のインライン表示（triage=null は面未設定等で審査できなかったとき）。 */}
      {triage !== undefined && (
        <div className="mt-2 rounded-md border border-hairline-soft bg-canvas px-2.5 py-2 text-[12px]">
          {triage === null ? (
            <span className="text-ink-subtle">
              審査できなかったのだ（LLM 面が未設定か応答が不正・status は据え置き）。
            </span>
          ) : (
            <>
              <span className="font-semibold text-ink">審査: {triage.verdict}</span>
              <span className="ml-2 text-ink-muted">{triage.reason}</span>
              {triage.quant_note && (
                <div className="mt-0.5 text-warning">要計算: {triage.quant_note}</div>
              )}
            </>
          )}
        </div>
      )}

      {/* 操作ボタン群。 */}
      {!editing && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={onTriage}
            disabled={busy}
            className="rounded-md bg-surface-2 px-2.5 py-1 text-[12px] text-ink hover:text-accent disabled:text-ink-subtle"
          >
            {busy ? "処理中…" : "AI 審査"}
          </button>
          {canActivate && (
            <button
              type="button"
              onClick={onActivate}
              disabled={busy}
              className="rounded-md bg-surface-2 px-2.5 py-1 text-[12px] text-up hover:bg-surface-3 disabled:text-ink-subtle"
            >
              承認して有効化
            </button>
          )}
          <button
            type="button"
            onClick={() => setEditing(true)}
            disabled={busy}
            className="rounded-md px-2.5 py-1 text-[12px] text-ink-muted hover:text-ink disabled:text-ink-subtle"
          >
            編集
          </button>
          <button
            type="button"
            onClick={onRemove}
            disabled={busy}
            className="rounded-md px-2.5 py-1 text-[12px] text-ink-subtle hover:text-down disabled:text-ink-subtle"
          >
            削除
          </button>
        </div>
      )}
    </div>
  );
}
