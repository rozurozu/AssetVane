"use client";

// LLM プロバイダ複数登録・面別 provider/model 設定（ADR-058・frontend-component-pattern）。
// 主要 provider（OpenAI/Claude/Sakana/OpenRouter/ローカル LLM）をプリセットとしてあらかじめ並べ、
// 開いて API キーを入れる形にする（プリセットは frontend カタログ＝llmProviderPresets・backend は
// 汎用 llm_providers のまま）。保存は name=プリセット label で 1 行 upsert（未登録=POST/登録=PUT）。
// model は provider ではなく「面」で指定する（provider カードに model 欄は持たない）。api_key は GET
// ではマスク済み・更新は空送信で据え置き（write-only）。秘密は backend のみ（ADR-005）。

import { Card } from "@/components/ui/Card";
import { inputCls, labelCls } from "@/components/ui/Field";
import { StatusBlock } from "@/components/ui/StatusBlock";
import {
  type EmbeddingConfig,
  type FaceConfig,
  type Provider,
  createProvider,
  deleteProvider,
  getEmbedding,
  getFaces,
  getProviders,
  testEmbedding,
  testProvider,
  updateEmbedding,
  updateFace,
  updateProvider,
} from "@/lib/api";
import { useCallback, useEffect, useState } from "react";
import { LLM_PRESETS, type LlmPreset, PRESET_LABELS, presetByLabel } from "./llmProviderPresets";
import { CUSTOM_ICON, PROVIDER_ICONS } from "./providerIcons";

const FACE_LABELS: Record<string, string> = {
  chat: "チャット（軸2 相談）",
  nightly: "夜間AI（軸1 分析）",
  dossier: "ドシエ要約",
  tagger: "タグ付け（テーマ/極性）",
  triage: "カード審査（知識カードの振り分け）",
  // 自己改善/反証の 3 面（ADR-081/082/086）。未割当だとその面が動かないので /settings で設定できるように。
  reviewer: "経験蒸留（採点結果→知識カード下書き）",
  profiler: "投資家プロファイル蒸留（台帳→傾向メモ）",
  skeptic: "提案の反証（red-team・別 model 推奨）",
};

// reasoning_effort の選択肢（固定 enum・ADR-059）。"" = 既定（openai は送らない）。
const REASONING_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "（既定）" },
  { value: "minimal", label: "minimal" },
  { value: "low", label: "low" },
  { value: "medium", label: "medium" },
  { value: "high", label: "high" },
];

const btnCls =
  "rounded-md border border-hairline bg-surface-2 px-3 py-1.5 text-[13px] font-medium hover:bg-surface-3 disabled:opacity-50";

function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** 鍵あり provider の base_url＋API キー編集ボタン群（プリセット節・カスタム行で共用）。 */
function ProviderFields({
  baseUrl,
  setBaseUrl,
  apiKey,
  setApiKey,
  registered,
  keyHint,
}: {
  baseUrl: string;
  setBaseUrl: (v: string) => void;
  apiKey: string;
  setApiKey: (v: string) => void;
  registered: Provider | undefined;
  keyHint: string;
}) {
  return (
    <div className="grid gap-2 sm:grid-cols-2">
      <div>
        <span className={labelCls}>base_url（OpenAI 互換 /v1）</span>
        <input className={inputCls} value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
      </div>
      <div>
        <span className={labelCls}>
          API キー（現在: {registered?.has_api_key ? registered.api_key_masked : "未設定"}・
          {keyHint}）
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
  );
}

/** プリセット 1 個のアコーディオン節（name=preset.label で登録行を照合し upsert）。 */
function PresetSection({
  preset,
  registered,
  onChanged,
}: {
  preset: LlmPreset;
  registered: Provider | undefined;
  onChanged: () => void;
}) {
  const [baseUrl, setBaseUrl] = useState(registered?.base_url ?? preset.baseUrl);
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  async function onSave() {
    setBusy(true);
    setNote(null);
    try {
      if (registered) {
        // api_key は空なら backend が据え置き（write-only）。
        await updateProvider(registered.id, { base_url: baseUrl, api_key: apiKey });
      } else {
        await createProvider({ name: preset.label, base_url: baseUrl, api_key: apiKey });
      }
      setApiKey("");
      setNote("保存したのだ ✅");
      onChanged();
    } catch (e) {
      setNote(errText(e));
    } finally {
      setBusy(false);
    }
  }

  async function onTest() {
    if (!registered) return;
    setBusy(true);
    setNote(null);
    try {
      const r = await testProvider(registered.id);
      setNote(r.ok ? `疎通OK ✅ ${r.detail}` : `疎通NG ⚠ ${r.detail}`);
    } catch (e) {
      setNote(errText(e));
    } finally {
      setBusy(false);
    }
  }

  async function onDelete() {
    if (!registered) return;
    if (!window.confirm(`${preset.label} の設定を削除していい？`)) return;
    setBusy(true);
    setNote(null);
    try {
      await deleteProvider(registered.id);
      onChanged();
    } catch (e) {
      // 面が使用中だと 409。message をそのまま出す。
      setNote(errText(e));
      setBusy(false);
    }
  }

  // 鍵必須プリセットを未登録・キー未入力で保存させない（ローカル LLM は空キー可）。
  const saveDisabled = busy || (preset.keyRequired && !registered && !apiKey.trim());

  return (
    <details className="rounded-md border border-hairline bg-surface-2 [&_summary]:cursor-pointer">
      <summary className="flex list-none items-center justify-between px-2.5 py-2 [&::-webkit-details-marker]:hidden">
        <span className="flex items-center gap-2">
          <span className="text-ink-muted">{PROVIDER_ICONS[preset.key]}</span>
          <span className="font-medium text-[13px]">{preset.label}</span>
        </span>
        <span className={registered ? "text-[11px] text-up" : "text-[11px] text-ink-subtle"}>
          {registered ? "● 設定済み" : "○ 未設定"}
        </span>
      </summary>
      <div className="border-hairline border-t p-2.5">
        <ProviderFields
          baseUrl={baseUrl}
          setBaseUrl={setBaseUrl}
          apiKey={apiKey}
          setApiKey={setApiKey}
          registered={registered}
          keyHint={preset.keyRequired ? "変更時のみ入力" : "ローカルは空可"}
        />
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <button type="button" className={btnCls} onClick={onSave} disabled={saveDisabled}>
            {busy ? "処理中…" : "保存"}
          </button>
          {registered && (
            <button type="button" className={btnCls} onClick={onTest} disabled={busy}>
              疎通テスト
            </button>
          )}
          {registered && (
            <button
              type="button"
              className={`${btnCls} text-down`}
              onClick={onDelete}
              disabled={busy}
            >
              削除
            </button>
          )}
          {note && <span className="text-[12px] text-ink-muted">{note}</span>}
        </div>
      </div>
    </details>
  );
}

/** カスタム（プリセット外）provider 1 行の編集＋削除。 */
function CustomRow({ provider, onChanged }: { provider: Provider; onChanged: () => void }) {
  const [baseUrl, setBaseUrl] = useState(provider.base_url);
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  async function onSave() {
    setBusy(true);
    setNote(null);
    try {
      await updateProvider(provider.id, { base_url: baseUrl, api_key: apiKey });
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
      setNote(errText(e));
      setBusy(false);
    }
  }

  return (
    <div className="rounded-md border border-hairline bg-surface-2 p-2.5">
      <div className="mb-1.5 font-medium text-[13px]">{provider.name}</div>
      <ProviderFields
        baseUrl={baseUrl}
        setBaseUrl={setBaseUrl}
        apiKey={apiKey}
        setApiKey={setApiKey}
        registered={provider}
        keyHint="変更時のみ入力"
      />
      <div className="mt-2 flex items-center gap-2">
        <button type="button" className={btnCls} onClick={onSave} disabled={busy}>
          {busy ? "処理中…" : "保存"}
        </button>
        <button type="button" className={`${btnCls} text-down`} onClick={onDelete} disabled={busy}>
          削除
        </button>
        {note && <span className="text-[12px] text-ink-muted">{note}</span>}
      </div>
    </div>
  );
}

/** カスタム provider の追加フォーム（プリセットに無い任意の OpenAI 互換エンドポイント用）。 */
function AddCustomForm({ onChanged }: { onChanged: () => void }) {
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  async function onAdd() {
    setBusy(true);
    setNote(null);
    try {
      await createProvider({ name, base_url: baseUrl, api_key: apiKey });
      setName("");
      setBaseUrl("");
      setApiKey("");
      onChanged();
    } catch (e) {
      setNote(errText(e));
    } finally {
      setBusy(false);
    }
  }

  const dup = PRESET_LABELS.has(name.trim());

  return (
    <div className="rounded-md border border-hairline border-dashed p-2.5">
      <div className="mb-2 text-[12px] text-ink-muted">
        プリセットに無い OpenAI 互換 provider を追加（表示名は上のプリセット名と被らないように）。
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        <div>
          <span className={labelCls}>表示名</span>
          <input
            className={inputCls}
            placeholder="My Provider"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        <div>
          <span className={labelCls}>base_url</span>
          <input
            className={inputCls}
            placeholder="https://example.com/v1"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
          />
        </div>
        <div>
          <span className={labelCls}>API キー（空可）</span>
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
          disabled={busy || !name.trim() || !baseUrl.trim() || dup}
        >
          {busy ? "追加中…" : "追加"}
        </button>
        {dup && <span className="text-[12px] text-down">プリセット名と重複しているのだ。</span>}
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
  // provider セレクト値: "" = 未設定(null) / "<id>" = provider。
  const [sel, setSel] = useState<string>(face.provider_id === null ? "" : String(face.provider_id));
  const [model, setModel] = useState(face.model);
  const [reasoning, setReasoning] = useState(face.reasoning_effort);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  async function onSave() {
    setBusy(true);
    setNote(null);
    try {
      const providerId = sel === "" ? null : Number(sel);
      await updateFace(face.face, { provider_id: providerId, model, reasoning_effort: reasoning });
      setNote("保存したのだ ✅");
      onChanged();
    } catch (e) {
      setNote(errText(e));
    } finally {
      setBusy(false);
    }
  }

  // model 入力の placeholder を選択中 provider に応じて出す（プリセット / 汎用）。
  const selProvider = providers.find((p) => String(p.id) === sel);
  const modelPlaceholder =
    presetByLabel(selProvider?.name)?.modelHint ?? "gpt-5.5 / claude-opus-4-8 / fugu-ultra …";

  return (
    <div className="rounded-md border border-hairline bg-surface-2 p-2.5">
      <div className="mb-1.5 flex items-center justify-between">
        <span className="font-medium text-[13px]">{FACE_LABELS[face.face] ?? face.face}</span>
        <span className={face.configured ? "text-[11px] text-up" : "text-[11px] text-down"}>
          {face.configured ? "● 設定済み" : "○ 未設定（この面は動かない）"}
        </span>
      </div>
      <div className="grid gap-2 sm:grid-cols-3">
        <div>
          <span className={labelCls}>provider</span>
          <select className={inputCls} value={sel} onChange={(e) => setSel(e.target.value)}>
            <option value="">（未設定）</option>
            {providers.map((p) => (
              <option key={p.id} value={String(p.id)}>
                {p.name}
              </option>
            ))}
          </select>
        </div>
        <div>
          <span className={labelCls}>model（必須）</span>
          <input
            className={inputCls}
            placeholder={modelPlaceholder}
            value={model}
            onChange={(e) => setModel(e.target.value)}
          />
        </div>
        <div>
          <span className={labelCls}>reasoning effort</span>
          <select
            className={inputCls}
            value={reasoning}
            onChange={(e) => setReasoning(e.target.value)}
          >
            {REASONING_OPTIONS.map((r) => (
              <option key={r.value} value={r.value}>
                {r.label}
              </option>
            ))}
          </select>
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

/** embedding（意味検索）接続カード（base_url/api_key/model/dim・ADR-059）。 */
function EmbeddingCard() {
  const [cfg, setCfg] = useState<EmbeddingConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [dim, setDim] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const c = await getEmbedding();
      setCfg(c);
      setBaseUrl(c.base_url);
      setModel(c.model);
      setDim(c.dim ? String(c.dim) : "");
    } catch (e) {
      setError(errText(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function onSave() {
    setBusy(true);
    setNote(null);
    try {
      const c = await updateEmbedding({
        base_url: baseUrl,
        api_key: apiKey, // 空は backend が据え置き（write-only）
        model,
        dim: dim ? Number(dim) : 0,
      });
      setCfg(c);
      setApiKey("");
      setNote("保存したのだ ✅");
    } catch (e) {
      setNote(errText(e));
    } finally {
      setBusy(false);
    }
  }

  async function onTest() {
    setBusy(true);
    setNote(null);
    try {
      const r = await testEmbedding();
      setNote(r.ok ? `疎通OK ✅ ${r.detail}` : `疎通NG ⚠ ${r.detail}`);
    } catch (e) {
      setNote(errText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Embedding（意味検索）">
      <p className="mb-2 text-[12px] text-ink-muted">
        ニュース意味検索の埋め込み接続（OpenAI 互換 /v1/embeddings）。chat provider
        とは独立に設定する （埋め込み用の別キー・別 model が普通）。3
        キーが揃って初めて有効（ADR-059）。
      </p>
      <StatusBlock loading={loading} error={error}>
        {cfg && (
          <div className="grid gap-2">
            <div className="flex items-center justify-between">
              <span
                className={cfg.configured ? "text-[11px] text-up" : "text-[11px] text-ink-subtle"}
              >
                {cfg.configured ? "● 設定済み（有効）" : "○ 未設定（意味検索は機能オフ）"}
              </span>
            </div>
            <div className="grid gap-2 sm:grid-cols-2">
              <div>
                <span className={labelCls}>base_url（OpenAI 互換 /v1）</span>
                <input
                  className={inputCls}
                  placeholder="https://api.openai.com/v1"
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                />
              </div>
              <div>
                <span className={labelCls}>
                  API キー（現在: {cfg.has_api_key ? cfg.api_key_masked : "未設定"}
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
              <div>
                <span className={labelCls}>model</span>
                <input
                  className={inputCls}
                  placeholder="text-embedding-3-small"
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                />
              </div>
              <div>
                <span className={labelCls}>dim（任意・次元）</span>
                <input
                  className={inputCls}
                  type="number"
                  placeholder="0=未設定"
                  value={dim}
                  onChange={(e) => setDim(e.target.value)}
                />
              </div>
            </div>
            <div className="flex items-center gap-2">
              <button type="button" className={btnCls} onClick={onSave} disabled={busy}>
                {busy ? "処理中…" : "保存"}
              </button>
              <button type="button" className={btnCls} onClick={onTest} disabled={busy}>
                疎通テスト
              </button>
              {note && <span className="text-[12px] text-ink-muted">{note}</span>}
            </div>
          </div>
        )}
      </StatusBlock>
    </Card>
  );
}

export function LlmSettings() {
  const [providers, setProviders] = useState<Provider[] | null>(null);
  const [faces, setFaces] = useState<FaceConfig[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // 初回だけ loading 表示（StatusBlock が子を gate）。保存後の再取得は silent＝子を保ったまま更新し、
  // アコーディオンの開閉や保存メッセージ・入力中のフィールドを失わない。
  const reload = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    setError(null);
    try {
      const [p, f] = await Promise.all([getProviders(), getFaces()]);
      setProviders(p);
      setFaces(f);
    } catch (e) {
      setError(errText(e));
    } finally {
      if (!silent) setLoading(false);
    }
  }, []);

  const refresh = useCallback(() => reload(true), [reload]);

  useEffect(() => {
    reload();
  }, [reload]);

  // プリセット外の登録済み provider（旧 UI 由来や任意エンドポイント）はカスタム節に出す。
  const customProviders = (providers ?? []).filter((p) => !PRESET_LABELS.has(p.name));

  return (
    <>
      <Card title="LLM プロバイダ">
        <p className="mb-2 text-[12px] text-ink-muted">
          主要 provider をあらかじめ並べてある。使うものを開いて API
          キーを入れて保存するのだ。OpenAI 互換 API（OpenAI / Claude / Sakana / OpenRouter /
          ローカル LLM）。キーは backend の DB に保存 （v1 は平文・LAN 内前提＝ADR-058）・GET
          ではマスクして返す。model は下の「面別 LLM 割当」で指定する。
        </p>
        <StatusBlock loading={loading} error={error}>
          {providers && (
            <div className="grid gap-2">
              {LLM_PRESETS.map((preset) => (
                <PresetSection
                  key={preset.key}
                  preset={preset}
                  registered={providers.find((p) => p.name === preset.label)}
                  onChanged={refresh}
                />
              ))}
              {/* プリセットと同様にアコーディオンで畳む（既定は閉じる・カスタム行があれば件数表示）。 */}
              <details className="rounded-md border border-hairline bg-surface-2 [&_summary]:cursor-pointer">
                <summary className="flex list-none items-center justify-between px-2.5 py-2 [&::-webkit-details-marker]:hidden">
                  <span className="flex items-center gap-2">
                    <span className="text-ink-muted">{CUSTOM_ICON}</span>
                    <span className="font-medium text-[13px]">その他（カスタム）</span>
                  </span>
                  <span className="text-[11px] text-ink-subtle">
                    {customProviders.length > 0 ? `● ${customProviders.length} 件` : "○ なし"}
                  </span>
                </summary>
                <div className="grid gap-2 border-hairline border-t p-2.5">
                  {customProviders.map((p) => (
                    <CustomRow key={p.id} provider={p} onChanged={refresh} />
                  ))}
                  <AddCustomForm onChanged={refresh} />
                </div>
              </details>
            </div>
          )}
        </StatusBlock>
      </Card>

      <Card title="面別 LLM 割当">
        <p className="mb-2 text-[12px] text-ink-muted">
          用途（面）ごとに provider と model を割り当てる。例: チャットは Claude Opus 4.8、夜間AI は
          安価な強モデル、タグ付けは安い高速モデル、のように使い分けられる（ADR-058）。model
          はここで指定する。
        </p>
        <StatusBlock loading={loading} error={error}>
          {faces && providers && (
            <div className="grid gap-2">
              {faces.map((f) => (
                <FaceRow key={f.face} face={f} providers={providers} onChanged={refresh} />
              ))}
            </div>
          )}
        </StatusBlock>
      </Card>

      <EmbeddingCard />
    </>
  );
}
