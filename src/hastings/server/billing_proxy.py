"""写作服务到支付服务的账单反向代理。

生产由 nginx 把 ``/api/billing/*`` 直接转到 relay；本地直接用 8000 托管构建前端时，
没有 nginx，这条路由保证同一入口仍能访问账单接口。代理只转发固定的账单前缀，
不成为任意 URL 的出口；登录门与 relay 的 Cookie/CSRF 校验仍然生效。
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

__all__ = ["create_billing_proxy_router"]

_HOP_HEADERS = frozenset(
    {
        "host",
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "content-length",
        "accept-encoding",
    }
)


def _error(request: Request, status: int, code: str, message: str) -> JSONResponse:
    request_id = str(getattr(request.state, "request_id", ""))
    return JSONResponse(
        status_code=status,
        content={
            "code": code,
            "message": f"[{code}] billing.proxy：{message}。建议：确认支付服务已启动后重试。",
            "request_id": request_id,
        },
    )


def create_billing_proxy_router(
    billing_url: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> APIRouter:
    base = billing_url.rstrip("/")
    router = APIRouter(prefix="/api/billing", tags=["billing-proxy"])

    @router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def proxy(path: str, request: Request) -> Response:
        target = f"{base}/api/billing/{path}"
        headers = {
            key: value for key, value in request.headers.items() if key.lower() not in _HOP_HEADERS
        }
        try:
            async with httpx.AsyncClient(
                timeout=30.0, transport=transport, trust_env=False
            ) as client:
                upstream = await client.request(
                    request.method,
                    target,
                    params=list(request.query_params.multi_items()),
                    content=await request.body(),
                    headers=headers,
                )
        except Exception:
            return _error(request, 502, "BILLING_UNAVAILABLE", "支付服务暂时不可用")
        response_headers = {
            key: value
            for key, value in upstream.headers.items()
            if key.lower() not in _HOP_HEADERS and key.lower() != "content-encoding"
        }
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            headers=response_headers,
        )

    return router
