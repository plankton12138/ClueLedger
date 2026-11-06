/** 账号接口客户端：响应形状与 `hastings/accounts/api.py` 对齐。 */

import { apiFetch } from "./http";

export interface AuthUser {
  user_id: string;
  email: string;
  verified: boolean;
}

export interface AuthMe {
  mode: "local" | "server";
  authenticated: boolean;
  user: AuthUser | null;
  csrf_token: string | null;
}

export interface LoginResult {
  user: AuthUser;
  csrf_token: string;
}

export class AuthApiError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "AuthApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(`/api/auth${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      code?: string;
      message?: string;
    } | null;
    throw new AuthApiError(
      body?.code ?? `HTTP_${response.status}`,
      body?.message ?? `请求没有成功（HTTP ${response.status}）`,
      response.status,
    );
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function fetchMe(): Promise<AuthMe> {
  return request<AuthMe>("/me");
}

export function login(email: string, password: string): Promise<LoginResult> {
  return request<LoginResult>("/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export function register(email: string, password: string): Promise<{ status: "accepted" }> {
  return request("/register", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export function resendVerification(email: string): Promise<{ status: "accepted" }> {
  return request("/resend-verification", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

export function logout(): Promise<void> {
  return request<void>("/logout", { method: "POST" });
}

export function forgotPassword(email: string): Promise<{ status: "accepted" }> {
  return request("/forgot-password", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

export function resetPassword(
  email: string,
  code: string,
  newPassword: string,
): Promise<{ reset: boolean }> {
  return request("/reset-password", {
    method: "POST",
    body: JSON.stringify({ email, code, new_password: newPassword }),
  });
}

export function verifyEmail(email: string, code: string): Promise<{ verified: boolean; email: string }> {
  return request("/verify-email", {
    method: "POST",
    body: JSON.stringify({ email, code }),
  });
}
