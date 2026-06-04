// 共有 UI: 読込/エラー/空 の三状態を畳む（frontend-component-pattern）。
// 各ページで「error && … / data===null && … / 空 && …」の三分岐を手書きしない。
// data が揃ったら children を描画する。状態メッセージのラッパ装飾は className で合わせる
// （ページにより p-4 やカード枠など差があるため）。純表示なので Server のまま。

import type { ReactNode } from "react";

type Props = {
  loading: boolean;
  error: string | null;
  empty?: boolean; // data 取得済みだが空（0 件）か
  className?: string; // 各状態メッセージのラッパに付与（"p-4" 等）
  errorHint?: ReactNode; // エラー時の補足（backend 起動確認の案内など）
  emptyText?: ReactNode; // 空表示の文言
  loadingText?: ReactNode; // 読込中の文言（既定「読み込み中…」）
  children: ReactNode; // 正常時の描画
};

export function StatusBlock({
  loading,
  error,
  empty,
  className = "",
  errorHint,
  emptyText,
  loadingText,
  children,
}: Props) {
  if (error) {
    return (
      <div className={`text-[13px] text-down ${className}`.trim()}>
        ⚠ 取得に失敗: {error}
        {errorHint && <div className="mt-1 text-[12px] text-ink-subtle">{errorHint}</div>}
      </div>
    );
  }
  if (loading) {
    return (
      <div className={`text-[13px] text-ink-subtle ${className}`.trim()}>
        {loadingText ?? "読み込み中…"}
      </div>
    );
  }
  if (empty) {
    return (
      <div className={`text-[13px] text-ink-subtle ${className}`.trim()}>
        {emptyText ?? "データがないのだ。"}
      </div>
    );
  }
  return <>{children}</>;
}
