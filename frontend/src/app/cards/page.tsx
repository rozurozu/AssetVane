"use client";

// 知識カード管理画面（ADR-062・docs/api.md「知識カード」）。
// 非自明な投資知識を「本文を貼るだけ」で登録する（ADR-062 追補・雑追加リデザイン）。追加時に AI が
// title/when_to_apply/level を生成し、verdict で status を振り分ける（rejected/to_core/needs_quant は
// 自動・active 候補は draft 留置＝人間がワンクリック承認＝ADR-009）。AI 未整形でも本文は draft 保存し、
// 行の「AIで整える」から再整形できる。
// 設計の規律: 規律は CORE（不変プロンプト）、一般常識は LLM、ここは“非自明な知識”を置く層。
// データは lib/api 経由のブラウザ fetch のみ（DB に触れない・ADR-005）。density-first・DESIGN.md トークン。
// 操作（追加/再整形/有効化/削除/編集）で一覧が書き換わるため useApi ではなく useState で持つ
// （frontend-component-pattern (c)・操作起点の更新）。初回は useEffect で取得する。

import { inputCls, labelCls } from "@/components/ui/Field";
import { StatusBlock } from "@/components/ui/StatusBlock";
import {
  type CardCreateIn,
  type CardOut,
  type CardStatus,
  activateCard,
  assistCard,
  deleteCard,
  getCards,
  postCard,
  putCard,
} from "@/lib/api";
import { useEffect, useState } from "react";

// status フィルタタブ（全＋5 状態）。値 undefined は全件。
const STATUS_TABS: { key: string; label: string; value: CardStatus | undefined }[] = [
  { key: "draft", label: "承認待ち", value: "draft" },
  { key: "active", label: "active", value: "active" },
  { key: "all", label: "全", value: undefined },
  { key: "needs_quant", label: "needs_quant", value: "needs_quant" },
  { key: "to_core", label: "to_core", value: "to_core" },
  { key: "rejected", label: "rejected", value: "rejected" },
];

// status バッジの色（DESIGN.md トークンのみ・生色なし）。
// active=承認済み（up）/ draft=未審査 or 採用候補（neutral）/ needs_quant=計算待ち（warning）/
// to_core=昇格候補（accent）/ rejected=却下（down）。
const STATUS_BADGE: Record<string, string> = {
  active: "bg-up-weak text-up",
  draft: "bg-surface-2 text-ink-muted",
  needs_quant: "bg-surface-2 text-warning",
  to_core: "bg-accent-weak text-accent",
  rejected: "bg-down-weak text-down",
};

function StatusBadge({ status }: { status: string }) {
  const cls = STATUS_BADGE[status] ?? "bg-surface-2 text-ink-muted";
  return (
    <span className={`rounded-sm px-1.5 py-0.5 text-[11px] font-medium ${cls}`}>{status}</span>
  );
}

export default function CardsPage() {
  // status フィルタ。既定は承認待ち（draft）＝「貼る→AI が選ぶ→承認」の着地点（ADR-062 追補）。
  const [status, setStatus] = useState<CardStatus | undefined>("draft");

  // 一覧（操作で書き換わるため useState・初回は useEffect で取得）。
  const [items, setItems] = useState<CardOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // 行ごとの操作中 id 集合（再整形/有効化/削除/保存で共用・ボタン無効化に使う）。
  const [busyIds, setBusyIds] = useState<Set<number>>(new Set());

  // 操作の失敗メッセージ（一覧上部に表示・watchlist の actionErr と対称）。
  const [actionErr, setActionErr] = useState<string | null>(null);

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

  // 一覧から 1 件を差し替える（再整形/有効化/保存のレスポンスで確定値に更新）。
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

  // 追加（postCard→一覧更新）。追加時 AI が status を決めるので、現在のフィルタに合うときだけ積む
  // （例: 既定 draft フィルタなら active 候補/AI未整形は積まれ、rejected は別タブ）。
  async function onCreate(created: CardOut) {
    setItems((prev) => {
      const base = prev ?? [];
      if (status && created.status !== status) return base;
      return [created, ...base];
    });
  }

  // 既存カードを AI で再整形（assistCard）。確定値で差し替える。triage=null は審査不能（ヒント表示）。
  async function onAssist(card: CardOut) {
    setBusy(card.id, true);
    setActionErr(null);
    try {
      const res = await assistCard(card.id);
      replaceCard(res.card);
      if (res.triage === null) {
        setActionErr(
          `#${card.id} は AI で整えられなかったのだ（triage 面が未設定の可能性・/settings で設定するのだ）。`,
        );
      }
    } catch (e) {
      setActionErr(`再整形に失敗（#${card.id}）: ${e instanceof Error ? e.message : String(e)}`);
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
    const label = card.title || "（AI 未整形のカード）";
    if (!window.confirm(`知識カード「${label}」を削除していい？`)) return;
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

  // weight（重要度）だけ更新（putCard・部分更新）。確定値で差し替える。
  // 「古い/信頼度の低いカードを下げる」用途（ADR-062 追補）。
  async function onSaveWeight(id: number, weight: number) {
    setBusy(id, true);
    setActionErr(null);
    try {
      const updated = await putCard(id, { weight });
      replaceCard(updated);
    } catch (e) {
      setActionErr(`重要度の保存に失敗（#${id}）: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(id, false);
    }
  }

  return (
    <>
      <div className="mb-3">
        <div className="font-semibold text-[20px] tracking-[-0.4px]">知識カード</div>
        <div className="mt-0.5 text-[12px] text-ink-muted">
          非自明な投資知識は本文を貼るだけで登録できるのだ。追加すると AI
          が見出し・使いどころを考え、 却下/CORE
          行き/計算待ち/採用候補に振り分けるのだ。「承認して有効化」（＝本番助言に効く）は
          人間が最終承認するのだ（ADR-009/062）。
        </div>
      </div>

      {/* 追加フォーム（本文＋出所 URL だけ）。送信→postCard（AI が整形＋審査して完成カードを返す）。 */}
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
        emptyText="このタブにカードはないのだ。上のフォームから本文を貼って追加するのだ。"
      >
        {items && (
          <div className="grid gap-2">
            {items.map((card) => (
              <CardRow
                key={card.id}
                card={card}
                busy={busyIds.has(card.id)}
                onAssist={() => onAssist(card)}
                onActivate={() => onActivate(card)}
                onRemove={() => onRemove(card)}
                onSave={(values) => onSaveEdit(card.id, values)}
                onSaveWeight={(weight) => onSaveWeight(card.id, weight)}
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
  onCreate: (created: CardOut) => Promise<void>;
};

function CreateCardForm({ onCreate }: CreateCardFormProps) {
  const [body, setBody] = useState("");
  const [source, setSource] = useState("");
  // 銘柄コード（任意・ADR-062 追補）。埋めると特定銘柄のノートになる（backend が実在検証＋market 解決）。
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // 直近の追加結果（AI 判定）。フォーム下にインライン表示して「却下された/採用候補」を伝える。
  const [lastResult, setLastResult] = useState<CardOut | null>(null);

  // 銘柄詳細の「この銘柄のノートを追加」導線（/cards?code=…）から来たとき code を初期化する
  // （focus.code プリフィル・ADR-062 追補）。useSearchParams の Suspense 制約を避け mount 時に読む。
  useEffect(() => {
    const q = new URLSearchParams(window.location.search).get("code");
    if (q) setCode(q);
  }, []);

  const canSubmit = body.trim() !== "" && !busy;

  async function onSubmit() {
    if (!canSubmit) return;
    setBusy(true);
    setErr(null);
    try {
      const input: CardCreateIn = {
        body: body.trim(),
        source: source.trim() || null,
        code: code.trim() || null,
      };
      const created = await postCard(input);
      setLastResult(created);
      await onCreate(created);
      // 成功で入力をクリア（結果表示は残す）。
      setBody("");
      setSource("");
      setCode("");
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
        {/* 主役は本文。これだけで追加でき、AI が見出し・使いどころ・分類を付ける。 */}
        <div>
          <label htmlFor="card-body" className={labelCls}>
            本文（非自明な知識の中身）
          </label>
          <textarea
            id="card-body"
            className={`${inputCls} min-h-24 resize-y`}
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder="良さげな記事や知識の中身を貼るだけ。AI が見出し・使いどころを考え、採用/却下を判定する。"
          />
        </div>

        <div className="flex flex-wrap items-end gap-2">
          <div className="flex-1 min-w-48">
            <label htmlFor="card-source" className={labelCls}>
              出所（URL・任意）
            </label>
            <input
              id="card-source"
              className={inputCls}
              value={source}
              onChange={(e) => setSource(e.target.value)}
              placeholder="https://…（任意）"
            />
          </div>
          {/* 銘柄コード（任意・ADR-062 追補）。埋めるとその銘柄のノートになる（アノマリー等）。 */}
          <div className="w-40">
            <label htmlFor="card-code" className={labelCls}>
              銘柄コード（任意）
            </label>
            <input
              id="card-code"
              className={inputCls}
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="例 72030 / AAPL"
            />
          </div>
          <button
            type="button"
            onClick={onSubmit}
            disabled={!canSubmit}
            className="rounded-md bg-accent px-3 py-1.5 text-[13px] text-white disabled:bg-surface-2 disabled:text-ink-subtle"
          >
            {busy ? "追加中…（AI が整え中）" : "追加"}
          </button>
        </div>

        {/* 直近の追加結果（AI 判定）をインライン表示。 */}
        {lastResult && (
          <div className="rounded-md border border-hairline-soft bg-canvas px-2.5 py-2 text-[12px]">
            <span className="text-ink-muted">追加した:</span>{" "}
            <StatusBadge status={lastResult.status} />{" "}
            <span className="text-ink">{lastResult.title || "（AI 未整形）"}</span>
            {lastResult.triage_reason && (
              <span className="ml-1 text-ink-muted">— {lastResult.triage_reason}</span>
            )}
          </div>
        )}
        {err && <span className="text-[12px] text-down">⚠ {err}</span>}
      </div>
    </div>
  );
}

// --- カード 1 行（feature 相当・props で受けて描画、編集フォームを内包）---

type CardRowProps = {
  card: CardOut;
  busy: boolean;
  onAssist: () => void;
  onActivate: () => void;
  onRemove: () => void;
  onSave: (values: { body: string; always_inject: boolean }) => Promise<void>;
  onSaveWeight: (weight: number) => Promise<void>;
};

// weight（重要度）の編集レンジ（ADR-062 追補・0.1〜3.0・0.1 刻み・既定 1.0）。
const WEIGHT_MIN = 0.1;
const WEIGHT_MAX = 3.0;
const WEIGHT_STEP = 0.1;

function CardRow({
  card,
  busy,
  onAssist,
  onActivate,
  onRemove,
  onSave,
  onSaveWeight,
}: CardRowProps) {
  const [editing, setEditing] = useState(false);
  const [bodyDraft, setBodyDraft] = useState(card.body);
  const [alwaysInject, setAlwaysInject] = useState(card.always_inject);
  // weight の編集ドラフト（数値入力 or スライダ）。確定は変更時に onSaveWeight。
  const [weightDraft, setWeightDraft] = useState(card.weight);

  // 親の確定値が変わったら編集ドラフトを同期（保存成功・外部更新）。
  useEffect(() => {
    setBodyDraft(card.body);
    setAlwaysInject(card.always_inject);
    setWeightDraft(card.weight);
  }, [card.body, card.always_inject, card.weight]);

  // weight を確定（値が変わったときだけ putCard）。0.1〜3.0 にクランプして保存。
  function commitWeight(next: number) {
    const clamped = Math.min(WEIGHT_MAX, Math.max(WEIGHT_MIN, next));
    setWeightDraft(clamped);
    if (clamped !== card.weight) onSaveWeight(clamped);
  }

  // draft / needs_quant のとき「承認して有効化」を出す（人間承認で active 化＝ADR-009）。
  const canActivate = card.status === "draft" || card.status === "needs_quant";
  // title が空 = AI 未整形（追加時に triage 面が未設定/失敗）。再整形を促す（ADR-062 追補）。
  const unrefined = card.title.trim() === "";

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
            {unrefined ? (
              <span className="rounded-sm bg-surface-2 px-1.5 py-0.5 text-[11px] text-ink-subtle">
                AI 未整形
              </span>
            ) : (
              <span className="font-semibold text-[14px]">{card.title}</span>
            )}
          </div>
          {/* メタ（level/theme/linked_signal_type/重要度/埋め込み/追加日・更新日）を控えめに並べる。
              適用条件（when_to_apply）は UI に出さない（AI が埋める内部キー・ADR-062 追補）。 */}
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-ink-subtle">
            {card.level && <span>level: {card.level}</span>}
            {/* 銘柄ノート（ADR-062 追補）＝この銘柄を見ているとき/夜の注目候補で exact-match 注入。 */}
            {card.code && (
              <span className="text-accent">
                銘柄: {card.code}
                {card.market ? `（${card.market}）` : ""}
              </span>
            )}
            {card.sector17_code && <span>S17: {card.sector17_code}</span>}
            {card.theme && <span>theme: {card.theme}</span>}
            {card.linked_signal_type && <span>signal: {card.linked_signal_type}</span>}
            <span>重要度: {card.weight.toFixed(1)}</span>
            {card.always_inject && <span className="text-accent">常時注入</span>}
            <span>{card.embedded_at ? "埋込済" : "未埋込"}</span>
            {card.created_at && <span>追加 {card.created_at.slice(0, 10)}</span>}
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
          {/* AI 審査理由（triage_reason）を永続表示。AI 未整形なら設定を促す。 */}
          {card.triage_reason ? (
            <p className="mt-1 text-[12px] text-ink-muted">審査理由: {card.triage_reason}</p>
          ) : (
            unrefined && (
              <p className="mt-1 text-[12px] text-ink-subtle">
                AI で整えられていないのだ（triage 面を /settings
                で設定して「AIで整える」を押すのだ）。
              </p>
            )
          )}
          {card.quant_note && (
            <p className="mt-1 text-[12px] text-warning">要計算メモ: {card.quant_note}</p>
          )}
        </>
      )}

      {/* 重要度（weight）の編集。スライダ＋数値入力でその場保存（編集モード外でも触れる）。
          「古い/信頼度の低いカードを下げる」用途のヒントを添える（ADR-062 追補）。 */}
      {!editing && (
        <div className="mt-2 flex flex-wrap items-center gap-3 text-[11px] text-ink-muted">
          <label htmlFor={`card-weight-${card.id}`} className="flex items-center gap-2">
            <span>重要度</span>
            <input
              id={`card-weight-${card.id}`}
              type="range"
              min={WEIGHT_MIN}
              max={WEIGHT_MAX}
              step={WEIGHT_STEP}
              value={weightDraft}
              disabled={busy}
              onChange={(e) => setWeightDraft(Number(e.target.value))}
              onPointerUp={(e) => commitWeight(Number((e.target as HTMLInputElement).value))}
              onKeyUp={(e) => commitWeight(Number((e.target as HTMLInputElement).value))}
              className="w-40 accent-accent disabled:opacity-50"
            />
          </label>
          <input
            type="number"
            min={WEIGHT_MIN}
            max={WEIGHT_MAX}
            step={WEIGHT_STEP}
            value={weightDraft}
            disabled={busy}
            onChange={(e) => setWeightDraft(Number(e.target.value))}
            onBlur={(e) => commitWeight(Number(e.target.value))}
            aria-label="重要度（数値入力）"
            className="w-16 rounded-md border border-hairline bg-canvas px-2 py-1 text-[12px] text-ink outline-none focus:border-accent disabled:opacity-50"
          />
          <span className="text-ink-subtle">
            古い/信頼度の低いカードは下げる（0.1〜3.0・既定 1.0）
          </span>
        </div>
      )}

      {/* 操作ボタン群。 */}
      {!editing && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={onAssist}
            disabled={busy}
            className="rounded-md bg-surface-2 px-2.5 py-1 text-[12px] text-ink hover:text-accent disabled:text-ink-subtle"
          >
            {busy ? "処理中…" : "AIで整える"}
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
