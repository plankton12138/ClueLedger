"""支付宝 SDK 适配层。

业务层只依赖 ``AlipayGateway`` 协议；测试注入替身，运行期使用官方 Python SDK。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Protocol, cast

from alipay.aop.api.AlipayClientConfig import AlipayClientConfig  # type: ignore[import-untyped]
from alipay.aop.api.DefaultAlipayClient import (  # type: ignore[import-untyped]
    DefaultAlipayClient,
)
from alipay.aop.api.domain.AlipayTradeCloseModel import (  # type: ignore[import-untyped]
    AlipayTradeCloseModel,
)
from alipay.aop.api.domain.AlipayTradeFastpayRefundQueryModel import (  # type: ignore[import-untyped]
    AlipayTradeFastpayRefundQueryModel,
)
from alipay.aop.api.domain.AlipayTradePagePayModel import (  # type: ignore[import-untyped]
    AlipayTradePagePayModel,
)
from alipay.aop.api.domain.AlipayTradeQueryModel import (  # type: ignore[import-untyped]
    AlipayTradeQueryModel,
)
from alipay.aop.api.domain.AlipayTradeRefundModel import (  # type: ignore[import-untyped]
    AlipayTradeRefundModel,
)
from alipay.aop.api.domain.AlipayTradeWapPayModel import (  # type: ignore[import-untyped]
    AlipayTradeWapPayModel,
)
from alipay.aop.api.request.AlipayTradeCloseRequest import (  # type: ignore[import-untyped]
    AlipayTradeCloseRequest,
)
from alipay.aop.api.request.AlipayTradeFastpayRefundQueryRequest import (  # type: ignore[import-untyped]
    AlipayTradeFastpayRefundQueryRequest,
)
from alipay.aop.api.request.AlipayTradePagePayRequest import (  # type: ignore[import-untyped]
    AlipayTradePagePayRequest,
)
from alipay.aop.api.request.AlipayTradeQueryRequest import (  # type: ignore[import-untyped]
    AlipayTradeQueryRequest,
)
from alipay.aop.api.request.AlipayTradeRefundRequest import (  # type: ignore[import-untyped]
    AlipayTradeRefundRequest,
)
from alipay.aop.api.request.AlipayTradeWapPayRequest import (  # type: ignore[import-untyped]
    AlipayTradeWapPayRequest,
)
from alipay.aop.api.util.SignatureUtils import (  # type: ignore[import-untyped]
    get_sign_content,
    verify_with_rsa,
)

from hastings.relay.config import RelaySettings


class AlipayGatewayError(RuntimeError):
    """支付宝 SDK 调用失败或返回结构无法识别。"""


class AlipayGateway(Protocol):
    def pay_url(
        self,
        *,
        out_trade_no: str,
        amount: str,
        subject: str,
        return_url: str,
        notify_url: str,
    ) -> str: ...

    def query(self, out_trade_no: str) -> dict[str, Any]: ...

    def refund(
        self,
        *,
        out_trade_no: str,
        amount: str,
        out_request_no: str,
        reason: str,
    ) -> dict[str, Any]: ...

    def refund_query(self, *, out_trade_no: str, out_request_no: str) -> dict[str, Any]: ...

    def close(self, out_trade_no: str) -> dict[str, Any]: ...

    def verify_notify(self, params: Mapping[str, str]) -> bool: ...


def _response(raw: object, key: str) -> dict[str, Any]:
    payload: object = raw
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AlipayGatewayError("支付宝返回内容不是 JSON") from exc
    if not isinstance(payload, dict):
        raise AlipayGatewayError("支付宝返回结构无法识别")
    # 当前 SDK 的 execute() 已经剥掉 alipay_xxx_response 外层，直接返回内层 JSON；
    # 只有部分版本或原始报文才带包装键，因此两种形态都要认。
    value = payload.get(key)
    if isinstance(value, dict):
        return cast(dict[str, Any], value)
    error = payload.get("error_response")
    if isinstance(error, dict):
        return cast(dict[str, Any], error)
    return cast(dict[str, Any], payload)


class SdkAlipayGateway:
    def __init__(self, settings: RelaySettings) -> None:
        config = AlipayClientConfig()
        config.server_url = settings.gateway
        config.app_id = settings.app_id
        config.app_private_key = settings.private_key
        config.alipay_public_key = settings.alipay_public_key
        config.charset = "utf-8"
        config.sign_type = "RSA2"
        self._settings = settings
        self._client = DefaultAlipayClient(alipay_client_config=config)

    def pay_url(
        self,
        *,
        out_trade_no: str,
        amount: str,
        subject: str,
        return_url: str,
        notify_url: str,
    ) -> str:
        # 本应用开通的是 AI 网页应用收款，只授权 alipay.trade.page.pay；
        # 调用 wap.pay 会返回 insufficient-isv-permissions。沙箱则相反，
        # page.pay 收银台报 SYSTEM_ERROR，只能用 wap.pay。
        request: AlipayTradeWapPayRequest | AlipayTradePagePayRequest
        model: AlipayTradeWapPayModel | AlipayTradePagePayModel
        if self._settings.sandbox:
            request = AlipayTradeWapPayRequest()
            model = AlipayTradeWapPayModel()
            model.product_code = "QUICK_WAP_WAY"
        else:
            request = AlipayTradePagePayRequest()
            model = AlipayTradePagePayModel()
            model.product_code = "FAST_INSTANT_TRADE_PAY"
        request.return_url = return_url
        if notify_url:
            request.notify_url = notify_url
        model.out_trade_no = out_trade_no
        model.total_amount = amount
        model.subject = subject
        request.biz_model = model
        url = self._client.page_execute(request, "GET")
        if not isinstance(url, str) or not url.strip():
            raise AlipayGatewayError("支付宝未返回可用的支付地址")
        return url

    def query(self, out_trade_no: str) -> dict[str, Any]:
        request = AlipayTradeQueryRequest()
        model = AlipayTradeQueryModel()
        model.out_trade_no = out_trade_no
        request.biz_model = model
        return _response(self._client.execute(request), "alipay_trade_query_response")

    def refund(
        self,
        *,
        out_trade_no: str,
        amount: str,
        out_request_no: str,
        reason: str,
    ) -> dict[str, Any]:
        request = AlipayTradeRefundRequest()
        model = AlipayTradeRefundModel()
        model.out_trade_no = out_trade_no
        model.refund_amount = amount
        model.out_request_no = out_request_no
        if reason:
            model.refund_reason = reason
        request.biz_model = model
        return _response(self._client.execute(request), "alipay_trade_refund_response")

    def refund_query(self, *, out_trade_no: str, out_request_no: str) -> dict[str, Any]:
        request = AlipayTradeFastpayRefundQueryRequest()
        model = AlipayTradeFastpayRefundQueryModel()
        model.out_trade_no = out_trade_no
        model.out_request_no = out_request_no
        request.biz_model = model
        return _response(
            self._client.execute(request),
            "alipay_trade_fastpay_refund_query_response",
        )

    def close(self, out_trade_no: str) -> dict[str, Any]:
        request = AlipayTradeCloseRequest()
        model = AlipayTradeCloseModel()
        model.out_trade_no = out_trade_no
        request.biz_model = model
        return _response(self._client.execute(request), "alipay_trade_close_response")

    def verify_notify(self, params: Mapping[str, str]) -> bool:
        signature = params.get("sign", "")
        if not signature:
            return False
        payload = {key: value for key, value in params.items() if key not in {"sign", "sign_type"}}
        content = get_sign_content(payload)
        try:
            return bool(
                verify_with_rsa(
                    self._settings.alipay_public_key,
                    content.encode("utf-8"),
                    signature,
                )
            )
        except Exception:
            return False
