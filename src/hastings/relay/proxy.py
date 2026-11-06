"""订阅模型代理：先预留、再调用上游、最后按真实 usage 结算。

代理只接受写作服务用短期授权发起的 OpenAI 兼容请求；平台模型密钥不出本进程。
流式中断或上游没有返回 usage 时进入待核对状态，由恢复任务在有限时间内释放。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi.responses import JSONResponse, Response, StreamingResponse

from hastings.relay.config import RelaySettings
from hastings.relay.internal_auth import Grant
from hastings.relay.metering import (
    ModelRate,
    actual_micro,
    estimate_input_tokens,
    extract_usage,
    rate_for,
    request_max_output,
    reservation_micro,
)
from hastings.relay.store import (
    ConcurrencyExceeded,
    QuotaExceeded,
    RelayStore,
    Reservation,
    utc_now,
)

logger = logging.getLogger("hastings.relay.proxy")


@dataclass(frozen=True, slots=True)
class ProxyError(Exception):
    status_code: int
    code: str
    message: str


def _openai_error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "message": f"[{code}] {message}",
                "type": "billing_error",
                "code": code,
            }
        },
    )


class ModelProxy:
    def __init__(
        self,
        store: RelayStore,
        settings: RelaySettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        now: Callable[[], str] = utc_now,
    ) -> None:
        self.store = store
        self.settings = settings
        self._transport = transport
        self._now = now

    async def completions(self, body: dict[str, Any], grant: Grant) -> Response:
        if not self.settings.platform_enabled:
            raise ProxyError(
                503,
                "BILLING_PLATFORM_DISABLED",
                "线簿订阅模型尚未配置，请联系维护者。",
            )
        if not isinstance(body, dict):
            raise ProxyError(400, "BILLING_BODY_INVALID", "请求体必须是 JSON 对象。")
        model = str(body.get("model", ""))
        rate = rate_for(model, self.settings.platform_models)
        if rate is None:
            raise ProxyError(400, "BILLING_MODEL_UNKNOWN", f"不支持的订阅模型：{model}")
        try:
            max_output = request_max_output(
                body,
                default_tokens=self.settings.platform_default_output_tokens,
                rate=rate,
            )
        except ValueError as exc:
            raise ProxyError(400, "BILLING_OUTPUT_INVALID", str(exc)) from exc
        input_tokens = estimate_input_tokens(body)
        reserved = reservation_micro(rate, input_tokens=input_tokens, max_output_tokens=max_output)
        try:
            reservation = self.store.create_reservation(
                user_id=grant.user_id,
                workspace_id="",
                request_id=grant.jti,
                model=model,
                reserved_micro=reserved,
                ttl_seconds=self.settings.reservation_ttl_seconds,
                max_concurrent=self.settings.max_concurrent_requests,
                now=self._now(),
            )
        except QuotaExceeded as exc:
            raise ProxyError(
                402,
                "BILLING_QUOTA_EXHAUSTED",
                "当前订阅额度不足；可以等待额度恢复或改用自带模型连接。",
            ) from exc
        except ConcurrencyExceeded as exc:
            raise ProxyError(
                429,
                "BILLING_CONCURRENCY_LIMIT",
                "当前账号同时进行的订阅请求已达上限，请等待上一轮结束后重试。",
            ) from exc
        if reservation.status != "reserved":
            raise ProxyError(
                409,
                "BILLING_REQUEST_REPLAYED",
                "这次请求已经结束；重新发起一轮写作后再试。",
            )
        stream = bool(body.get("stream"))
        upstream_body = dict(body)
        upstream_body["max_tokens"] = max_output
        upstream_body.pop("max_completion_tokens", None)
        if stream:
            options = upstream_body.get("stream_options")
            options = dict(options) if isinstance(options, dict) else {}
            options["include_usage"] = True
            upstream_body["stream_options"] = options
        headers = {
            "Authorization": f"Bearer {self.settings.platform_api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.settings.platform_base_url}/chat/completions"
        timeout = httpx.Timeout(connect=20.0, read=600.0, write=30.0, pool=20.0)
        if stream:
            return StreamingResponse(
                self._stream(url, headers, upstream_body, reservation, rate),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        return await self._non_stream(url, headers, upstream_body, reservation, rate, timeout)

    async def _non_stream(
        self,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any],
        reservation: Reservation,
        rate: ModelRate,
        timeout: httpx.Timeout,
    ) -> Response:
        try:
            async with httpx.AsyncClient(
                timeout=timeout, transport=self._transport, trust_env=False
            ) as client:
                response = await client.post(url, headers=headers, json=body)
        except Exception as exc:
            self.store.release_reservation(
                reservation.id, error=type(exc).__name__, now=self._now()
            )
            raise ProxyError(502, "BILLING_UPSTREAM_UNAVAILABLE", "订阅模型暂时不可用。") from exc
        if response.status_code >= 400:
            self.store.release_reservation(
                reservation.id, error=f"upstream:{response.status_code}", now=self._now()
            )
            return _openai_error(
                response.status_code,
                "BILLING_UPSTREAM_ERROR",
                "订阅模型返回了错误。",
            )
        try:
            payload = response.json()
        except ValueError as exc:
            self.store.mark_reservation_review(
                reservation.id,
                error="invalid_json",
                review_ttl_seconds=self.settings.review_ttl_seconds,
                now=self._now(),
            )
            raise ProxyError(502, "BILLING_UPSTREAM_INVALID", "订阅模型返回了无效响应。") from exc
        usage = extract_usage(payload)
        if usage is None:
            self.store.mark_reservation_review(
                reservation.id,
                error="usage_missing",
                review_ttl_seconds=self.settings.review_ttl_seconds,
                now=self._now(),
            )
        else:
            input_count, cached, output = usage
            self.store.settle_reservation(
                reservation.id,
                input_tokens=input_count,
                cached_tokens=cached,
                output_tokens=output,
                settled_micro=actual_micro(
                    rate,
                    input_tokens=input_count,
                    cached_tokens=cached,
                    output_tokens=output,
                ),
                now=self._now(),
            )
        return JSONResponse(status_code=response.status_code, content=payload)

    async def _stream(
        self,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any],
        reservation: Reservation,
        rate: ModelRate,
    ) -> AsyncIterator[bytes]:
        usage: tuple[int, int, int] | None = None
        released = False
        buffer = ""
        try:
            async with (
                httpx.AsyncClient(
                    timeout=httpx.Timeout(connect=20.0, read=600.0, write=30.0, pool=20.0),
                    transport=self._transport,
                    trust_env=False,
                ) as client,
                client.stream("POST", url, headers=headers, json=body) as response,
            ):
                if response.status_code >= 400:
                    await response.aread()
                    self.store.release_reservation(
                        reservation.id,
                        error=f"upstream:{response.status_code}",
                        now=self._now(),
                    )
                    released = True
                    yield _sse_error("BILLING_UPSTREAM_ERROR", "订阅模型返回了错误。")
                    return
                async for chunk in response.aiter_bytes():
                    yield chunk
                    buffer += chunk.decode("utf-8", "replace")
                    while "\n\n" in buffer:
                        frame, buffer = buffer.split("\n\n", 1)
                        data = "\n".join(
                            line[5:].strip()
                            for line in frame.splitlines()
                            if line.startswith("data:")
                        )
                        if not data or data == "[DONE]":
                            continue
                        try:
                            payload = json.loads(data)
                        except ValueError:
                            continue
                        found = extract_usage(payload)
                        if found is not None:
                            usage = found
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("subscription upstream stream failed")
            if usage is None:
                self.store.mark_reservation_review(
                    reservation.id,
                    error="stream_failed",
                    review_ttl_seconds=self.settings.review_ttl_seconds,
                    now=self._now(),
                )
            return
        finally:
            if not released:
                if usage is None:
                    self.store.mark_reservation_review(
                        reservation.id,
                        error="usage_missing",
                        review_ttl_seconds=self.settings.review_ttl_seconds,
                        now=self._now(),
                    )
                else:
                    input_count, cached, output = usage
                    self.store.settle_reservation(
                        reservation.id,
                        input_tokens=input_count,
                        cached_tokens=cached,
                        output_tokens=output,
                        settled_micro=actual_micro(
                            rate,
                            input_tokens=input_count,
                            cached_tokens=cached,
                            output_tokens=output,
                        ),
                        now=self._now(),
                    )


def _sse_error(code: str, message: str) -> bytes:
    payload = json.dumps(
        {"error": {"message": f"[{code}] {message}", "type": "billing_error", "code": code}},
        ensure_ascii=False,
    )
    return f"data: {payload}\n\n".encode()
