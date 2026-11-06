"""账单反向代理：固定前缀、保留会话/CSRF，relay 不可用时返回可读错误。"""

from __future__ import annotations

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hastings.server.billing_proxy import create_billing_proxy_router


def _client(handler: object) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_billing_proxy_router(
            "http://127.0.0.1:8300",
            transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
        )
    )
    return TestClient(app)


def test_forwards_path_query_cookie_csrf_and_response() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["cookie"] = request.headers.get("cookie")
        seen["csrf"] = request.headers.get("x-csrf-token")
        seen["body"] = request.content
        return httpx.Response(200, json={"plans": []}, headers={"X-Upstream": "relay"})

    client = _client(handler)
    response = client.post(
        "/api/billing/orders?source=test",
        json={"plan_code": "experience", "request_id": "req-1"},
        headers={"Cookie": "clue_session=token", "X-CSRF-Token": "csrf"},
    )
    assert response.status_code == 200
    assert response.json() == {"plans": []}
    assert response.headers["x-upstream"] == "relay"
    assert seen["url"] == "http://127.0.0.1:8300/api/billing/orders?source=test"
    assert seen["cookie"] == "clue_session=token"
    assert seen["csrf"] == "csrf"
    assert b"experience" in seen["body"]  # type: ignore[operator]


def test_relay_unavailable_returns_billing_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    client = _client(handler)
    response = client.get("/api/billing/plans")
    assert response.status_code == 502
    body = response.json()
    assert body["code"] == "BILLING_UNAVAILABLE"
    assert body["request_id"] == ""
