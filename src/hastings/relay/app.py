"""订阅支付 HTTP 服务（v1.4 P3）。

浏览器用写作服务的登录 Cookie；支付服务不再签发匿名会话，也不把长期凭据放进
localStorage。网站账单接口在 ``/api/billing/*``，支付宝通知与回跳保持公开路径。
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any
from urllib.parse import urlencode, urlsplit

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel, Field

from hastings.accounts.middleware import RequestContextMiddleware
from hastings.accounts.models import SessionRecord
from hastings.accounts.tokens import hash_token
from hastings.relay.alipay import AlipayGateway, SdkAlipayGateway
from hastings.relay.config import RelaySettings, load_settings
from hastings.relay.internal_auth import verify_grant
from hastings.relay.proxy import ModelProxy, ProxyError
from hastings.relay.service import BillingError, BillingService
from hastings.relay.store import Order, RelayStore, utc_now

logger = logging.getLogger("hastings.relay")


class CreateOrderBody(BaseModel):
    plan_code: str = Field(min_length=1, max_length=40)
    request_id: str = Field(min_length=1, max_length=120)


class RefundRequestBody(BaseModel):
    reason: str = Field(default="", max_length=500)
    request_id: str = Field(min_length=1, max_length=120)


class AdminRefundBody(BaseModel):
    amount: str | None = None
    out_request_no: str | None = None
    reason: str = ""


def _error(status_code: int, code: str, where: str, why: str, fix: str) -> BillingError:
    return BillingError(status_code=status_code, code=code, where=where, why=why, fix=fix)


def _origin_allowed(settings: RelaySettings, origin: str, host: str | None) -> bool:
    normalized = origin.strip().rstrip("/")
    if not normalized or normalized == "null":
        return False
    allowed = {item.rstrip("/").lower() for item in settings.allowed_origins}
    allowed.add(settings.site_url.rstrip("/").lower())
    allowed.add(settings.public_base_url.rstrip("/").lower())
    if normalized.lower() in allowed:
        return True
    if not host:
        return False
    netloc = urlsplit(normalized).netloc
    return bool(netloc) and netloc.lower() == host.lower()


def _record(request: Request, settings: RelaySettings, store: RelayStore) -> SessionRecord:
    token = request.cookies.get(settings.cookie_name)
    record = store.accounts.session(hash_token(token), int(time.time())) if token else None
    if record is None:
        raise _error(
            401,
            "BILLING_SESSION_REQUIRED",
            "billing.auth",
            "需要登录后才能购买或查看订单",
            "回到线簿登录后重试",
        )
    return record


def _require_write(request: Request, settings: RelaySettings, record: SessionRecord) -> str:
    origin = request.headers.get("origin")
    if origin is not None and not _origin_allowed(settings, origin, request.headers.get("host")):
        raise _error(
            403,
            "BILLING_ORIGIN_REJECTED",
            "billing.auth",
            "请求来源不是本站",
            "从线簿页面重新操作，不要在其他网站提交",
        )
    supplied = request.headers.get("x-csrf-token", "").encode("utf-8", "replace")
    if not secrets.compare_digest(supplied, record.csrf_token.encode("utf-8")):
        raise _error(
            403,
            "BILLING_CSRF_FAILED",
            "billing.auth",
            "页面校验令牌缺失或已过期",
            "刷新页面后重试",
        )
    return record.account.user_id


def _require_admin(request: Request, settings: RelaySettings) -> None:
    if not settings.admin_token:
        raise _error(
            503,
            "BILLING_ADMIN_DISABLED",
            "billing.admin",
            "服务未配置退款管理令牌",
            "设置 RELAY_ADMIN_TOKEN 后重启服务",
        )
    provided = request.headers.get("x-admin-token", "")
    if not provided or not secrets.compare_digest(provided, settings.admin_token):
        raise _error(
            403,
            "BILLING_ADMIN_FORBIDDEN",
            "billing.admin",
            "管理令牌不正确",
            "使用服务端配置的 RELAY_ADMIN_TOKEN",
        )


def _return_page(order: Order | None, order_token: str, site_url: str) -> str:
    if order is None:
        body = (
            '<p class="lead">没有找到对应的订单。</p>'
            '<p class="muted">请返回线簿重新下单；如已付款，请联系支持并提供订单号。</p>'
            f'<a class="button" href="{html.escape(site_url)}">返回线簿</a>'
        )
        return _page_shell(title="支付结果 · 线簿", body=body)
    labels = {
        "PENDING": "正在确认支付结果",
        "PAID": "支付成功",
        "CLOSED": "订单已关闭",
        "REFUNDED": "已退款",
    }
    messages = {
        "PENDING": "支付宝正在确认这笔交易。页面会自动刷新，也可以稍后回到订阅页查看。",
        "PAID": "订阅已生效，可以回到线簿继续写作。",
        "CLOSED": "这笔订单已关闭，没有产生扣款。",
        "REFUNDED": "这笔订单已退款，对应订阅权益已结束。",
    }
    status = order.status if order.status in labels else "PENDING"
    pending = "true" if status == "PENDING" else "false"
    body = f"""
    <p class="eyebrow">订单结果</p>
    <h1>{labels[status]}</h1>
    <p class="lead" id="status-text">{messages[status]}</p>
    <dl class="facts">
      <div><dt>订单号</dt><dd>{html.escape(order.out_trade_no)}</dd></div>
      <div><dt>金额</dt><dd>¥{order.amount_cents / 100:.2f}</dd></div>
    </dl>
    <a class="button" href="{html.escape(site_url)}">返回线簿</a>
    <script>
    const orderNo = {json.dumps(order.out_trade_no)};
    const orderToken = {json.dumps(order_token)};
    const pending = {pending};
    const statusNode = document.getElementById("status-text");
    let attempts = 0;
    async function poll() {{
      if (!pending || attempts >= 30) return;
      attempts += 1;
      try {{
        const url = "/return/alipay/status?out_trade_no=" + encodeURIComponent(orderNo)
          + "&order_token=" + encodeURIComponent(orderToken);
        const response = await fetch(url);
        if (response.ok) {{
          const data = await response.json();
          if (data.status && data.status !== "PENDING") {{ window.location.reload(); return; }}
        }}
      }} catch (error) {{
        statusNode.textContent = "网络暂时不稳定，正在重试确认。";
      }}
      setTimeout(poll, 2000);
    }}
    poll();
    </script>
    """
    return _page_shell(title="支付结果 · 线簿", body=body)


def _page_shell(*, title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
:root {{ color-scheme: light; }}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; min-height: 100vh; display: grid; place-items: center;
  padding: 32px 20px; color: #26221d; background: #f4efe6;
  font-family: ui-serif, "Songti SC", "Noto Serif CJK SC", Georgia, serif;
}}
main {{
  width: min(680px, 100%); background: #fffdf8; border: 1px solid #ddd3c4;
  border-radius: 20px; padding: clamp(28px, 6vw, 56px);
  box-shadow: 0 24px 60px rgba(79, 60, 39, 0.09);
}}
h1 {{ margin: 8px 0 12px; font-size: clamp(30px, 6vw, 44px); line-height: 1.15; }}
.lead {{ margin: 0 0 18px; font-size: 17px; line-height: 1.8; color: #4d463d; }}
.muted {{ color: #7a7064; line-height: 1.8; }}
.eyebrow {{ margin: 0; font-size: 12px; letter-spacing: 0.22em; color: #a45b36; }}
.facts {{ margin: 26px 0; border-top: 1px solid #e5dccd; }}
.facts div {{
  display: flex; justify-content: space-between; gap: 20px;
  padding: 13px 0; border-bottom: 1px solid #e5dccd;
}}
dt {{ color: #7a7064; }}
dd {{ margin: 0; font-variant-numeric: tabular-nums; }}
.button {{
  display: inline-block; margin-top: 10px; padding: 12px 20px; border-radius: 999px;
  color: #fff; background: #a44b2b; text-decoration: none; font-size: 15px;
}}
</style>
</head>
<body><main>{body}</main></body>
</html>"""


def create_relay_app(
    *,
    settings: RelaySettings | None = None,
    store: RelayStore | None = None,
    gateway: AlipayGateway | None = None,
    service: BillingService | None = None,
    proxy: ModelProxy | None = None,
) -> FastAPI:
    resolved = settings or load_settings()
    resolved_store = store or RelayStore.from_path(resolved.database_path)
    resolved_store.initialize()
    resolved_gateway = gateway or SdkAlipayGateway(resolved)
    billing = service or BillingService(resolved_store, resolved_gateway, resolved)
    model_proxy = proxy or ModelProxy(resolved_store, resolved)

    async def _reconcile_loop() -> None:
        interval = resolved.reconcile_interval_seconds
        while interval > 0:
            await asyncio.sleep(interval)
            try:
                await asyncio.to_thread(billing.reconcile_pending)
                await asyncio.to_thread(resolved_store.recover_usage_reservations, utc_now())
            except Exception:
                logger.exception("billing reconcile loop failed")

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await asyncio.to_thread(resolved_store.recover_usage_reservations, utc_now())
        task: asyncio.Task[None] | None = None
        if resolved.reconcile_interval_seconds > 0:
            task = asyncio.create_task(_reconcile_loop())
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(title="线簿订阅支付", version="1.4.0", lifespan=lifespan)
    app.state.settings = resolved
    app.state.store = resolved_store
    app.state.gateway = resolved_gateway
    app.state.billing = billing
    app.state.model_proxy = model_proxy
    app.add_middleware(RequestContextMiddleware)

    @app.exception_handler(BillingError)
    async def billing_error_handler(request: Request, exc: BillingError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": exc.code,
                "message": exc.message,
                "request_id": str(getattr(request.state, "request_id", "")),
            },
        )

    @app.exception_handler(ProxyError)
    async def proxy_error_handler(_: Request, exc: ProxyError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "message": f"[{exc.code}] {exc.message}",
                    "type": "billing_error",
                    "code": exc.code,
                }
            },
        )

    @app.get("/health")
    def health() -> Response:
        try:
            resolved_store.ping()
        except Exception:
            logger.warning("relay health check failed")
            return JSONResponse(status_code=503, content={"status": "degraded"})
        return JSONResponse(content={"status": "ok"})

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Response:
        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        grant = (
            verify_grant(token.strip(), resolved.internal_secret)
            if scheme.lower() == "bearer"
            else None
        )
        if grant is None:
            raise ProxyError(
                401,
                "BILLING_GRANT_INVALID",
                "订阅模型授权无效或已过期，请重新发起这一轮。",
            )
        try:
            body = await request.json()
        except ValueError as exc:
            raise ProxyError(400, "BILLING_BODY_INVALID", "请求体必须是 JSON 对象。") from exc
        return await model_proxy.completions(body, grant)

    @app.get("/")
    def index() -> Any:
        if resolved.site_url.rstrip("/") != resolved.public_base_url.rstrip("/"):
            return RedirectResponse(resolved.site_url)
        return {"service": "line-book billing", "site": resolved.site_url}

    @app.get("/purchase")
    def purchase() -> RedirectResponse:
        return RedirectResponse(resolved.site_url)

    @app.get("/v1/plans")
    def internal_plans() -> dict[str, object]:
        return {"plans": billing.plans()}

    @app.get("/api/billing/plans")
    def plans() -> dict[str, object]:
        return {"plans": billing.plans()}

    @app.post("/api/billing/orders")
    def create_order(body: CreateOrderBody, request: Request) -> dict[str, object]:
        record = _record(request, resolved, resolved_store)
        user_id = _require_write(request, resolved, record)
        payment = billing.create_order(user_id, body.plan_code, body.request_id)
        return {**billing.order_payload(payment.order), "payment_url": payment.payment_url}

    @app.get("/api/billing/orders/{order_no}")
    def get_order(order_no: str, request: Request) -> dict[str, object]:
        record = _record(request, resolved, resolved_store)
        return billing.order_payload(billing.get_order(record.account.user_id, order_no))

    @app.post("/api/billing/orders/{order_no}/query")
    def query_order(order_no: str, request: Request) -> dict[str, object]:
        record = _record(request, resolved, resolved_store)
        user_id = _require_write(request, resolved, record)
        order, result = billing.query_order(user_id, order_no)
        payload = billing.order_payload(order)
        payload["gateway_code"] = str(result.get("code", ""))
        payload["gateway_trade_status"] = str(result.get("trade_status", ""))
        return payload

    @app.get("/api/billing/entitlement")
    def entitlement(request: Request) -> dict[str, object]:
        record = _record(request, resolved, resolved_store)
        return billing.entitlement(record.account.user_id)

    @app.get("/api/billing/usage")
    def usage(request: Request) -> dict[str, object]:
        record = _record(request, resolved, resolved_store)
        return billing.entitlement(record.account.user_id)

    @app.post("/api/billing/orders/{order_no}/refund-request")
    def refund_request(
        order_no: str, body: RefundRequestBody, request: Request
    ) -> dict[str, object]:
        record = _record(request, resolved, resolved_store)
        user_id = _require_write(request, resolved, record)
        return billing.request_refund(user_id, order_no, body.reason, body.request_id)

    # -- 运营：退款与关单 ------------------------------------------------

    @app.post("/v1/orders/{order_no}/refund")
    def admin_refund(order_no: str, body: AdminRefundBody, request: Request) -> dict[str, object]:
        _require_admin(request, resolved)
        return billing.admin_refund(
            order_no,
            amount=body.amount,
            out_request_no=body.out_request_no,
            reason=body.reason,
        )

    @app.get("/v1/refunds/{out_request_no}")
    def admin_refund_query(out_request_no: str, request: Request) -> dict[str, object]:
        _require_admin(request, resolved)
        return billing.query_refund(out_request_no)

    @app.post("/v1/orders/{order_no}/close")
    def admin_close(order_no: str, request: Request) -> dict[str, object]:
        _require_admin(request, resolved)
        return billing.close_order(order_no)

    # -- 支付宝：通知与回跳 ----------------------------------------------

    @app.get("/pay/{order_no}")
    def pay_redirect(order_no: str, request: Request) -> RedirectResponse:
        token = request.query_params.get("token", "").strip()
        order = resolved_store.get_order(order_no)
        if order is None or not billing.valid_return_token(order_no, token):
            raise _error(
                404,
                "BILLING_ORDER_NOT_FOUND",
                "billing.pay",
                "支付链接无效",
                "回到订阅页重新下单",
            )
        if order.status != "PENDING":
            query = urlencode({"out_trade_no": order_no, "order_token": token})
            return RedirectResponse(
                f"{resolved.site_url.rstrip('/')}/return/alipay?{query}", status_code=302
            )
        return RedirectResponse(billing.cashier_url(order), status_code=302)

    @app.post("/notify/alipay")
    async def alipay_notify(request: Request) -> PlainTextResponse:
        try:
            form = await request.form()
            params = {key: str(value) for key, value in form.items()}
        except Exception:
            return PlainTextResponse("fail")
        return PlainTextResponse("success" if billing.handle_notify(params) else "fail")

    @app.get("/return/alipay")
    def alipay_return(request: Request) -> HTMLResponse:
        order_no = request.query_params.get("out_trade_no", "").strip()
        token = request.query_params.get("order_token", "").strip()
        order = resolved_store.get_order(order_no) if order_no else None
        if order is not None and not billing.valid_return_token(order_no, token):
            order = None
        if order is not None:
            with suppress(BillingError):
                order = billing.refresh_order(order_no)
        return HTMLResponse(
            _return_page(order, token if order is not None else "", resolved.site_url)
        )

    @app.get("/return/alipay/status")
    def alipay_return_status(request: Request) -> dict[str, object]:
        order_no = request.query_params.get("out_trade_no", "").strip()
        token = request.query_params.get("order_token", "").strip()
        order = resolved_store.get_order(order_no) if order_no else None
        if order is None or not billing.valid_return_token(order_no, token):
            raise _error(
                404,
                "BILLING_ORDER_NOT_FOUND",
                "billing.return",
                "订单不存在",
                "返回订阅页重新下单",
            )
        order = billing.refresh_order(order_no)
        return billing.order_payload(order)

    return app
