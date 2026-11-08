/** 网站账单客户端：套餐、下单、订单状态、权益与退款申请。 */

import { apiFetch } from "./http";

export interface BillingPlan {
  code: string;
  name: string;
  price_cents: number;
  price: string;
  days: number;
  daily_points: number;
  total_points: number;
  description: string;
}

export interface BillingEntitlement {
  active: boolean;
  plan: string | null;
  expires_at: string | null;
  daily_points: number;
  daily_used: number;
  daily_reserved: number;
  daily_remaining: number | null;
  daily_percent: number | null;
  total_points: number;
  total_used: number;
  total_reserved: number;
  total_remaining: number | null;
  total_percent: number | null;
  window_resets_at: string | null;
  now?: string;
}

export interface BillingOrder {
  order_no: string;
  plan_code: string;
  plan_name: string;
  amount_cents: number;
  amount: string;
  status: "PENDING" | "PAID" | "CLOSED" | "REFUNDED";
  created_at: string;
  paid_at: string;
  closed_at: string;
  payment_url?: string;
  gateway_code?: string;
  gateway_trade_status?: string;
}

export class BillingApiError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "BillingApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(`/api/billing${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      code?: string;
      message?: string;
    } | null;
    throw new BillingApiError(
      body?.code ?? `HTTP_${response.status}`,
      body?.message ?? `请求没有成功（HTTP ${response.status}）`,
      response.status,
    );
  }
  return (await response.json()) as T;
}

export async function fetchPlans(): Promise<BillingPlan[]> {
  const body = await request<{ plans: BillingPlan[] }>("/plans");
  return body.plans;
}

export function fetchEntitlement(): Promise<BillingEntitlement> {
  return request<BillingEntitlement>("/entitlement");
}

export function createOrder(planCode: string, requestId: string): Promise<BillingOrder> {
  return request<BillingOrder>("/orders", {
    method: "POST",
    body: JSON.stringify({ plan_code: planCode, request_id: requestId }),
  });
}

export function fetchOrder(orderNo: string): Promise<BillingOrder> {
  return request<BillingOrder>(`/orders/${encodeURIComponent(orderNo)}`);
}

export function queryOrder(orderNo: string): Promise<BillingOrder> {
  return request<BillingOrder>(`/orders/${encodeURIComponent(orderNo)}/query`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export function requestRefund(
  orderNo: string,
  reason: string,
  requestId: string,
): Promise<{ out_request_no: string; status: string }> {
  return request(`/orders/${encodeURIComponent(orderNo)}/refund-request`, {
    method: "POST",
    body: JSON.stringify({ reason, request_id: requestId }),
  });
}
