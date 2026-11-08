/** 订阅对话框：套餐、下单、支付状态与退款申请。 */

import { useEffect, useState } from "react";
import SubscriptionPay from "./SubscriptionPay";
import { useBillingStore } from "../../state/billing";
import type { BillingPlan } from "../../api/billing";
import { AppDialog } from "../ui/AppDialog";
import { Button } from "../ui/Button";

const STATUS_LABELS: Record<string, string> = {
  PENDING: "等待支付确认",
  PAID: "已支付",
  CLOSED: "已关闭",
  REFUNDED: "已退款",
};

function money(value: string): string {
  return `¥${value}`;
}

function points(plan: { daily_points: number; total_points: number }): string {
  if (plan.daily_points > 0) return `每日 ${plan.daily_points} 点`;
  return `共 ${plan.total_points} 点`;
}

function formatTime(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN");
}

/** 当前套餐不能重复购买，只允许往更高价位升级。 */
function planAction(plan: BillingPlan, current: BillingPlan | null): { disabled: boolean; label: string } {
  if (!current) return { disabled: false, label: "用支付宝购买" };
  if (plan.code === current.code) return { disabled: true, label: "当前套餐" };
  if (plan.price_cents <= current.price_cents) return { disabled: true, label: "已包含在当前套餐" };
  return { disabled: false, label: "升级订阅" };
}

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export default function SubscriptionDialog({ open, onOpenChange }: Props) {
  const plans = useBillingStore((state) => state.plans);
  const entitlement = useBillingStore((state) => state.entitlement);
  const order = useBillingStore((state) => state.order);
  const loading = useBillingStore((state) => state.loading);
  const busy = useBillingStore((state) => state.busy);
  const error = useBillingStore((state) => state.error);
  const refundMessage = useBillingStore((state) => state.refundMessage);
  const init = useBillingStore((state) => state.init);
  const buy = useBillingStore((state) => state.buy);
  const requestRefund = useBillingStore((state) => state.requestRefund);
  const [paymentUrl, setPaymentUrl] = useState("");
  const [cashierOpened, setCashierOpened] = useState(false);
  const [reason, setReason] = useState("");

  useEffect(() => {
    if (open) void init();
  }, [open, init]);

  useEffect(() => {
    if (order && order.status !== "PENDING") {
      setPaymentUrl("");
      setCashierOpened(false);
    }
  }, [order]);

  const currentPlan = entitlement?.active
    ? plans.find((plan) => plan.code === entitlement.plan) ?? null
    : null;
  const activePlan = entitlement?.plan ? currentPlan?.name ?? entitlement.plan : null;
  const hasDaily = (entitlement?.daily_points ?? 0) > 0;
  const usedPercent = entitlement?.active
    ? Math.min(100, hasDaily ? entitlement.daily_percent ?? 0 : entitlement.total_percent ?? 0)
    : 0;
  const exhausted = entitlement?.active
    ? hasDaily
      ? entitlement.daily_remaining === 0
      : entitlement.total_remaining === 0
    : false;

  async function purchase(planCode: string) {
    const created = await buy(planCode);
    if (!created?.payment_url) return;
    setPaymentUrl(created.payment_url);
    // 下单成功即自动打开发起收银台；被浏览器拦截时保留手动链接兜底。
    const opened = window.open(created.payment_url, "_blank");
    if (opened) opened.opener = null;
    setCashierOpened(opened !== null);
  }

  return (
    <>
      <AppDialog open={open} onOpenChange={onOpenChange} title="订阅">
        <div className="billing-current" aria-live="polite">
          {entitlement?.active ? (
            <>
              <div className="billing-current-head">
                <strong>{activePlan}</strong>
                <span>有效期至 {formatTime(entitlement.expires_at)}</span>
              </div>
              <div className="billing-meter" aria-label="额度使用进度">
                <span style={{ width: `${usedPercent}%` }} data-low={usedPercent >= 80 ? "true" : "false"} />
              </div>
              <p className="ui-field-hint">
                {hasDaily
                  ? `今日剩余 ${entitlement.daily_remaining ?? 0} 点，额度下次恢复：${formatTime(entitlement.window_resets_at)}`
                  : `本期剩余 ${entitlement.total_remaining ?? 0} 点，不设每日上限`}
              </p>
              {usedPercent >= 80 && !exhausted && (
                <p className="billing-low">额度接近用完；用完后阅读与编辑仍可用，模型请求需要等待恢复或改用自带连接。</p>
              )}
              {exhausted && (
                <p className="billing-low">
                  额度已用完。可以等待{formatTime(hasDaily ? entitlement.window_resets_at : entitlement.expires_at)}恢复，
                  或在模型设置里改用自带连接；阅读与编辑不受影响。
                </p>
              )}
            </>
          ) : (
            <p className="ui-field-hint">
              {loading ? "正在读取订阅状态…" : "尚未订阅。购买后即可在模型设置里选择「线簿订阅」。"}
            </p>
          )}
        </div>

        <div className="billing-plans" aria-busy={loading || busy}>
          {plans.map((plan) => {
            const action = planAction(plan, currentPlan);
            return (
              <article className="billing-plan" key={plan.code}>
                <div className="billing-plan-head">
                  <h3>{plan.name}</h3>
                  <span>{money(plan.price)}</span>
                </div>
                <p className="billing-plan-meta">
                  {plan.days} 天 · {points(plan)}
                </p>
                <p className="billing-plan-copy">{plan.description}</p>
                <Button
                  variant="primary"
                  size="compact"
                  disabled={busy || action.disabled}
                  onClick={() => void purchase(plan.code)}
                >
                  {action.label}
                </Button>
              </article>
            );
          })}
        </div>

        {order && (
          <section className="billing-order">
            <div className="billing-order-head">
              <strong>{order.plan_name}</strong>
              <span>{STATUS_LABELS[order.status] ?? order.status}</span>
            </div>
            <p className="ui-field-hint">
              订单号 {order.order_no} · {money(order.amount)}
            </p>
            {order.status === "PAID" && (
              <div className="billing-refund">
                <label htmlFor="billing-refund-reason">申请退款</label>
                <div className="billing-refund-row">
                  <input
                    id="billing-refund-reason"
                    className="ui-input"
                    value={reason}
                    maxLength={500}
                    placeholder="说明退款原因"
                    onChange={(event) => setReason(event.target.value)}
                  />
                  <Button
                    size="compact"
                    disabled={busy || !reason.trim()}
                    onClick={() => void requestRefund(order.order_no, reason.trim())}
                  >
                    提交申请
                  </Button>
                </div>
              </div>
            )}
          </section>
        )}

        {paymentUrl && order?.status === "PENDING" && (
          cashierOpened ? (
            <p className="ui-field-hint">已在新窗口打开支付宝收银台，请在新窗口中完成付款。</p>
          ) : (
            <SubscriptionPay url={paymentUrl} />
          )
        )}

        {refundMessage && <p className="ui-field-hint">{refundMessage}</p>}
        {error && <p className="auth-error" role="alert">{error}</p>}
        <p className="ui-field-hint">
          支付结果以支付宝异步通知或服务端主动查单为准；关闭页面不影响已付款订单生效。
        </p>
      </AppDialog>
    </>
  );
}
