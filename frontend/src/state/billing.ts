/**
 * 订阅状态（v1.4 P3）。
 *
 * 只保存套餐、权益与最近一笔订单号；订单号不是登录凭证，仅用于关闭页面后
 * 回来查询状态。真正的身份仍是 HttpOnly 会话 Cookie。
 */

import { create } from "zustand";
import * as billingApi from "../api/billing";
import type { BillingEntitlement, BillingOrder, BillingPlan } from "../api/billing";

const PENDING_KEY = "hastings.billing.pendingOrder";

function pendingOrderNo(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(PENDING_KEY);
}

function rememberOrder(orderNo: string, keep: boolean): void {
  if (typeof window === "undefined") return;
  if (keep) window.localStorage.setItem(PENDING_KEY, orderNo);
  else window.localStorage.removeItem(PENDING_KEY);
}

function describe(reason: unknown): string {
  if (reason instanceof billingApi.BillingApiError) return reason.message;
  if (reason instanceof Error && reason.message) return reason.message;
  return "订阅服务暂时不可用，请稍后重试";
}

interface BillingState {
  plans: BillingPlan[];
  entitlement: BillingEntitlement | null;
  order: BillingOrder | null;
  loading: boolean;
  busy: boolean;
  error: string | null;
  refundMessage: string | null;
  dialogOpen: boolean;
  openDialog: () => void;
  closeDialog: () => void;
  init: () => Promise<void>;
  refresh: () => Promise<void>;
  buy: (planCode: string) => Promise<BillingOrder | null>;
  query: (orderNo: string) => Promise<void>;
  requestRefund: (orderNo: string, reason: string) => Promise<boolean>;
  clearError: () => void;
}

export const useBillingStore = create<BillingState>((set, get) => ({
  plans: [],
  entitlement: null,
  order: null,
  loading: false,
  busy: false,
  error: null,
  refundMessage: null,
  dialogOpen: false,

  openDialog() {
    set({ dialogOpen: true });
  },

  closeDialog() {
    set({ dialogOpen: false });
  },

  async init() {
    set({ loading: true, error: null });
    try {
      const [plans, entitlement] = await Promise.all([
        billingApi.fetchPlans(),
        billingApi.fetchEntitlement(),
      ]);
      set({ plans, entitlement, loading: false });
      const recent = pendingOrderNo();
      if (recent) {
        const order = await billingApi.fetchOrder(recent);
        set({ order });
        if (order.status !== "PENDING") rememberOrder(recent, false);
      }
    } catch (reason) {
      set({ loading: false, error: describe(reason) });
    }
  },

  async refresh() {
    try {
      const entitlement = await billingApi.fetchEntitlement();
      set({ entitlement });
    } catch (reason) {
      set({ error: describe(reason) });
    }
  },

  async buy(planCode) {
    set({ busy: true, error: null, refundMessage: null });
    try {
      const requestId =
        typeof crypto !== "undefined" && "randomUUID" in crypto
          ? crypto.randomUUID()
          : `${Date.now().toString(36)}-${Math.random().toString(16).slice(2)}`;
      const order = await billingApi.createOrder(planCode, requestId);
      set({ order, busy: false });
      rememberOrder(order.order_no, order.status === "PENDING");
      return order;
    } catch (reason) {
      set({ busy: false, error: describe(reason) });
      return null;
    }
  },

  async query(orderNo) {
    set({ busy: true, error: null });
    try {
      const order = await billingApi.queryOrder(orderNo);
      set({ order, busy: false });
      if (order.status !== "PENDING") {
        rememberOrder(orderNo, false);
        await get().refresh();
      }
    } catch (reason) {
      set({ busy: false, error: describe(reason) });
    }
  },

  async requestRefund(orderNo, reason) {
    set({ busy: true, error: null, refundMessage: null });
    try {
      const requestId =
        typeof crypto !== "undefined" && "randomUUID" in crypto
          ? crypto.randomUUID()
          : `${Date.now().toString(36)}`;
      const result = await billingApi.requestRefund(orderNo, reason, requestId);
      set({ busy: false, refundMessage: `退款申请已提交：${result.out_request_no}` });
      return true;
    } catch (error) {
      set({ busy: false, error: describe(error) });
      return false;
    }
  },

  clearError() {
    set({ error: null });
  },
}));
