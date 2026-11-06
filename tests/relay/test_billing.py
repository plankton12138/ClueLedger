"""v1.4 P3：账号订单、通知、查单恢复、权益与退款。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from hastings.accounts.db import AccountDatabase
from hastings.accounts.store import AccountsStore
from hastings.accounts.tokens import hash_token
from hastings.relay.app import create_relay_app
from hastings.relay.config import RelaySettings, load_settings
from hastings.relay.service import BillingService
from hastings.relay.store import RelayStore

APP_ID = "2026000000000000"
SELLER_ID = "2088000000000000"
COOKIE = "clue_session"
USER_A = "usr_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
USER_B = "usr_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


class FakeGateway:
    def __init__(self) -> None:
        self.payment_html = (
            '<form id="pay" method="post" action="https://sandbox.example/gateway.do">'
            '<input type="hidden" name="biz_content" value="{}"></form>'
        )
        self.last_return_url = ""
        self.verify_result = True
        self.query_result: dict[str, Any] = {
            "code": "10000",
            "trade_status": "WAIT_BUYER_PAY",
        }
        self.refund_result: dict[str, Any] = {
            "code": "10000",
            "fund_change": "Y",
            "refund_fee": "9.00",
        }
        self.refund_query_result: dict[str, Any] = {
            "code": "10000",
            "refund_status": "REFUND_SUCCESS",
        }
        self.close_result: dict[str, Any] = {"code": "10000"}
        self.page_calls = 0
        self.refund_calls = 0

    def page_pay(
        self,
        *,
        out_trade_no: str,
        amount: str,
        subject: str,
        return_url: str,
        notify_url: str,
    ) -> str:
        assert out_trade_no and amount and subject and return_url
        assert "order_token=" in return_url
        assert f"out_trade_no={out_trade_no}" in return_url
        assert notify_url == ""
        self.page_calls += 1
        self.last_return_url = return_url
        return self.payment_html

    def query(self, out_trade_no: str) -> dict[str, Any]:
        assert out_trade_no
        return dict(self.query_result)

    def refund(
        self,
        *,
        out_trade_no: str,
        amount: str,
        out_request_no: str,
        reason: str,
    ) -> dict[str, Any]:
        assert out_trade_no and amount and out_request_no
        assert reason is not None
        self.refund_calls += 1
        return dict(self.refund_result)

    def refund_query(self, *, out_trade_no: str, out_request_no: str) -> dict[str, Any]:
        assert out_trade_no and out_request_no
        return dict(self.refund_query_result)

    def close(self, out_trade_no: str) -> dict[str, Any]:
        assert out_trade_no
        return dict(self.close_result)

    def verify_notify(self, params: dict[str, str]) -> bool:
        return bool(params.get("sign")) and self.verify_result


def _settings(tmp_path: Path) -> RelaySettings:
    return RelaySettings(
        app_id=APP_ID,
        private_key="pkcs1-key",
        alipay_public_key="public-key",
        gateway="https://openapi-sandbox.dl.alipaydev.com/gateway.do",
        seller_id=SELLER_ID,
        notify_url="",
        return_url="http://testserver/return/alipay",
        database_path=tmp_path / "clueledger.sqlite3",
        admin_token="admin-secret",
        allowed_origins=("http://testserver",),
        sandbox=True,
        public_base_url="http://testserver",
        site_url="http://testserver",
        cookie_name=COOKIE,
        reconcile_interval_seconds=0,
    )


def _build(
    tmp_path: Path,
) -> tuple[TestClient, FakeGateway, RelayStore, BillingService, AccountDatabase]:
    settings = _settings(tmp_path)
    database = AccountDatabase(settings.database_path)
    database.initialize()
    accounts = AccountsStore(database)
    now = int(time.time())
    for user_id, email in ((USER_A, "a@example.com"), (USER_B, "b@example.com")):
        accounts.create_account(
            user_id=user_id,
            email=email,
            email_norm=email,
            password_hash="not-used-in-billing-tests",
            now=now,
        )
    for user_id, token, csrf in (
        (USER_A, "token-a", "csrf-a"),
        (USER_B, "token-b", "csrf-b"),
    ):
        accounts.create_session(
            token_hash=hash_token(token),
            user_id=user_id,
            csrf_token=csrf,
            now=now,
            expires_at=now + 3600,
        )
    gateway = FakeGateway()
    store = RelayStore(database)
    app = create_relay_app(settings=settings, store=store, gateway=gateway)
    return TestClient(app), gateway, store, app.state.billing, database


def _headers(user: str, *, write: bool = False) -> dict[str, str]:
    token, csrf = ("token-a", "csrf-a") if user == USER_A else ("token-b", "csrf-b")
    headers = {"Cookie": f"{COOKIE}={token}"}
    if write:
        headers["X-CSRF-Token"] = csrf
        headers["Origin"] = "http://testserver"
    return headers


def _create(
    client: TestClient,
    *,
    plan: str = "experience",
    request_id: str = "req-1",
    user: str = USER_A,
) -> dict[str, Any]:
    response = client.post(
        "/api/billing/orders",
        json={"plan_code": plan, "request_id": request_id},
        headers=_headers(user, write=True),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _notify(
    client: TestClient,
    order_no: str,
    *,
    notify_id: str = "notify-1",
    total_amount: str = "9.00",
    sign: str = "test-signature",
) -> Any:
    return client.post(
        "/notify/alipay",
        data={
            "app_id": APP_ID,
            "notify_id": notify_id,
            "notify_type": "trade_status_sync",
            "out_trade_no": order_no,
            "trade_no": "2026110622000000001",
            "trade_status": "TRADE_SUCCESS",
            "total_amount": total_amount,
            "seller_id": SELLER_ID,
            "buyer_id": "2088000000000001",
            "sign_type": "RSA2",
            "sign": sign,
        },
    )


def _paid(client: TestClient, *, user: str = USER_A) -> str:
    order = _create(client, request_id="paid-1", user=user)
    response = _notify(client, order["order_no"])
    assert response.text == "success"
    return str(order["order_no"])


def test_plans_are_public_and_prices_come_from_server(
    tmp_path: Path,
) -> None:
    client, _, _, _, _ = _build(tmp_path)
    plans = client.get("/api/billing/plans")
    assert plans.status_code == 200
    rows = plans.json()["plans"]
    assert [row["code"] for row in rows] == ["experience", "standard", "long"]
    assert rows[0]["price_cents"] == 900

    order = _create(client)
    assert order["amount_cents"] == 900
    assert order["status"] == "PENDING"
    assert "payment_html" in order


def test_order_creation_is_idempotent_and_scoped_to_the_account(tmp_path: Path) -> None:
    client, gateway, _, _, _ = _build(tmp_path)
    first = _create(client, request_id="same-request")
    again = _create(client, request_id="same-request")
    assert again["order_no"] == first["order_no"]
    assert gateway.page_calls == 2

    used = client.post(
        "/api/billing/orders",
        json={"plan_code": "experience", "request_id": "another-request"},
        headers=_headers(USER_A, write=True),
    )
    assert used.status_code == 409
    assert used.json()["code"] == "BILLING_EXPERIENCE_USED"

    assert (
        client.get(f"/api/billing/orders/{first['order_no']}", headers=_headers(USER_B)).status_code
        == 404
    )
    own = _create(client, request_id="same-request", user=USER_B)
    assert own["order_no"] != first["order_no"]
    assert (
        client.get(f"/api/billing/orders/{own['order_no']}", headers=_headers(USER_B)).status_code
        == 200
    )


def test_unauthenticated_order_is_rejected(tmp_path: Path) -> None:
    client, _, _, _, _ = _build(tmp_path)
    response = client.post(
        "/api/billing/orders",
        json={"plan_code": "experience", "request_id": "req"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "BILLING_SESSION_REQUIRED"
    assert client.get("/api/billing/orders/CL000").status_code == 401


def test_notify_grants_once_even_if_payer_is_someone_else(tmp_path: Path) -> None:
    client, _, store, _, _ = _build(tmp_path)
    order = _create(client)
    assert _notify(client, order["order_no"]).text == "success"

    paid = store.get_order(order["order_no"])
    assert paid is not None and paid.status == "PAID"
    assert paid.user_id == USER_A
    assert paid.buyer_id == "2088000000000001"
    summary = store.entitlement_summary(USER_A, paid.paid_at)
    assert summary["active"] is True
    expires = summary["expires_at"]

    assert _notify(client, order["order_no"], notify_id="notify-1").text == "success"
    assert _notify(client, order["order_no"], notify_id="notify-2").text == "success"
    after = store.entitlement_summary(USER_A, paid.paid_at)
    assert after["expires_at"] == expires
    assert store.entitlement_summary(USER_B, paid.paid_at)["active"] is False


def test_notify_rejects_bad_amount_or_signature(tmp_path: Path) -> None:
    client, gateway, store, _, _ = _build(tmp_path)
    order = _create(client)
    assert _notify(client, order["order_no"], total_amount="8.00").text == "fail"
    assert store.get_order(order["order_no"]).status == "PENDING"

    gateway.verify_result = False
    assert _notify(client, order["order_no"]).text == "fail"
    assert store.get_order(order["order_no"]).status == "PENDING"


def test_query_and_background_reconcile_confirm_without_browser(tmp_path: Path) -> None:
    client, gateway, store, billing, _ = _build(tmp_path)
    first = _create(client, request_id="query-1")
    gateway.query_result = {
        "code": "10000",
        "trade_status": "TRADE_SUCCESS",
        "total_amount": "9.00",
        "trade_no": "2026110622000000002",
        "buyer_user_id": "2088000000000002",
    }
    queried = client.post(
        f"/api/billing/orders/{first['order_no']}/query",
        headers=_headers(USER_A, write=True),
    )
    assert queried.status_code == 200, queried.text
    assert queried.json()["status"] == "PAID"
    assert store.entitlement_summary(USER_A, queried.json()["paid_at"])["active"] is True

    second = _create(client, request_id="reconcile-1", user=USER_B)
    changed = billing.reconcile_pending(min_age_seconds=0)
    assert changed == 1
    reconciled = store.get_order(second["order_no"])
    assert reconciled is not None and reconciled.status == "PAID"
    assert store.entitlement_summary(USER_B, reconciled.paid_at)["active"] is True


def test_refund_request_is_user_side_only_and_admin_refund_revokes(tmp_path: Path) -> None:
    client, gateway, store, _, _ = _build(tmp_path)
    order_no = _paid(client)
    assert (
        client.post(
            f"/api/billing/orders/{order_no}/refund-request",
            json={"reason": "不适用", "request_id": "refund-1"},
            headers=_headers(USER_B, write=True),
        ).status_code
        == 404
    )

    requested = client.post(
        f"/api/billing/orders/{order_no}/refund-request",
        json={"reason": "不适用", "request_id": "refund-1"},
        headers=_headers(USER_A, write=True),
    )
    assert requested.status_code == 200, requested.text
    request_no = requested.json()["out_request_no"]
    assert requested.json()["status"] == "REQUESTED"
    assert gateway.refund_calls == 0
    assert store.get_refund(request_no) is None

    refunded = client.post(
        f"/v1/orders/{order_no}/refund",
        json={"out_request_no": request_no, "reason": "运营确认"},
        headers={"X-Admin-Token": "admin-secret"},
    )
    assert refunded.status_code == 200, refunded.text
    assert gateway.refund_calls == 1
    assert refunded.json()["status"] == "SUCCESS"
    assert store.get_order(order_no).status == "REFUNDED"
    assert store.entitlement_summary(USER_A, "2999-01-01T00:00:00+00:00")["active"] is False
    request = store.get_refund_request(request_no)
    assert request is not None and request.status == "APPROVED"


def test_return_page_is_status_only_and_has_no_session_cookie(tmp_path: Path) -> None:
    client, gateway, _, _, _ = _build(tmp_path)
    order_no = _paid(client)
    parsed = urlsplit(gateway.last_return_url)
    path = parsed.path + (f"?{parsed.query}" if parsed.query else "")

    returned = client.get(path)
    assert returned.status_code == 200
    assert "支付成功" in returned.text
    assert "set-cookie" not in {key.lower() for key in returned.headers}

    invalid = client.get(f"/return/alipay?out_trade_no={order_no}&order_token=bad")
    assert "没有找到" in invalid.text
    assert (
        client.get(
            "/return/alipay/status",
            params={"out_trade_no": order_no, "order_token": "bad"},
        ).status_code
        == 404
    )


def test_admin_close_unpaid_order(tmp_path: Path) -> None:
    client, _, store, _, _ = _build(tmp_path)
    order = _create(client, request_id="close-1")
    closed = client.post(
        f"/v1/orders/{order['order_no']}/close",
        headers={"X-Admin-Token": "admin-secret"},
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "CLOSED"
    assert store.get_order(order["order_no"]).status == "CLOSED"


def test_relay_settings_use_unified_database_and_site_origin(tmp_path: Path) -> None:
    env = {
        "ALIPAY_APP_ID": APP_ID,
        "ALIPAY_APP_PRIVATE_KEY": "key",
        "ALIPAY_PUBLIC_KEY": "public",
        "ALIPAY_SELLER_ID": SELLER_ID,
        "RELAY_DB_PATH": str(tmp_path / "shared.sqlite3"),
        "RELAY_PUBLIC_BASE_URL": "https://clue.example",
        "RELAY_SITE_URL": "https://clue.example",
        "RELAY_ADMIN_TOKEN": "admin",
    }
    settings = load_settings(root=tmp_path, environ=env)
    assert settings.database_path == tmp_path / "shared.sqlite3"
    assert settings.public_base_url == "https://clue.example"
    assert settings.return_url == "https://clue.example/return/alipay"
    assert "https://clue.example" in settings.allowed_origins
    assert settings.reconcile_interval_seconds == 60.0
