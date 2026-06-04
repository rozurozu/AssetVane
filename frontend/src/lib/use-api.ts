"use client";

// GET 取得の三分岐（loading/error/data）を一本化するフック（frontend-component-pattern）。
// 生 useEffect+useState+then/catch を毎ページ手書きしない。
// fetcher は signal を受けて fetch に渡す（AbortController でキャンセル・二重実行を吸収）。
// deps が変わると再取得する。deps にはプリミティブを渡すこと（fetcher を入れない）。

import { useEffect, useState } from "react";

export type ApiState<T> = {
  data: T | null;
  error: string | null;
  loading: boolean;
};

export function useApi<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  deps: unknown[],
): ApiState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // biome-ignore lint/correctness/useExhaustiveDependencies: deps は呼び出し側が管理する（fetcher は意図的に除外）。
  useEffect(() => {
    let ignore = false;
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);
    setData(null);
    fetcher(ctrl.signal)
      .then((d) => {
        if (!ignore) setData(d);
      })
      .catch((e) => {
        // アンマウント・キャンセル後の state 反映は抑止する。
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
  }, deps);

  return { data, error, loading };
}
