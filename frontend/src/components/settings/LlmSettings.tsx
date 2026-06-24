"use client";

// LLM プロバイダ複数登録・面別 provider/model 設定（ADR-058・frontend-component-pattern）。
// provider（鍵あり・複数）と 4 面（chat/nightly/dossier/tagger）の割当を /settings から編集する。
// provider/faces を一括取得し、変更のたび reload で両方を取り直す（codex は鍵なし組み込みで
// provider 一覧には出ず、面のセレクトで「codex」を選ぶ＝provider_id=0）。api_key は GET では
// マスク済みで来る・更新は空送信で据え置き（write-only）。秘密は backend のみ（ADR-005）。

import { Card } from "@/components/ui/Card";
import { inputCls, labelCls } from "@/components/ui/Field";
import { StatusBlock } from "@/components/ui/StatusBlock";
import {
  type FaceConfig,
  type Provider,
  createProvider,
  deleteProvider,
  getFaces,
  getProviders,
  testProvider,
  updateFace,
  updateProvider,
} from "@/lib/api";
import { useCallback, useEffect, useState } from "react";

const FACE_LABELS: Record<string, string> = {
  chat: "チャット（軸2 相談）",
  nightly: "夜間AI（軸1 分析）",
  dossier: "ドシエ要約",
  tagger: "タグ付け（テーマ/極性）",
};

const btnCls =
  "rounded-md border border-hairline bg-surface-2 px-3 py-1.5 text-[13px] font-medium hover:bg-surface-3 disabled:opacity-50";

function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** provider 1 行の編集（保存＝write-only・削除＝使用中は 409・疎通テスト）。 */
function ProviderRow({ provider, onChanged }: { provider: Provider; onChanged: () => void }) {
  const [name, setName] = useState(provider.name);
  const [baseUrl, setBaseUrl] = useState(provider.base_url);
  const [defaultModel, setDefaultModel] = useState(provider.default_model);
  const [apiKey, setApiKey] = useState(""); // 空＝据え置き（write-only）
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  async function onSave() {
    setBusy(true);
    setNote(null);
    try {
      await updateProvider(provider.id, {
        name,
        base_url: baseUrl,
        default_model: defaultModel,
        api_key: apiKey, // 空文字は backend が据え置きにする
      });
      setApiKey("");
      setNote("保存したのだ ✅");
      onChanged();
    } catch (e) {
      setNote(errText(e));
    } finally {
      setBusy(false);
    }
  }

  async function onDelete() {
    if (!window.confirm(`provider「${provider.name}」を削除していい？`)) return;
    setBusy(true);
    setNote(null);
    try {
      await deleteProvider(provider.id);
      onChanged();
    } catch (e) {
      // 面が使用中だと 409。message をそのまま出す。
      setNote(errText(e));
      setBusy(false);
    }
  }

  async function onTest() {
    setBusy(true);
    setNote(null);
    try {
      const r = await testProvider(provider.id);
      setNote(r.ok ? `疎通OK ✅ ${r.detail}` : `疎通NG ⚠ ${r.detail}`);
    } catch (e) {
      setNote(errText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-md border border-hairline bg-surface-2 p-2.5">
      <div className="grid gap-2 sm:grid-cols-2">
        <div>
          <span className={labelCls}>表示名</span>
          <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div>
          <span className={labelCls}>base_url（OpenAI 互換 /v1）</span>
          <input
            className={inputCls}
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
          />
        </div>
        <div>
          <span className={labelCls}>既定 model（面で空のとき使う・任意）</span>
          <input
            className={inputCls}
            value={defaultModel}
            onChange={(e) => setDefaultModel(e.target.value)}
          />
        </div>
        <div>
          <span className={labelCls}>
            API キー（現在: {provider.has_api_key ? provider.api_key_masked : "未設定"}
            ・変更時のみ入力）
          </span>
          <input
            className={inputCls}
            type="password"
            placeholder="変更しないなら空のまま"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
          />
        </div>
      </div>
      <div className="mt-2 flex items-center gap-2">
        <button type="button" className={btnCls} onClick={onSave} disabled={busy}>
          {busy ? "処理中…" : "保存"}
        </button>
        <button type="button" className={btnCls} onClick={onTest} disabled={busy}>
          疎通テスト
        </button>
        <button type="button" className={`${btnCls} text-down`} onClick={onDelete} disabled={busy}>
          削除
        </button>
        {note && <span className="text-[12px] text-ink-muted">{note}</span>}
      </div>
    </div>
  );
}

/** provider 追加フォーム。 */
function AddProviderForm({ onChanged }: { onChanged: () => void }) {
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [defaultModel, setDefaultModel] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  async function onAdd() {
    setBusy(true);
    setNote(null);
    try {
      await createProvider({
        name,
        base_url: baseUrl,
        api_key: apiKey,
        default_model: defaultModel,
      });
      setName("");
      setBaseUrl("");
      setApiKey("");
      setDefaultModel("");
      onChanged();
    } catch (e) {
      setNote(errText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-md border border-hairline border-dashed p-2.5">
      <div className="mb-2 text-[12px] text-ink-muted">
        provider を追加（例: OpenRouter / OpenAI 直 / ローカル LLM / Sakana。base_url を OpenAI 互換
        エンドポイントに向ける）。
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        <div>
          <span className={labelCls}>表示名</span>
          <input
            className={inputCls}
            placeholder="OpenRouter"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        <div>
          <span className={labelCls}>base_url</span>
          <input
            className={inputCls}
            placeholder="https://openrouter.ai/api/v1"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
          />
        </div>
        <div>
          <span className={labelCls}>既定 model（任意）</span>
          <input
            className={inputCls}
            placeholder="anthropic/claude-opus-4-8"
            value={defaultModel}
            onChange={(e) => setDefaultModel(e.target.value)}
          />
        </div>
        <div>
          <span className={labelCls}>API キー（ローカル LLM は空可）</span>
          <input
            className={inputCls}
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
          />
        </div>
      </div>
      <div className="mt-2 flex items-center gap-2">
        <button
          type="button"
          className={btnCls}
          onClick={onAdd}
          disabled={busy || !name.trim() || !baseUrl.trim()}
        >
          {busy ? "追加中…" : "追加"}
        </button>
        {note && <span className="text-[12px] text-down">{note}</span>}
      </div>
    </div>
  );
}

/** 面 1 行の割当（provider セレクト＋model 自由入力＋保存）。 */
function FaceRow({
  face,
  providers,
  onChanged,
}: {
  face: FaceConfig;
  providers: Provider[];
  onChanged: () => void;
}) {
  // provider セレクト値: "" = 未設定(null) / "0" = codex / "<id>" = provider。
  const [sel, setSel] = useState<string>(face.provider_id === null ? "" : String(face.provider_id));
  const [model, setModel] = useState(face.model);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  async function onSave() {
    setBusy(true);
    setNote(null);
    try {
      const providerId = sel === "" ? null : Number(sel);
      await updateFace(face.face, { provider_id: providerId, model });
      setNote("保存したのだ ✅");
      onChanged();
    } catch (e) {
      setNote(errText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-md border border-hairline bg-surface-2 p-2.5">
      <div className="mb-1.5 flex items-center justify-between">
        <span className="font-medium text-[13px]">{FACE_LABELS[face.face] ?? face.face}</span>
        <span className={face.configured ? "text-[11px] text-up" : "text-[11px] text-down"}>
          {face.configured ? "● 設定済み" : "○ 未設定（この面は動かない）"}
        </span>
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        <div>
          <span className={labelCls}>provider</span>
          <select className={inputCls} value={sel} onChange={(e) => setSel(e.target.value)}>
            <option value="">（未設定）</option>
            <option value="0">codex（鍵なし・ChatGPT サブスク）</option>
            {providers.map((p) => (
              <option key={p.id} value={String(p.id)}>
                {p.name}
              </option>
            ))}
          </select>
        </div>
        <div>
          <span className={labelCls}>model（空なら provider 既定 / codex 既定）</span>
          <input
            className={inputCls}
            placeholder="gpt-5.5 / claude-opus-4-8 / fugu-ultra …"
            value={model}
            onChange={(e) => setModel(e.target.value)}
          />
        </div>
      </div>
      <div className="mt-2 flex items-center gap-2">
        <button type="button" className={btnCls} onClick={onSave} disabled={busy}>
          {busy ? "保存中…" : "保存"}
        </button>
        {note && <span className="text-[12px] text-ink-muted">{note}</span>}
      </div>
    </div>
  );
}

export function LlmSettings() {
  const [providers, setProviders] = useState<Provider[] | null>(null);
  const [faces, setFaces] = useState<FaceConfig[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [p, f] = await Promise.all([getProviders(), getFaces()]);
      setProviders(p);
      setFaces(f);
    } catch (e) {
      setError(errText(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  return (
    <>
      <Card title="LLM プロバイダ">
        <p className="mb-2 text-[12px] text-ink-muted">
          OpenAI 互換 API を複数登録できる（OpenRouter / OpenAI 直 / ローカル LLM / Sakana 等）。
          キーは backend の DB に保存する（v1 は平文・LAN 内前提＝ADR-058）。GET では常にマスクして
          返す。
        </p>
        <StatusBlock loading={loading} error={error}>
          {providers && (
            <div className="grid gap-2">
              {providers.length === 0 && (
                <p className="text-[12px] text-ink-subtle">
                  まだ provider が無いのだ。下のフォームから追加するのだ（codex は鍵なしで最初から
                  使えるが、鍵あり provider は登録が要るのだ）。
                </p>
              )}
              {providers.map((p) => (
                <ProviderRow key={p.id} provider={p} onChanged={reload} />
              ))}
              <AddProviderForm onChanged={reload} />
            </div>
          )}
        </StatusBlock>
      </Card>

      <Card title="面別 LLM 割当">
        <p className="mb-2 text-[12px] text-ink-muted">
          用途（面）ごとに provider と model を割り当てる。例: チャットは Claude Opus 4.8、夜間AI は
          codex、タグ付けは安い高速モデル、のように使い分けられる（ADR-058）。
        </p>
        <StatusBlock loading={loading} error={error}>
          {faces && providers && (
            <div className="grid gap-2">
              {faces.map((f) => (
                <FaceRow key={f.face} face={f} providers={providers} onChanged={reload} />
              ))}
            </div>
          )}
        </StatusBlock>
      </Card>
    </>
  );
}
