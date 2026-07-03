"use client";

// 投資家プロファイル画面（ADR-082・docs/screens.md）。
// policy（規範＝どうすべきか）と分離した「記述＝この投資家の行動の癖」の単一文書。夜バッチ profiler
// が取引台帳から傾向メモ（profile_note）を承認制で起票し、人間が承認すると本文へ追記される（ADR-009）。
// 使い方は鏡・反追従＝AI は癖を打ち消す方向に助言する（迎合しない・CORE の反追従節が縛る）。
// データは lib/api 経由のブラウザ fetch のみ（DB に触れない・ADR-005）。density-first・DESIGN.md トークン。
// 操作（保存/承認/却下）で本文と一覧が書き換わるため useApi でなく useState（frontend-component-pattern (c)）。

import { inputCls } from "@/components/ui/Field";
import { StatusBlock } from "@/components/ui/StatusBlock";
import {
  type InvestorProfile,
  type ProfileNote,
  approveProposal,
  getProfile,
  getProfileNotes,
  putProfile,
  rejectProposal,
} from "@/lib/api";
import { useEffect, useState } from "react";

export default function ProfilePage() {
  // 本文（active 文書）と編集ドラフト。保存/承認で書き換わるため useState で持つ。
  const [profile, setProfile] = useState<InvestorProfile | null>(null);
  const [bodyDraft, setBodyDraft] = useState("");
  // pending の傾向メモ一覧（承認/却下で書き換わる）。
  const [notes, setNotes] = useState<ProfileNote[] | null>(null);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [savingBody, setSavingBody] = useState(false);
  const [actionErr, setActionErr] = useState<string | null>(null);
  // 承認/却下の操作中メモ id（ボタン無効化用）。
  const [busyIds, setBusyIds] = useState<Set<number>>(new Set());

  // 初回ロード（本文＋pending メモをまとめて取得）。以降は操作起点で setState する。
  useEffect(() => {
    let ignore = false;
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);
    Promise.all([getProfile(ctrl.signal), getProfileNotes(ctrl.signal)])
      .then(([p, n]) => {
        if (ignore) return;
        setProfile(p);
        setBodyDraft(p.body);
        setNotes(n);
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
  }, []);

  function setBusy(id: number, on: boolean) {
    setBusyIds((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  const dirty = profile !== null && bodyDraft !== profile.body;

  // 本文を手編集で全文置換（putProfile）。active は人間の承認/編集でのみ育つ（ADR-009）。
  async function onSaveBody() {
    setSavingBody(true);
    setActionErr(null);
    try {
      const updated = await putProfile(bodyDraft);
      setProfile(updated);
      setBodyDraft(updated.body);
    } catch (e) {
      setActionErr(`本文の保存に失敗: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setSavingBody(false);
    }
  }

  // 傾向メモを承認（approveProposal）。本文へ追記されるので profile を取り直し、一覧から除く。
  async function onApprove(note: ProfileNote) {
    setBusy(note.id, true);
    setActionErr(null);
    try {
      await approveProposal(note.id);
      const refreshed = await getProfile();
      setProfile(refreshed);
      setBodyDraft(refreshed.body);
      setNotes((prev) => (prev ?? []).filter((n) => n.id !== note.id));
    } catch (e) {
      setActionErr(`承認に失敗（#${note.id}）: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(note.id, false);
    }
  }

  // 傾向メモを却下（rejectProposal）。本文には触れず一覧から除く。
  async function onReject(note: ProfileNote) {
    setBusy(note.id, true);
    setActionErr(null);
    try {
      await rejectProposal(note.id);
      setNotes((prev) => (prev ?? []).filter((n) => n.id !== note.id));
    } catch (e) {
      setActionErr(`却下に失敗（#${note.id}）: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(note.id, false);
    }
  }

  return (
    <>
      <div className="mb-3">
        <div className="font-semibold text-[20px] tracking-[-0.4px]">投資家プロファイル</div>
        <div className="mt-0.5 text-[12px] text-ink-muted">
          方針（policy）とは別に、あなた自身の「行動の癖」を記述として持つのだ。夜のバッチが取引台帳
          から傾向メモを提案し、承認すると本文に積み上がるのだ。AI はこの癖を
          <b>打ち消す方向</b>（鏡・反追従）で助言に使うのだ（迎合しない・ADR-082/009）。
        </div>
      </div>

      {actionErr && (
        <div className="mb-3 rounded-md bg-down-weak px-3 py-2 text-[12px] text-down">
          ⚠ {actionErr}
        </div>
      )}

      <StatusBlock
        loading={loading}
        error={error}
        className="rounded-lg border border-hairline bg-surface-1 p-4"
        errorHint="backend 起動を確認するのだ。"
      >
        {/* 承認待ちの傾向メモ（夜バッチ profiler が起票）。承認で本文へ追記・却下で消す。 */}
        <div className="mb-3 rounded-lg border border-hairline bg-surface-1 p-3">
          <div className="mb-2 font-semibold text-[13px]">
            承認待ちの傾向メモ{notes && notes.length > 0 ? `（${notes.length}）` : ""}
          </div>
          {notes && notes.length > 0 ? (
            <div className="grid gap-2">
              {notes.map((note) => (
                <ProfileNoteRow
                  key={note.id}
                  note={note}
                  busy={busyIds.has(note.id)}
                  onApprove={() => onApprove(note)}
                  onReject={() => onReject(note)}
                />
              ))}
            </div>
          ) : (
            <div className="text-[12px] text-ink-subtle">
              承認待ちのメモはないのだ（夜のバッチが台帳から癖を見つけると、ここに提案が出るのだ）。
            </div>
          )}
        </div>

        {/* 本文（active 文書）の手編集。全文置換で保存する。 */}
        <div className="rounded-lg border border-hairline bg-surface-1 p-3">
          <div className="mb-2 flex items-center justify-between gap-2">
            <div className="font-semibold text-[13px]">プロファイル本文</div>
            {profile?.updated_at && (
              <span className="text-[11px] text-ink-subtle">
                更新 {profile.updated_at.slice(0, 10)}
              </span>
            )}
          </div>
          <textarea
            className={`${inputCls} min-h-40 resize-y`}
            value={bodyDraft}
            onChange={(e) => setBodyDraft(e.target.value)}
            placeholder="まだ育っていないのだ。上の傾向メモを承認するか、ここに直接書けるのだ。"
            aria-label="投資家プロファイル本文"
          />
          <div className="mt-2 flex items-center gap-2">
            <button
              type="button"
              onClick={onSaveBody}
              disabled={savingBody || !dirty}
              className="rounded-md bg-accent px-3 py-1.5 text-[13px] text-white disabled:bg-surface-2 disabled:text-ink-subtle"
            >
              {savingBody ? "保存中…" : "保存"}
            </button>
            {dirty && !savingBody && (
              <button
                type="button"
                onClick={() => profile && setBodyDraft(profile.body)}
                className="rounded-md bg-surface-2 px-3 py-1.5 text-[13px] text-ink-muted hover:text-ink"
              >
                取消
              </button>
            )}
          </div>
        </div>
      </StatusBlock>
    </>
  );
}

// --- 傾向メモ 1 行（feature 相当・props で受けて描画、承認/却下を親へ返す）---

type ProfileNoteRowProps = {
  note: ProfileNote;
  busy: boolean;
  onApprove: () => void;
  onReject: () => void;
};

function ProfileNoteRow({ note, busy, onApprove, onReject }: ProfileNoteRowProps) {
  return (
    <div className="rounded-lg border border-hairline-soft bg-canvas p-2.5">
      <p className="text-[13px] text-ink">{note.text}</p>
      {note.evidence && <p className="mt-1 text-[12px] text-ink-muted">根拠: {note.evidence}</p>}
      <div className="mt-2 flex items-center gap-2">
        <button
          type="button"
          onClick={onApprove}
          disabled={busy}
          className="rounded-md bg-surface-2 px-2.5 py-1 text-[12px] text-up hover:bg-surface-3 disabled:text-ink-subtle"
        >
          承認して本文に追記
        </button>
        <button
          type="button"
          onClick={onReject}
          disabled={busy}
          className="rounded-md px-2.5 py-1 text-[12px] text-ink-subtle hover:text-down disabled:text-ink-subtle"
        >
          却下
        </button>
        {note.created_date && (
          <span className="ml-auto text-[11px] text-ink-subtle">{note.created_date}</span>
        )}
      </div>
    </div>
  );
}
