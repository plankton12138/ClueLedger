/**
 * 登录会话（v1.4 P1）。
 *
 * server 模式下 `init()` 是工作区挂载的前置条件：未登录时不初始化作品与模型，
 * 避免把未授权请求发出的 401 当作页面错误。local 模式由 `/api/auth/me` 直接
 * 返回已认证，不需要登录页。
 */

import { create } from "zustand";
import * as authApi from "../api/auth";
import type { AuthMe, AuthUser } from "../api/auth";
import { setCsrfToken, setUnauthorizedHandler } from "../api/http";

type SessionStatus = "loading" | "anonymous" | "authenticated";
type SessionMode = "local" | "server";

interface SessionState {
  status: SessionStatus;
  mode: SessionMode;
  user: AuthUser | null;
  csrfToken: string | null;
  busy: boolean;
  error: string | null;
  init: () => Promise<void>;
  refresh: () => Promise<void>;
  login: (email: string, password: string) => Promise<boolean>;
  register: (email: string, password: string) => Promise<boolean>;
  resendVerification: (email: string) => Promise<boolean>;
  forgotPassword: (email: string) => Promise<boolean>;
  logout: () => Promise<void>;
  clearError: () => void;
  expire: () => void;
}

function resetPage(): void {
  // 模块级 store 会跨账号保留；退出或失效后整页重载，避免上一个账号的作品与草稿状态
  // 短暂出现在同一标签页。未提交草稿仍在 localStorage，不随重载丢失。
  if (typeof window !== "undefined") {
    window.localStorage.removeItem("hastings.billing.pendingOrder");
    window.location.reload();
  }
}

function applyMe(me: AuthMe): Partial<SessionState> {
  setCsrfToken(me.csrf_token);
  return {
    mode: me.mode,
    status: me.mode === "local" || me.authenticated ? "authenticated" : "anonymous",
    user: me.user,
    csrfToken: me.csrf_token,
    error: null,
  };
}

async function messageOf(reason: unknown): Promise<string> {
  if (reason instanceof authApi.AuthApiError) return reason.message;
  if (reason instanceof Error && reason.message) return reason.message;
  return "无法连接服务器，请稍后重试";
}

export const useSessionStore = create<SessionState>((set, get) => ({
  status: "loading",
  mode: "server",
  user: null,
  csrfToken: null,
  busy: false,
  error: null,

  async init() {
    set({ status: "loading", error: null });
    try {
      const me = await authApi.fetchMe();
      set(applyMe(me));
    } catch (reason) {
      set({
        mode: "server",
        status: "anonymous",
        user: null,
        csrfToken: null,
        error: await messageOf(reason),
      });
    }
  },

  async refresh() {
    try {
      const me = await authApi.fetchMe();
      set(applyMe(me));
    } catch (reason) {
      set({ error: await messageOf(reason) });
    }
  },

  async login(email, password) {
    set({ busy: true, error: null });
    try {
      const result = await authApi.login(email, password);
      setCsrfToken(result.csrf_token);
      set({
        mode: "server",
        status: "authenticated",
        user: result.user,
        csrfToken: result.csrf_token,
        busy: false,
        error: null,
      });
      return true;
    } catch (reason) {
      set({ busy: false, error: await messageOf(reason) });
      return false;
    }
  },

  async register(email, password) {
    set({ busy: true, error: null });
    try {
      await authApi.register(email, password);
      set({ busy: false });
      return true;
    } catch (reason) {
      set({ busy: false, error: await messageOf(reason) });
      return false;
    }
  },

  async resendVerification(email) {
    set({ busy: true, error: null });
    try {
      await authApi.resendVerification(email);
      set({ busy: false });
      return true;
    } catch (reason) {
      set({ busy: false, error: await messageOf(reason) });
      return false;
    }
  },

  async forgotPassword(email) {
    set({ busy: true, error: null });
    try {
      await authApi.forgotPassword(email);
      set({ busy: false });
      return true;
    } catch (reason) {
      set({ busy: false, error: await messageOf(reason) });
      return false;
    }
  },

  async logout() {
    set({ busy: true, error: null });
    try {
      await authApi.logout();
      setCsrfToken(null);
      set({
        status: "anonymous",
        user: null,
        csrfToken: null,
        busy: false,
        error: null,
      });
      resetPage();
    } catch (reason) {
      // 退出失败时保留会话：否则浏览器看起来已退出，服务器会话却仍然有效。
      set({ busy: false, error: await messageOf(reason) });
    }
  },

  clearError() {
    set({ error: null });
  },

  expire() {
    if (get().status === "authenticated" && get().mode === "server") {
      setCsrfToken(null);
      set({ status: "anonymous", user: null, csrfToken: null });
      resetPage();
    }
  },
}));

setUnauthorizedHandler(() => {
  useSessionStore.getState().expire();
});
