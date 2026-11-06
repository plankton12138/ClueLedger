"""线簿订阅支付服务。

独立的常驻服务，持有支付宝应用密钥、订阅订单与权益数据。写作服务与浏览器
通过同域 Cookie 访问 ``/api/billing/*``；模型密钥和支付密钥都不下发客户端。
"""

from __future__ import annotations

from hastings.relay.app import create_relay_app
from hastings.relay.config import RelayConfigError, RelaySettings, load_settings

__all__ = ["RelayConfigError", "RelaySettings", "create_relay_app", "load_settings"]
