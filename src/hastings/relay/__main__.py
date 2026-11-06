"""``python -m hastings.relay`` 启动订阅支付服务。"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence

import uvicorn

from hastings.logging import configure_logging
from hastings.relay.app import create_relay_app
from hastings.relay.config import RelayConfigError, load_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hastings-relay",
        description="线簿订阅支付服务（支付宝 AI 网页应用收款）",
    )
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认回环，由反向代理对外）")
    parser.add_argument("--port", type=int, default=8300, help="监听端口（默认 8300）")
    parser.add_argument(
        "--production",
        action="store_true",
        default=os.environ.get("RELAY_ENV", "").strip() == "production",
        help="生产校验：拒绝沙箱、要求 HTTPS、显式数据库与内部密钥",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(
        json_mode=args.production
        or os.environ.get("RELAY_LOG_JSON", "").strip() in {"1", "true", "yes"}
    )
    try:
        settings = load_settings(production=args.production)
        app = create_relay_app(settings=settings)
    except RelayConfigError as exc:
        print(exc)
        return 1
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
