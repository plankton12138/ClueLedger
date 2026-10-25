import { useEffect, useState } from "react";
import { overviewRequest, type Page } from "../../api/storyOverview";
import { errorMessage } from "../../api/errors";

/** 每次读取绑定完整查询和请求代数；旧响应不能切回当前选择。 */
export function useOverviewResource<T>(id: string, path: string | null, params: Record<string, string | number | null | undefined>, refresh = 0) {
  const key = JSON.stringify([id, path, params, refresh]);
  const [result, setResult] = useState<{ key: string; data?: T; error?: string }>({ key: "" });
  useEffect(() => {
    if (path === null) return;
    const controller = new AbortController();
    void overviewRequest<T>(id, path, params, { signal: controller.signal })
      .then(data => { if (!controller.signal.aborted) setResult({ key, data }); })
      .catch(error => { if (!controller.signal.aborted) setResult({ key, error: errorMessage(error, "故事总览") }); });
    return () => controller.abort();
  }, [key]);
  return result.key === key ? { ...result, loading: false } : { key, loading: path !== null, data: undefined, error: undefined };
}

export function mergePage<T>(previous: Page<T> | undefined, next: Page<T>): Page<T> {
  return { ...next, items: [...(previous?.items ?? []), ...next.items] };
}
