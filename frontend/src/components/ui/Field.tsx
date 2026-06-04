// 共有 UI: フォーム入力スタイルの一本化（frontend-component-pattern）。
// inputCls/labelCls は input・select・textarea で共用するため、コンポーネントではなく
// クラストークンとして公開する（要素の種類が多く、汎用ラッパだと表現力が落ちるため）。
// Field は「label＋入力」の縦並びラッパ（任意利用）。DESIGN.md トークンのみ・生色なし。

import type { ReactNode } from "react";

/** input/select/textarea 共通のフォーム入力クラス（bg-canvas・focus:border-accent）。 */
export const inputCls =
  "w-full rounded-md border border-hairline bg-canvas px-2.5 py-1.5 text-[13px] text-ink outline-none focus:border-accent";

/** ラベル共通クラス。 */
export const labelCls = "block text-[11px] text-ink-muted mb-0.5";

type FieldProps = {
  /** label の htmlFor と入力の id を結ぶ。 */
  htmlFor?: string;
  label: ReactNode;
  children: ReactNode; // <input>/<select>/<textarea>（className={inputCls} を付ける）
};

/** ラベル＋入力の縦並び。入力側に inputCls を付けて子に渡す。 */
export function Field({ htmlFor, label, children }: FieldProps) {
  return (
    <div>
      <label htmlFor={htmlFor} className={labelCls}>
        {label}
      </label>
      {children}
    </div>
  );
}
