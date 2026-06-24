// LLM プロバイダのプリセットカタログ（ADR-058・/settings の「LLM プロバイダ」）。
// 主要 provider をあらかじめ並べ、開いて API キーを入れる UI のための frontend 定数。
// backend は汎用 llm_providers テーブルのまま（プリセットは frontend だけの概念）。
// label を llm_providers.name に焼くことで、登録済み行とプリセット節を name で対応づける。
// model は provider ではなく「面」で指定する（modelHint は面の model 入力の placeholder 例）。

export type LlmPreset = {
  key: string; // 安定キー（React key・照合用）
  label: string; // = llm_providers.name（登録行との対応づけキー・表示名）
  baseUrl: string; // base_url 既定（編集可・prefill）
  modelHint: string; // 面の model 入力の placeholder 例
  keyRequired: boolean; // false=ローカル LLM（空キー可）
};

// Claude は Anthropic 直の OpenAI 互換エンドポイント（sk-ant キー・確定3）。OpenRouter は別プリセット。
export const LLM_PRESETS: LlmPreset[] = [
  {
    key: "openai",
    label: "OpenAI",
    baseUrl: "https://api.openai.com/v1",
    modelHint: "gpt-5.5",
    keyRequired: true,
  },
  {
    key: "claude",
    label: "Claude (Anthropic)",
    baseUrl: "https://api.anthropic.com/v1",
    modelHint: "claude-opus-4-8",
    keyRequired: true,
  },
  {
    key: "sakana",
    label: "Sakana",
    baseUrl: "https://api.sakana.ai/v1",
    modelHint: "fugu-ultra",
    keyRequired: true,
  },
  {
    key: "openrouter",
    label: "OpenRouter",
    baseUrl: "https://openrouter.ai/api/v1",
    modelHint: "anthropic/claude-opus-4-8",
    keyRequired: true,
  },
  {
    key: "localllm",
    label: "ローカル LLM",
    baseUrl: "http://localhost:11434/v1",
    modelHint: "qwen3.5:9b",
    keyRequired: false,
  },
];

/** プリセットの label 集合（カスタム判定＝この集合に無い name はカスタム扱い）。 */
export const PRESET_LABELS: ReadonlySet<string> = new Set(LLM_PRESETS.map((p) => p.label));

/** provider 名（= llm_providers.name）からプリセットを引く（無ければ undefined＝カスタム）。 */
export function presetByLabel(name: string | null | undefined): LlmPreset | undefined {
  if (!name) return undefined;
  return LLM_PRESETS.find((p) => p.label === name);
}
