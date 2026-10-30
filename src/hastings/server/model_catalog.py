"""内置模型容量目录（0.9 U4）。

目录只回答一个问题：常见模型在作者没有手填容量时，窗口与输入预算能不能直接算出来。
它不是模型能力大全，也不在运行期联网刷新；每条数字都来自供应商公开文档，只收录
核对过的模型。不确定的模型宁可缺席，让界面如实显示「容量未知」。

两条边界：

- **按供应商预设 + 精确模型 ID 匹配。** ``deepseek-chat`` 的容量不会顺带给
  ``deepseek-chat-v2``；同一个模型经自定义中转接入时不套用原厂容量，因为中转可能
  改写窗口。所以只有官方的供应商预设才查这张表。
- **目录只补空缺字段。** 作者的手工容量、服务商在模型列表里返回的有效容量都排在
  目录前面；目录不会覆盖任何已填的值。

供应商调整窗口后更新这里的数字，不按名称前缀推断。
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["CatalogEntry", "catalog_capacity"]


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """一个模型的容量事实；未收录的项保持 ``None``。"""

    context_window_tokens: int
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None


#: 供应商预设 ID → 精确模型 ID → 容量。
#:
#: Google 只公布输入上限与输出上限；两者相加得到窗口，输入上限单独保留，
#: 因此输入预算不会被输出预留误伤。
#:
#: 每条数字都注明来源与核对日期。价格页用「1M / 384K」表达规模，目录取接口
#: 示例里的精确整数；输出上限是模型能力，不代表本项目要请求这么长的回复。
CATALOG: dict[str, dict[str, CatalogEntry]] = {
    "deepseek": {
        "deepseek-chat": CatalogEntry(131_072, max_output_tokens=8_192),
        "deepseek-reasoner": CatalogEntry(131_072, max_output_tokens=65_536),
        # https://api-docs.deepseek.com/zh-cn/api/list-models/  核对日期 2026-10-30
        "deepseek-flash": CatalogEntry(1_048_576, max_output_tokens=393_216),
        "deepseek-v4-pro": CatalogEntry(1_048_576, max_output_tokens=393_216),
    },
    "openai": {
        "gpt-5": CatalogEntry(400_000, max_output_tokens=128_000),
        "gpt-5-mini": CatalogEntry(400_000, max_output_tokens=128_000),
        "gpt-5-nano": CatalogEntry(400_000, max_output_tokens=128_000),
        "gpt-4.1": CatalogEntry(1_047_576, max_output_tokens=32_768),
        "gpt-4.1-mini": CatalogEntry(1_047_576, max_output_tokens=32_768),
        "gpt-4.1-nano": CatalogEntry(1_047_576, max_output_tokens=32_768),
        "gpt-4o": CatalogEntry(128_000, max_output_tokens=16_384),
        "gpt-4o-mini": CatalogEntry(128_000, max_output_tokens=16_384),
        "o3": CatalogEntry(200_000, max_output_tokens=100_000),
    },
    "anthropic": {
        "claude-sonnet-4-5": CatalogEntry(200_000, max_output_tokens=64_000),
        "claude-haiku-4-5": CatalogEntry(200_000, max_output_tokens=64_000),
        "claude-opus-4-1": CatalogEntry(200_000, max_output_tokens=32_000),
        "claude-3-5-sonnet": CatalogEntry(200_000, max_output_tokens=8_192),
        "claude-3-5-haiku": CatalogEntry(200_000, max_output_tokens=8_192),
    },
    "google": {
        "gemini-2.5-pro": CatalogEntry(
            1_114_112, max_input_tokens=1_048_576, max_output_tokens=65_536
        ),
        "gemini-2.5-flash": CatalogEntry(
            1_114_112, max_input_tokens=1_048_576, max_output_tokens=65_536
        ),
        "gemini-2.0-flash": CatalogEntry(
            1_056_768, max_input_tokens=1_048_576, max_output_tokens=8_192
        ),
    },
}


def catalog_capacity(catalog_id: str, model: str) -> CatalogEntry | None:
    """按预设与精确模型名取目录条目；未收录时返回 ``None``。"""
    return CATALOG.get(catalog_id, {}).get(model)
