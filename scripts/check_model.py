"""用真实模型核对「模型来源」是否可用（0.4 V04-9）。

用法：

    uv run python scripts/check_model.py
    uv run python scripts/check_model.py --config 其他目录/providers.yaml --model deepseek-chat

做的事只有一件：按产品同一条装配路径（config.yaml → 系统凭据存储 → 适配器）取当前
连接与模型，发一句最短的请求，把连接名、模型、key 尾四位、回复与用量打到终端。
失败时给三要素说明并以退出码 1 结束——这样「模型到底通不通」不必启动整个写作台、
也不必写一章小说才能看出来。

不读进程环境：key 只来自 config.yaml（直填）或系统凭据存储（credential_ref）。
本轮实例用完即关闭，与写作轮次的处理一致。
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from hastings.infrastructure.credentials import build_credential_store
from hastings.server.provider_config import (
    DEFAULT_CONFIG_PATH,
    ProviderBook,
    ProviderConfigError,
    load_server_config,
    mask_key,
)
from hastings.server.provider_runtime import ModelSnapshot
from tau_agent.messages import UserMessage
from tau_ai.events import AssistantDoneEvent, AssistantErrorEvent, TextDeltaEvent

PROMPT = "用一句中文回答：你好。"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="check-model", description="核对模型来源是否可用")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="模型来源配置")
    parser.add_argument("--model", default="", help="覆盖当前连接使用的模型名")
    return parser


async def ask(snapshot: ModelSnapshot) -> tuple[str, str]:
    """发一句请求；返回（回复文本, 用量描述）。厂商报错按异常抛出。"""
    text: list[str] = []
    usage = ""
    try:
        async for event in snapshot.provider.stream_response(
            model=snapshot.model,
            system="你是一个简洁的助手，只回一句话。",
            messages=[UserMessage(content=PROMPT)],
            tools=[],
        ):
            if isinstance(event, TextDeltaEvent):
                text.append(event.delta)
            elif isinstance(event, AssistantDoneEvent):
                usage = str(event.message.usage)
            elif isinstance(event, AssistantErrorEvent):
                raise ProviderConfigError(
                    f"模型调用失败：{event.error.error_message or '厂商没有返回可用回复'}。"
                    "为什么错：端点、key 或模型名与厂商不匹配，或网络到不了该端点。"
                    "怎样算对：核对 config.yaml 里该连接的 base_url、api_key 与模型名。"
                )
    finally:
        await snapshot.aclose()
    return "".join(text).strip(), usage


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_server_config(args.config)
        if not config.providers:
            raise ProviderConfigError(
                f"没有可用的模型连接：{args.config} 不存在或没有 providers。"
                "为什么错：模型来源只从这个文件读取。"
                "怎样算对：在写作台的「模型设置」里添加一个连接，"
                "或参照 config.example.yaml 手写一个（name / base_url / api_key / 模型）。"
            )
        available = next((item for item in config.providers if item.available), None)
        boot = available or config.providers[0]
        book = ProviderBook(
            config,
            boot_name=boot.name,
            boot_model=args.model or boot.default_model,
            config_path=args.config,
            credentials=build_credential_store(),
            cli_model=args.model,
        )
        selection = book.selection()
        profile = (
            book.profile(selection.connection_id) if selection.connection_id is not None else None
        )
        if profile is None or not selection.available:
            raise ProviderConfigError(
                f"连接不可用：{selection.connection_id or '（未选择）'}。"
                f"为什么错：{selection.reason or '这条连接没有可用的 key 或模型'}。"
                "怎样算对：在「模型设置」里填好 API Key 与模型，"
                "或把 key 写进 config.yaml 的 api_key（保存一次即转入系统凭据存储）。"
            )
        print(f"连接：{profile.title}（{profile.name}）｜端点：{profile.base_url}")
        print(
            f"协议：{profile.api}｜模型：{selection.model}｜"
            f"key：{mask_key(profile.resolved_api_key())}"
        )
        reply, usage = asyncio.run(ask(book.snapshot(worker=False)))
        print(f"回复：{reply or '（空）'}")
        if usage:
            print(f"用量：{usage}")
    except ProviderConfigError as exc:
        print(exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
