"""订阅业务：下单、查单、通知、权益、退款与后台恢复。

HTTP 层只负责 Cookie/CSRF 与响应格式；这里的所有方法都以 ``user_id`` 为第一参数，
便于写作进程、支付进程和测试共用同一套判断。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from hastings.relay.alipay import AlipayGateway
from hastings.relay.config import RelaySettings
from hastings.relay.plans import PLANS, Plan, get_plan
from hastings.relay.store import (
    PAID_TRADE_STATUSES,
    Order,
    RefundConflictError,
    RefundRecord,
    RelayStore,
    parse_ts,
    utc_now,
)

logger = logging.getLogger("hastings.relay")

QUERY_INTERVAL_SECONDS = 3.0
RECONCILE_MIN_AGE_SECONDS = 30.0


class BillingError(Exception):
    """可分类的账单失败；对外正文遵守 ``[CODE] 位置：现象。建议：…``。"""

    def __init__(
        self,
        *,
        code: str,
        where: str,
        why: str,
        fix: str,
        status_code: int,
    ) -> None:
        self.code = code
        self.where = where
        self.why = why
        self.fix = fix
        self.status_code = status_code
        super().__init__(self.message)

    @property
    def message(self) -> str:
        return f"[{self.code}] {self.where}：{self.why}。建议：{self.fix}"


@dataclass(frozen=True, slots=True)
class OrderPayment:
    order: Order
    payment_html: str


def amount_text(cents: int) -> str:
    return f"{Decimal(cents) / Decimal(100):.2f}"


def parse_amount_cents(value: str) -> int | None:
    try:
        amount = Decimal(value.strip())
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount <= 0:
        return None
    if amount != amount.quantize(Decimal("0.01")):
        return None
    return int(amount * 100)


def new_order_no() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    return f"CL{stamp}{secrets.token_hex(4).upper()}"


def new_refund_no(out_trade_no: str) -> str:
    return f"RF{out_trade_no[-12:]}{secrets.token_hex(4).upper()}"


def _safe_query_raw(result: dict[str, Any]) -> str:
    keys = (
        "code",
        "msg",
        "sub_code",
        "sub_msg",
        "trade_no",
        "out_trade_no",
        "trade_status",
        "total_amount",
        "buyer_user_id",
        "send_pay_date",
    )
    return json.dumps(
        {key: result.get(key) for key in keys if result.get(key) is not None},
        ensure_ascii=False,
    )


def _safe_notify_raw(params: dict[str, str]) -> str:
    keys = (
        "notify_id",
        "notify_type",
        "trade_no",
        "out_trade_no",
        "trade_status",
        "total_amount",
        "app_id",
        "seller_id",
        "out_biz_no",
        "gmt_refund",
        "refund_fee",
    )
    return json.dumps(
        {key: params.get(key) for key in keys if params.get(key) is not None},
        ensure_ascii=False,
    )


class BillingService:
    def __init__(
        self,
        store: RelayStore,
        gateway: AlipayGateway,
        settings: RelaySettings,
        *,
        now: Callable[[], str] = utc_now,
    ) -> None:
        self.store = store
        self.gateway = gateway
        self.settings = settings
        self._now = now

    # -- 展示 ---------------------------------------------------------------

    def plans(self) -> list[dict[str, object]]:
        return [
            {
                "code": plan.code,
                "name": plan.name,
                "price_cents": plan.price_cents,
                "price": plan.price_text,
                "days": plan.days,
                "daily_points": plan.daily_points,
                "total_points": plan.total_points,
                "description": plan.description,
            }
            for plan in PLANS
        ]

    def order_payload(self, order: Order) -> dict[str, object]:
        plan = get_plan(order.plan)
        return {
            "order_no": order.out_trade_no,
            "plan_code": order.plan,
            "plan_name": plan.name if plan is not None else order.plan,
            "amount_cents": order.amount_cents,
            "amount": amount_text(order.amount_cents),
            "status": order.status,
            "created_at": order.created_at,
            "paid_at": order.paid_at,
            "closed_at": order.closed_at,
        }

    def entitlement(self, user_id: str) -> dict[str, object]:
        summary = self.store.entitlement_summary(user_id, self._now())
        summary["now"] = self._now()
        return summary

    # -- 下单 ---------------------------------------------------------------

    def create_order(self, user_id: str, plan_code: str, request_id: str) -> OrderPayment:
        if not request_id.strip():
            raise BillingError(
                code="BILLING_REQUEST_INVALID",
                where="billing.order",
                why="缺少幂等请求标识",
                fix="刷新订阅页后重新下单",
                status_code=400,
            )
        plan = get_plan(plan_code)
        if plan is None:
            raise BillingError(
                code="BILLING_PLAN_UNKNOWN",
                where="billing.order",
                why="订阅档位不存在",
                fix="刷新订阅页后重新选择档位",
                status_code=400,
            )
        existing = self.store.find_order_by_request(user_id, request_id.strip())
        if existing is not None:
            return self._resume(existing, plan)
        if plan.code == "experience" and self.store.experience_order_exists(user_id):
            raise BillingError(
                code="BILLING_EXPERIENCE_USED",
                where="billing.order",
                why="体验卡每个账号只能购买一次",
                fix="选择标准月卡或长篇月卡",
                status_code=409,
            )
        order_no = new_order_no()
        try:
            order = self.store.create_order(
                out_trade_no=order_no,
                user_id=user_id,
                request_id=request_id.strip(),
                plan=plan,
                subject=f"线簿订阅-{plan.name}",
                now=self._now(),
            )
        except Exception:
            raced = self.store.find_order_by_request(user_id, request_id.strip())
            if raced is None:
                raise
            return self._resume(raced, plan)
        return OrderPayment(order=order, payment_html=self._page_pay(order))

    def _resume(self, order: Order, plan: Plan) -> OrderPayment:
        if order.plan != plan.code:
            raise BillingError(
                code="BILLING_REQUEST_CONFLICT",
                where="billing.order",
                why="同一请求标识已经用于其他档位",
                fix="新订单使用新的请求标识",
                status_code=409,
            )
        if order.status != "PENDING":
            return OrderPayment(order=order, payment_html="")
        return OrderPayment(order=order, payment_html=self._page_pay(order))

    def return_token(self, order_no: str) -> str:
        return hmac.new(
            self.store.return_secret().encode("utf-8"),
            order_no.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()[:32]

    def valid_return_token(self, order_no: str, token: str) -> bool:
        return bool(token) and secrets.compare_digest(token, self.return_token(order_no))

    def _signed_return_url(self, order_no: str) -> str:
        parts = urlsplit(self.settings.return_url)
        query = parse_qsl(parts.query, keep_blank_values=True)
        query.append(("out_trade_no", order_no))
        query.append(("order_token", self.return_token(order_no)))
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
        )

    def _page_pay(self, order: Order) -> str:
        try:
            return self.gateway.page_pay(
                out_trade_no=order.out_trade_no,
                amount=amount_text(order.amount_cents),
                subject=order.subject,
                return_url=self._signed_return_url(order.out_trade_no),
                notify_url=self.settings.notify_url,
            )
        except Exception as exc:
            logger.warning("alipay page pay failed: %s", type(exc).__name__)
            raise BillingError(
                code="BILLING_PAYMENT_CREATE_FAILED",
                where="billing.order",
                why="支付宝下单暂时不可用",
                fix="稍后使用同一请求标识重试；订单号已经保留",
                status_code=502,
            ) from exc

    # -- 查询 ---------------------------------------------------------------

    def get_order(self, user_id: str, order_no: str) -> Order:
        order = self.store.get_order(order_no)
        if order is None or order.user_id != user_id:
            raise self._not_found()
        return order

    def query_order(self, user_id: str, order_no: str) -> tuple[Order, dict[str, Any]]:
        order = self.get_order(user_id, order_no)
        return self._query_and_update(order)

    def refresh_order(self, order_no: str) -> Order:
        """回跳页只凭签名订单号刷新状态，不建立账号会话。"""
        order = self.store.get_order(order_no)
        if order is None:
            raise self._not_found()
        updated, _ = self._query_and_update(order)
        return updated

    def _query_and_update(self, order: Order) -> tuple[Order, dict[str, Any]]:
        try:
            result = self.gateway.query(order.out_trade_no)
        except Exception as exc:
            self.store.mark_checked(order.out_trade_no, self._now(), error=type(exc).__name__)
            raise BillingError(
                code="BILLING_QUERY_FAILED",
                where="billing.query",
                why="支付宝交易查询暂时不可用",
                fix="稍后重试；若已付款，订阅会在通知到达后生效",
                status_code=502,
            ) from exc
        code = str(result.get("code", ""))
        if code != "10000":
            self.store.mark_checked(order.out_trade_no, self._now(), error=f"gateway:{code}")
            return self.store.get_order(order.out_trade_no) or order, result
        total_amount = result.get("total_amount")
        if total_amount is not None and parse_amount_cents(str(total_amount)) != order.amount_cents:
            self.store.mark_checked(order.out_trade_no, self._now(), error="amount_mismatch")
            raise BillingError(
                code="BILLING_AMOUNT_MISMATCH",
                where="billing.query",
                why="支付宝返回的订单金额与本地订单不一致",
                fix="停止发货并核对订单与支付宝交易记录",
                status_code=409,
            )
        seller_id = str(result.get("seller_id", ""))
        if self.settings.seller_id and seller_id and seller_id != self.settings.seller_id:
            self.store.mark_checked(order.out_trade_no, self._now(), error="seller_mismatch")
            raise BillingError(
                code="BILLING_SELLER_MISMATCH",
                where="billing.query",
                why="支付宝返回的收款方与配置不一致",
                fix="停止发货并核对商户配置",
                status_code=409,
            )
        updated = self.store.update_from_query(
            order.out_trade_no,
            trade_status=str(result.get("trade_status", "")),
            trade_no=str(result.get("trade_no", "")),
            buyer_id=str(result.get("buyer_user_id", "")),
            raw=_safe_query_raw(result),
            now=self._now(),
        )
        return updated, result

    def reconcile_pending(
        self, *, limit: int = 20, min_age_seconds: float = RECONCILE_MIN_AGE_SECONDS
    ) -> int:
        cutoff = (parse_ts(self._now()) - timedelta(seconds=min_age_seconds)).isoformat()
        changed = 0
        for order in self.store.pending_orders(checked_before=cutoff, limit=limit):
            try:
                result = self.gateway.query(order.out_trade_no)
            except Exception as exc:
                self.store.mark_checked(order.out_trade_no, self._now(), error=type(exc).__name__)
                continue
            code = str(result.get("code", ""))
            if code != "10000":
                self.store.mark_checked(order.out_trade_no, self._now(), error=f"gateway:{code}")
                continue
            total = result.get("total_amount")
            if total is not None and parse_amount_cents(str(total)) != order.amount_cents:
                self.store.mark_checked(order.out_trade_no, self._now(), error="amount_mismatch")
                continue
            try:
                updated = self.store.update_from_query(
                    order.out_trade_no,
                    trade_status=str(result.get("trade_status", "")),
                    trade_no=str(result.get("trade_no", "")),
                    buyer_id=str(result.get("buyer_user_id", "")),
                    raw=_safe_query_raw(result),
                    now=self._now(),
                )
            except Exception:
                logger.exception("pending order reconcile failed")
                self.store.mark_checked(order.out_trade_no, self._now(), error="reconcile_failed")
                continue
            if updated.status != order.status:
                changed += 1
        return changed

    # -- 通知 ---------------------------------------------------------------

    def handle_notify(self, params: dict[str, str]) -> bool:
        if params.get("app_id") != self.settings.app_id:
            return False
        if not self.gateway.verify_notify(params):
            return False
        order_no = params.get("out_trade_no", "")
        order = self.store.get_order(order_no)
        if order is None:
            return False
        if order.status == "REFUNDED":
            # 已退款订单不再接受支付通知，也不让它触发权益重发。
            return True
        if parse_amount_cents(params.get("total_amount", "")) != order.amount_cents:
            return False
        if self.settings.seller_id and params.get("seller_id", "") != self.settings.seller_id:
            return False
        trade_status = params.get("trade_status", "")
        is_paid = (
            trade_status in PAID_TRADE_STATUSES
            and not params.get("out_biz_no")
            and not params.get("gmt_refund")
            and not params.get("refund_fee")
        )
        notify_id = params.get("notify_id", "")
        if not notify_id:
            return False
        try:
            self.store.process_notify(
                notify_id=notify_id,
                out_trade_no=order_no,
                trade_no=params.get("trade_no", ""),
                trade_status=trade_status,
                buyer_id=params.get("buyer_id", ""),
                raw=_safe_notify_raw(params),
                is_paid=is_paid,
                now=self._now(),
            )
        except Exception:
            logger.exception("alipay notify processing failed")
            return False
        return True

    # -- 退款 ---------------------------------------------------------------

    def request_refund(
        self, user_id: str, order_no: str, reason: str, request_id: str
    ) -> dict[str, object]:
        order = self.get_order(user_id, order_no)
        if order.status != "PAID":
            raise BillingError(
                code="BILLING_REFUND_NOT_ALLOWED",
                where="billing.refund",
                why="只有已支付订单可以申请退款",
                fix="刷新订单状态后再试",
                status_code=409,
            )
        digest = hashlib.sha256(f"{user_id}:{request_id}".encode()).hexdigest()[:20]
        request = self.store.create_refund_request(
            out_request_no=f"RR{digest}",
            out_trade_no=order_no,
            user_id=user_id,
            reason=reason.strip()[:500],
            now=self._now(),
        )
        return {
            "out_request_no": request.out_request_no,
            "order_no": request.out_trade_no,
            "status": request.status,
            "reason": request.reason,
            "created_at": request.created_at,
        }

    def admin_refund(
        self,
        order_no: str,
        *,
        amount: str | None,
        out_request_no: str | None,
        reason: str,
    ) -> dict[str, object]:
        order = self.store.get_order(order_no)
        if order is None:
            raise BillingError(
                code="BILLING_ORDER_NOT_FOUND",
                where="billing.admin_refund",
                why="订单不存在",
                fix="核对订单号后重试",
                status_code=404,
            )
        if order.status != "PAID":
            raise BillingError(
                code="BILLING_REFUND_NOT_ALLOWED",
                where="billing.admin_refund",
                why="只有已支付订单可以退款",
                fix="确认订单状态后再操作",
                status_code=409,
            )
        amount_cents = order.amount_cents if amount is None else parse_amount_cents(amount)
        if amount_cents is None or amount_cents <= 0 or amount_cents > order.amount_cents:
            raise BillingError(
                code="BILLING_REFUND_AMOUNT_INVALID",
                where="billing.admin_refund",
                why="退款金额无效",
                fix="退款金额必须大于 0 且不超过订单实付金额",
                status_code=400,
            )
        request_no = (out_request_no or "").strip() or new_refund_no(order_no)
        try:
            record = self.store.create_refund(
                out_request_no=request_no,
                out_trade_no=order_no,
                amount_cents=amount_cents,
                now=self._now(),
            )
        except (RefundConflictError, ValueError) as exc:
            raise BillingError(
                code="BILLING_REFUND_CONFLICT",
                where="billing.admin_refund",
                why="退款请求与已有记录冲突，或累计退款超过订单实付金额",
                fix="核对退款请求号与金额；新退款使用新的请求号",
                status_code=409,
            ) from exc
        try:
            result = self.gateway.refund(
                out_trade_no=order_no,
                amount=amount_text(amount_cents),
                out_request_no=record.out_request_no,
                reason=reason,
            )
        except Exception as exc:
            raise BillingError(
                code="BILLING_REFUND_FAILED",
                where="billing.admin_refund",
                why="支付宝退款请求暂时不可用",
                fix="使用同一退款请求号重试，或通过退款查询确认结果",
                status_code=502,
            ) from exc
        code = str(result.get("code", ""))
        fund_change = str(result.get("fund_change", ""))
        if code != "10000":
            self.store.mark_refund_result(
                record.out_request_no,
                status="FAILED",
                fund_change=fund_change,
                raw=json.dumps(result, ensure_ascii=False),
                now=self._now(),
            )
            raise BillingError(
                code="BILLING_REFUND_REJECTED",
                where="billing.admin_refund",
                why="支付宝未接受退款请求",
                fix="核对订单与退款金额，或联系支付宝支持",
                status_code=409,
            )
        updated = self.store.mark_refund_result(
            record.out_request_no,
            status="SUCCESS" if fund_change == "Y" else "PENDING",
            fund_change=fund_change,
            raw=json.dumps(result, ensure_ascii=False),
            now=self._now(),
        )
        return self._refund_payload(updated, result)

    def query_refund(self, out_request_no: str) -> dict[str, object]:
        record = self.store.get_refund(out_request_no)
        if record is None:
            raise BillingError(
                code="BILLING_REFUND_NOT_FOUND",
                where="billing.admin_refund",
                why="退款记录不存在",
                fix="核对退款请求号后重试",
                status_code=404,
            )
        try:
            result = self.gateway.refund_query(
                out_trade_no=record.out_trade_no,
                out_request_no=record.out_request_no,
            )
        except Exception as exc:
            raise BillingError(
                code="BILLING_REFUND_QUERY_FAILED",
                where="billing.admin_refund",
                why="支付宝退款查询暂时不可用",
                fix="稍后重试；不要重复发起退款",
                status_code=502,
            ) from exc
        code = str(result.get("code", ""))
        if code == "10000" and str(result.get("refund_status", "")) == "REFUND_SUCCESS":
            record = self.store.mark_refund_result(
                record.out_request_no,
                status="SUCCESS",
                fund_change=record.fund_change,
                raw=json.dumps(result, ensure_ascii=False),
                now=self._now(),
            )
        return {
            "out_request_no": record.out_request_no,
            "order_no": record.out_trade_no,
            "amount": amount_text(record.amount_cents),
            "status": record.status,
            "fund_change": record.fund_change,
            "gateway_code": code,
            "gateway_refund_status": str(result.get("refund_status", "")),
        }

    def close_order(self, order_no: str) -> dict[str, object]:
        order = self.store.get_order(order_no)
        if order is None:
            raise BillingError(
                code="BILLING_ORDER_NOT_FOUND",
                where="billing.admin_close",
                why="订单不存在",
                fix="核对订单号后重试",
                status_code=404,
            )
        try:
            result = self.gateway.close(order_no)
        except Exception as exc:
            raise BillingError(
                code="BILLING_CLOSE_FAILED",
                where="billing.admin_close",
                why="支付宝关单暂时不可用",
                fix="稍后重试",
                status_code=502,
            ) from exc
        if str(result.get("code", "")) != "10000":
            raise BillingError(
                code="BILLING_CLOSE_REJECTED",
                where="billing.admin_close",
                why="支付宝未接受关单请求",
                fix="确认订单未支付后重试；已支付订单不能关闭",
                status_code=409,
            )
        try:
            updated = self.store.close_order(order_no, self._now())
        except ValueError as exc:
            raise BillingError(
                code="BILLING_CLOSE_REJECTED",
                where="billing.admin_close",
                why="本地订单状态不允许关闭",
                fix="已支付订单请走退款；未支付订单可重新关单",
                status_code=409,
            ) from exc
        return self.order_payload(updated)

    @staticmethod
    def _refund_payload(record: RefundRecord, result: dict[str, Any]) -> dict[str, object]:
        return {
            "out_request_no": record.out_request_no,
            "status": record.status,
            "fund_change": record.fund_change,
            "refund_fee": str(result.get("refund_fee", "")),
            "gateway_code": str(result.get("code", "")),
        }

    @staticmethod
    def _not_found() -> BillingError:
        return BillingError(
            code="BILLING_ORDER_NOT_FOUND",
            where="billing.order",
            why="订单不存在",
            fix="返回订阅页重新下单",
            status_code=404,
        )
