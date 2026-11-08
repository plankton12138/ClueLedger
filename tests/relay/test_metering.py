"""v1.4 P4：订阅代理的预留、流式结算、额度与恢复。"""

from __future__ import annotations

import json
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from hastings.accounts.db import AccountDatabase
from hastings.accounts.store import AccountsStore
from hastings.accounts.tokens import hash_token
from hastings.relay.app import create_relay_app
from hastings.relay.config import RelaySettings
from hastings.relay.internal_auth import mint_grant
from hastings.relay.metering import ModelRate, actual_micro, estimate_input_tokens
from hastings.relay.plans import get_plan
from hastings.relay.proxy import ModelProxy
from hastings.relay.store import RelayStore, utc_now

USER = "usr_metering0000000000000000000000"
SECRET = "internal-secret"
MODEL = "test-model"


class DummyGateway:
    def verify_notify(self, params: dict[str, str]) -> bool:
        return False


def _settings(tmp_path: Path) -> RelaySettings:
    return RelaySettings(
        app_id="app",
        private_key="key",
        alipay_public_key="public",
        gateway="https://sandbox.example/gateway.do",
        seller_id="seller",
        notify_url="",
        return_url="http://testserver/return/alipay",
        database_path=tmp_path / "shared.sqlite3",
        admin_token="admin",
        allowed_origins=("http://testserver",),
        sandbox=True,
        public_base_url="http://testserver",
        site_url="http://testserver",
        cookie_name="clue_session",
        reconcile_interval_seconds=0,
        platform_base_url="https://upstream.example/v1",
        platform_api_key="platform-key",
        platform_models=(ModelRate(MODEL, Decimal("1.0"), 10_000),),
        platform_default_output_tokens=1024,
        internal_secret=SECRET,
    )


def _build(tmp_path: Path, handler: Any) -> tuple[TestClient, RelayStore, ModelProxy]:
    settings = _settings(tmp_path)
    database = AccountDatabase(settings.database_path)
    database.initialize()
    accounts = AccountsStore(database)
    now = int(time.time())
    accounts.create_account(
        user_id=USER,
        email="m@example.com",
        email_norm="m@example.com",
        password_hash="x",
        now=now,
    )
    accounts.create_session(
        token_hash=hash_token("cookie"),
        user_id=USER,
        csrf_token="csrf",
        now=now,
        expires_at=now + 3600,
    )
    store = RelayStore(database)
    store.initialize()
    plan = get_plan("experience")
    assert plan is not None
    order = store.create_order(
        out_trade_no="CLmetering",
        user_id=USER,
        request_id="order-1",
        plan=plan,
        subject="metering",
        now=utc_now(),
    )
    store.mark_paid(
        order.out_trade_no,
        trade_no="TRADE",
        buyer_id="buyer",
        raw="{}",
        now=utc_now(),
    )
    proxy = ModelProxy(
        store,
        settings,
        transport=httpx.MockTransport(handler),
        now=utc_now,
    )
    app = create_relay_app(settings=settings, store=store, gateway=DummyGateway(), proxy=proxy)
    return TestClient(app), store, proxy


def _grant(now: int | None = None) -> str:
    return mint_grant(USER, SECRET, ttl_seconds=900, now=now)


def _chat(client: TestClient, grant: str, body: dict[str, Any]) -> Any:
    return client.post(
        "/v1/chat/completions",
        json=body,
        headers={"Authorization": f"Bearer {grant}"},
    )


def _body(*, stream: bool = False, max_tokens: int = 100) -> dict[str, Any]:
    return {
        "model": MODEL,
        "messages": [{"role": "user", "content": "写一段测试"}],
        "max_tokens": max_tokens,
        "stream": stream,
    }


def test_non_stream_settles_actual_usage_and_is_idempotent(tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append(payload)
        assert request.headers["authorization"] == "Bearer platform-key"
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "好"}}],
                "usage": {
                    "prompt_tokens": 100,
                    "prompt_cache_hit_tokens": 20,
                    "completion_tokens": 50,
                },
            },
        )

    client, store, _ = _build(tmp_path, handler)
    grant = _grant()
    response = _chat(client, grant, _body())
    assert response.status_code == 200, response.text
    assert calls[0]["max_tokens"] == 100

    expected = actual_micro(
        ModelRate(MODEL, Decimal("1.0"), 10_000),
        input_tokens=100,
        cached_tokens=20,
        output_tokens=50,
    )
    reservation = store.reservation_by_request(USER, _grant_jti(grant))
    assert reservation is not None
    assert reservation.status == "settled"
    assert reservation.settled_micro == expected
    summary = store.entitlement_summary(USER, utc_now())
    assert summary["active"] is True
    assert summary["total_used"] == round(expected / 1_000_000, 3)
    assert summary["total_used"] > 0

    replay = _chat(client, grant, _body())
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "BILLING_REQUEST_REPLAYED"


def test_stream_settles_from_final_usage_chunk(tmp_path: Path) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        content = (
            'data: {"choices":[{"delta":{"content":"你"}}]}\n\n'
            'data: {"choices":[],"usage":{"prompt_tokens":120,"completion_tokens":30}}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(
            200, content=content.encode(), headers={"content-type": "text/event-stream"}
        )

    client, store, _ = _build(tmp_path, handler)
    grant = _grant()
    response = _chat(client, grant, _body(stream=True))
    assert response.status_code == 200, response.text
    assert "你" in response.text
    reservation = store.reservation_by_request(USER, _grant_jti(grant))
    assert reservation is not None
    assert reservation.status == "settled"
    assert reservation.input_tokens == 120
    assert reservation.settled_micro == actual_micro(
        ModelRate(MODEL, Decimal("1.0"), 10_000),
        input_tokens=120,
        cached_tokens=0,
        output_tokens=30,
    )


def test_missing_usage_enters_review_and_recovery_releases(tmp_path: Path) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client, store, _ = _build(tmp_path, handler)
    grant = _grant()
    assert _chat(client, grant, _body()).status_code == 200
    reservation = store.reservation_by_request(USER, _grant_jti(grant))
    assert reservation is not None and reservation.status == "review"
    assert store.recover_usage_reservations("2999-01-01T00:00:00+00:00") == 1
    assert store.get_reservation(reservation.id).status == "released"


def test_quota_and_concurrency_are_enforced_before_upstream(tmp_path: Path) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    client, store, _ = _build(tmp_path, handler)
    # 体验卡总额 100 点；构造一个超过总额的输入，必须在下游调用前拒绝。
    huge = _body()
    huge["messages"] = [{"role": "user", "content": "字" * 600_000}]
    too_big = _chat(client, _grant(), huge)
    assert too_big.status_code == 402
    assert too_big.json()["error"]["code"] == "BILLING_QUOTA_EXHAUSTED"

    first = _grant()
    second = _grant()
    assert _chat(client, first, _body()).status_code == 200
    # 第一条已经结算，不会被当成仍在运行；手工占用一个名额再验证并发上限。
    store.create_reservation(
        user_id=USER,
        workspace_id="",
        request_id="manual-1",
        model=MODEL,
        reserved_micro=1,
        ttl_seconds=600,
        max_concurrent=2,
        now=utc_now(),
    )
    store.create_reservation(
        user_id=USER,
        workspace_id="",
        request_id="manual-2",
        model=MODEL,
        reserved_micro=1,
        ttl_seconds=600,
        max_concurrent=2,
        now=utc_now(),
    )
    third = _chat(client, second, _body())
    assert third.status_code == 429
    assert third.json()["error"]["code"] == "BILLING_CONCURRENCY_LIMIT"


def test_refunded_entitlement_blocks_proxy(tmp_path: Path) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    client, store, _ = _build(tmp_path, handler)
    # 全额退款撤销权益；之后代理必须在调用上游前返回额度错误。
    store.create_refund(
        out_request_no="refund-1",
        out_trade_no="CLmetering",
        amount_cents=300,
        now=utc_now(),
    )
    store.mark_refund_result(
        "refund-1",
        status="SUCCESS",
        fund_change="Y",
        raw="{}",
        now=utc_now(),
    )
    result = _chat(client, _grant(), _body())
    assert result.status_code == 402
    assert result.json()["error"]["code"] == "BILLING_QUOTA_EXHAUSTED"


def test_invalid_grant_is_rejected(tmp_path: Path) -> None:
    client, _, _ = _build(tmp_path, lambda _: httpx.Response(200, json={"usage": {}}))
    result = _chat(client, "v1:forged", _body())
    assert result.status_code == 401
    assert result.json()["error"]["code"] == "BILLING_GRANT_INVALID"


def test_token_estimate_and_rates() -> None:
    body = {"messages": [{"role": "user", "content": "hello 世界"}], "tools": []}
    assert estimate_input_tokens(body) > 10
    flash = ModelRate("flash", Decimal("0.30"), 1000)
    pro = ModelRate("pro", Decimal("1.0"), 1000)
    assert actual_micro(flash, input_tokens=1000, cached_tokens=0, output_tokens=1000) == int(
        actual_micro(pro, input_tokens=1000, cached_tokens=0, output_tokens=1000) * 0.3
    )


def _grant_jti(grant: str) -> str:
    return grant.split(":")[3]


def test_renewal_keeps_the_existing_daily_window_anchor(tmp_path: Path) -> None:
    _, store, _ = _build(tmp_path, lambda _: httpx.Response(200, json={"usage": {}}))
    first = store.current_entitlement(USER, utc_now())
    assert first is not None
    plan = get_plan("standard")
    assert plan is not None
    order = store.create_order(
        out_trade_no="CLrenew",
        user_id=USER,
        request_id="renew-1",
        plan=plan,
        subject="renew",
        now=utc_now(),
    )
    store.mark_paid(
        order.out_trade_no,
        trade_no="RENEW",
        buyer_id="buyer",
        raw="{}",
        now=utc_now(),
    )
    second = store.current_entitlement(USER, utc_now())
    assert second is not None
    assert second.plan == "standard"
    assert second.starts_at == first.starts_at


def test_settlement_is_idempotent_and_uses_the_reservation_window(tmp_path: Path) -> None:
    client, store, _ = _build(tmp_path, lambda _: httpx.Response(200, json={"usage": {}}))
    reservation = store.create_reservation(
        user_id=USER,
        workspace_id="workspace-a",
        request_id="settle-once",
        model=MODEL,
        reserved_micro=5_000_000,
        ttl_seconds=600,
        max_concurrent=2,
        now=utc_now(),
    )
    store.settle_reservation(
        reservation.id,
        input_tokens=10,
        cached_tokens=2,
        output_tokens=10,
        settled_micro=1_000_000,
        now=utc_now(),
    )
    store.settle_reservation(
        reservation.id,
        input_tokens=10,
        cached_tokens=2,
        output_tokens=10,
        settled_micro=1_000_000,
        now=utc_now(),
    )
    with store._read() as connection:
        ledger = connection.execute(
            "SELECT COUNT(*) AS count FROM usage_ledger WHERE user_id = ? AND request_id = ?",
            (USER, "settle-once"),
        ).fetchone()
        window = connection.execute(
            "SELECT points_used FROM usage_windows WHERE user_id = ? AND window_start = ?",
            (USER, reservation.window_start),
        ).fetchone()
    assert ledger is not None and int(ledger["count"]) == 1
    assert window is not None and int(window["points_used"]) == 1_000_000


def test_cross_window_settlement_charges_original_window(tmp_path: Path) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"usage": {"prompt_tokens": 10, "completion_tokens": 10}})

    client, store, _ = _build(tmp_path, handler)
    grant = _grant()
    assert _chat(client, grant, _body()).status_code == 200
    reservation = store.reservation_by_request(USER, _grant_jti(grant))
    assert reservation is not None
    with store._read() as connection:
        row = connection.execute(
            "SELECT window_start FROM usage_reservations WHERE id = ?",
            (reservation.id,),
        ).fetchone()
        charged = connection.execute(
            "SELECT points_used FROM usage_windows WHERE user_id = ? AND window_start = ?",
            (USER, str(row["window_start"])),
        ).fetchone()
    assert charged is not None and int(charged["points_used"]) == reservation.settled_micro


@pytest.mark.parametrize("value", [0, -1])
def test_invalid_output_budget_is_rejected(tmp_path: Path, value: int) -> None:
    client, _, _ = _build(tmp_path, lambda _: httpx.Response(200, json={"usage": {}}))
    result = _chat(client, _grant(), _body(max_tokens=value))
    assert result.status_code == 400
    assert result.json()["error"]["code"] == "BILLING_OUTPUT_INVALID"
