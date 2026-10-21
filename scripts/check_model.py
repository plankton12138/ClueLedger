"""用真实模型核对「模型来源」是否可用（0.4）。

用法：

    uv run python scripts/check_model.py
    uv run python scripts/check_model.py --config 其他目录/providers.yaml --model deepseek-chat

做的事只有一件：按产品同一条装配路径（config.yaml → 系统凭据存储 → provider）取当前
档案与模型，发一句最短的请求，把档案名、模型、key 尾四位、回复与用量打到终端。
失败时给三要素说明并以退出码 1 结束——这样「模型到底通不通」不必启动整个写作台、
也不必写一章小说才能看出来。

不读进程环境：key 只来自 config.yaml（直填）或系统凭据存储（credential_ref）。
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
from tau_agent.messages import UserMessage
from tau_agent.provider_events import AssistantDoneEvent, AssistantErrorEvent, TextDeltaEvent

PROMPT = "用一句中文回答：你好。"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="check-model", description="核对模型来源是否可用")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="模型来源配置")
    parser.add_argument("--model", default="", help="覆盖启动档案的模型名")
    return parser


async def ask(book: ProviderBook, profile_name: str, model: str) -> tuple[str, str]:
    """发一句请求；返回（回复文本, 用量描述）。厂商报错按异常抛出。"""
    provider = book.provider(profile_name)
    text: list[str] = []
    usage = ""
    async for event in provider.stream_response(
        model=model,
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
                "怎样算对：核对 config.yaml 里该档案的 base_url、api_key 与模型名。"
            )
    return "".join(text).strip(), usage


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_server_config(args.config)
        available = next((item for item in config.providers if item.available), None)
        boot = available or (config.providers[0] if config.providers else None)
        if boot is None:
            raise ProviderConfigError(
                f"没有可用的模型档案：{args.config} 不存在或没有 providers。"
                "为什么错：模型来源只从这个文件读取。"
                "怎样算对：参照 config.example.yaml 写一个档案（name / base_url / api_key / "
                "default_model），再运行本脚本。"
            )
        book = ProviderBook(
            config,
            boot_name=boot.name,
            boot_model=args.model or boot.default_model,
            config_path=args.config,
            credentials=build_credential_store(),
        )
        profile, model = book.selected()
        if profile is None or not profile.available or not model:
            raise ProviderConfigError(
                f"档案不可用：{boot.name}。"
                "为什么错：这个档案既没有 key，也没有可用凭据。"
                "怎样算对：把 key 写进 api_key（保存一次即可转入系统凭据存储），"
                "并填好 default_model。"
            )
        print(f"档案：{profile.name}｜端点：{profile.base_url}")
        print(f"模型：{model}｜key：{mask_key(profile.resolved_api_key())}")
        reply, usage = asyncio.run(ask(book, profile.name, model))
        print(f"回复：{reply or '（空）'}")
        if usage:
            print(f"用量：{usage}")
    except ProviderConfigError as exc:
        print(exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
