// 共有 UI プリミティブ: watchlist 星トグルボタン（screens.md #2・一覧から監視追加）。
// 純プリミティブ（frontend-component-pattern (b)）。データ取得も計算もしない。
// active=塗り星（accent）／未=アウトライン星（ink-subtle・hover で accent）。busy 中は disabled。
// トグルの状態管理・mutation は呼び元のページが持つ（active/busy/onClick を受け取るだけ）。

type Props = {
  active: boolean; // watchlist 済みか（塗り星）
  busy: boolean; // 送信中（disabled・淡色）
  onClick: () => void;
};

export function WatchlistStar({ active, busy, onClick }: Props) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      aria-pressed={active}
      aria-label={active ? "watchlist から外す" : "watchlist に追加"}
      className={`-m-1 rounded p-1 leading-none transition-colors disabled:opacity-50 ${
        active ? "text-accent" : "text-ink-subtle hover:text-accent"
      }`}
    >
      <svg
        width="15"
        height="15"
        viewBox="0 0 24 24"
        fill={active ? "currentColor" : "none"}
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <path d="M12 2.5l2.9 5.9 6.5.95-4.7 4.58 1.1 6.47L12 17.9 6.2 20.9l1.1-6.47-4.7-4.58 6.5-.95z" />
      </svg>
    </button>
  );
}
