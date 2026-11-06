/**
 * 统一请求入口（v1.4 P1）。
 *
 * 浏览器只持有 Cookie 会话，不保存 token；写请求自动附带当前会话的 CSRF 令牌。
 * 401 统一交给会话层处理：工作区卸载回登录页，未提交草稿仍在 localStorage。
 */

let csrfToken: string | null = null;
let unauthorizedHandler: (() => void) | null = null;

export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

function isAuthEndpoint(url: string): boolean {
  return url.includes("/api/auth/");
}

function mergeHeaders(init?: HeadersInit): Record<string, string> {
  const merged: Record<string, string> = {};
  if (!init) return merged;
  if (init instanceof Headers) {
    init.forEach((value, key) => {
      merged[key] = value;
    });
    return merged;
  }
  if (Array.isArray(init)) {
    for (const [key, value] of init) merged[key] = value;
    return merged;
  }
  return { ...init };
}

export async function apiFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const method = (init?.method ?? "GET").toUpperCase();
  const headers = mergeHeaders(init?.headers);
  if (csrfToken && method !== "GET" && method !== "HEAD" && method !== "OPTIONS") {
    headers["X-CSRF-Token"] = csrfToken;
  }
  const response = await fetch(input, {
    credentials: "same-origin",
    ...init,
    headers,
  });
  if (response.status === 401) {
    const url = typeof input === "string" ? input : input instanceof URL ? input.pathname : input.url;
    if (!isAuthEndpoint(url)) unauthorizedHandler?.();
  }
  return response;
}
