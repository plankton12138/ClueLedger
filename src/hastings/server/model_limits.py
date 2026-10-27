"""按精确模型解析容量与输入预算（0.9 U1）。

容量回答的是「这一轮最多能带多少输入」，它必须同时服务于两个地方：界面上的占比，
和真正发出请求之前的预算检查。两处各算一套，就会出现「界面说还能写、请求被拒」。

四条取舍：

- **不猜窗口。** 模型发现列表只有名字，名称前缀相同也不代表容量相同。服务商未返回且没有
  目录条目时就是未知，不填 128K 之类的默认值；未知时界面不显示百分比，自动压缩停用。
- **来源有优先级。** 服务商在模型列表里返回的有效容量 →
  内置模型目录 → 未知。逐字段补齐：服务商未返回的字段可由目录补上。
- **回复预留对齐实际请求。** 写作用途的回复上限是运行时固定的 4096，预算中的
  回复预留与它保持一致。显示 8K 而实际只允许 4K，会让占比看起来比真实情况宽松。
- **按精确模型名匹配。** 配置以模型名为键，`deepseek-chat` 的容量不会顺带给
  `deepseek-chat-v2`；自定义中转也不套用原厂目录，因为中转可能改写窗口。

``revision`` 把容量事实压成一个短标识，供估算指纹使用：改容量即换指纹，旧估算失效。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from hastings.server.model_catalog import catalog_capacity
from hastings.server.provider_catalog import ProviderApi

__all__ = [
    "WORKER_OUTPUT_TOKENS",
    "ModelCapacity",
    "ModelLimitOverride",
    "ModelLimits",
    "resolve_model_limits",
]

#: 写作用途的回复上限；与 provider_runtime 构造 provider 时实际发送的值一致。
WORKER_OUTPUT_TOKENS = 4_096

#: 未配置安全余量时按窗口的 5% 留白，上限与回复预留相同，避免小窗口被余量吃掉太多。
_SAFETY_SHARE = 20
_MAX_AUTO_SAFETY = 4_096

_CAPACITY_FIELDS = (
    "context_window_tokens",
    "max_input_tokens",
    "max_output_tokens",
    "output_reserve_tokens",
)


def _validate_integer(value: object, info: Any) -> object:
    if value is None:
        return None
    # ``True`` 在 Python 里等于 1，当成容量会算出毫无意义的预算。
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(_invalid(f"{info.field_name} 必须是整数"))
    floor = 0 if info.field_name == "safety_tokens" else 1
    if value < floor:
        raise ValueError(_invalid(f"{info.field_name} 不能小于 {floor}"))
    return value


class ModelLimitOverride(BaseModel):
    """作者为某个精确模型填的容量；未填的项保持未知。"""

    model_config = ConfigDict(extra="forbid")

    context_window_tokens: int | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    #: 预留多少输出空间；不填时用运行时实际上限，仍小于模型的最大输出。
    output_reserve_tokens: int | None = None
    #: 安全余量；不填时按窗口比例计算。
    safety_tokens: int | None = None

    @field_validator(*_CAPACITY_FIELDS, "safety_tokens", mode="before")
    @classmethod
    def _positive_integer(cls, value: object, info: Any) -> object:
        return _validate_integer(value, info)

    @model_validator(mode="after")
    def _reserve_within_output(self) -> ModelLimitOverride:
        if (
            self.max_output_tokens is not None
            and self.output_reserve_tokens is not None
            and self.output_reserve_tokens > self.max_output_tokens
        ):
            raise ValueError(_invalid("回复预留超过模型的最大输出"))
        return self


class ModelCapacity(BaseModel):
    """供应商在模型列表里返回的有效容量。

    只含三个上限，不含回复预留与安全余量：那两项是线簿的运行时策略，
    不是供应商对模型的描述。
    """

    model_config = ConfigDict(extra="forbid")

    context_window_tokens: int | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None

    @field_validator(*_CAPACITY_FIELDS[:3], mode="before")
    @classmethod
    def _positive_integer(cls, value: object, info: Any) -> object:
        return _validate_integer(value, info)


def _invalid(why: str) -> str:
    return f"[MODEL_LIMITS_INVALID] 模型设置：{why}。建议：核对已填容量后重新保存。"


@dataclass(frozen=True, slots=True)
class ModelLimits:
    """一个模型在一条连接上的容量事实；未知项保持 ``None``。"""

    connection_id: str
    api: str
    model: str
    context_window_tokens: int | None
    max_input_tokens: int | None
    max_output_tokens: int | None
    #: 实际为本轮回复预留的空间；未知窗口时也给出运行时上限。
    output_reserve_tokens: int
    safety_tokens: int
    #: 窗口数字的来源；界面在展开明细时显示它，避免把目录值当成作者填的。
    source: Literal["override", "metadata", "catalog", "unknown"]
    revision: str

    def input_limit(self) -> int | None:
        """这一轮可用的输入上限；两项都未知时返回 ``None``。"""
        ceilings: list[int] = []
        if self.max_input_tokens is not None:
            ceilings.append(self.max_input_tokens)
        if self.context_window_tokens is not None:
            ceilings.append(
                self.context_window_tokens - self.output_reserve_tokens - self.safety_tokens
            )
        usable = [value for value in ceilings if value > 0]
        return min(usable) if usable else None

    def window_share(self, estimated_tokens: int) -> float | None:
        """估算量占窗口的比例；窗口未知时不给数字。"""
        if not self.context_window_tokens:
            return None
        return estimated_tokens / self.context_window_tokens

    def as_dict(self) -> dict[str, object]:
        return {
            "connection_id": self.connection_id,
            "api": self.api,
            "model": self.model,
            "context_window_tokens": self.context_window_tokens,
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "output_reserve_tokens": self.output_reserve_tokens,
            "safety_tokens": self.safety_tokens,
            "source": self.source,
            "revision": self.revision,
        }


class _LimitProfile(Protocol):
    """解析所需的最小连接事实，避免反向依赖配置模型。"""

    name: str
    api: ProviderApi
    catalog_id: str
    model_limits: dict[str, ModelLimitOverride]
    model_metadata: dict[str, ModelCapacity]


def resolve_model_limits(
    profile: _LimitProfile,
    model: str,
    *,
    output_reserve_tokens: int = WORKER_OUTPUT_TOKENS,
) -> ModelLimits:
    """按精确模型名解析容量；依次查服务商返回、内置目录；旧手工覆盖不再生效，都没有就是未知。"""
    reported = profile.model_metadata.get(model)
    builtin = catalog_capacity(profile.catalog_id, model)

    def pick(field: str) -> tuple[int | None, Literal["override", "metadata", "catalog"] | None]:
        for source, holder in (
            ("metadata", reported),
            ("catalog", builtin),
        ):
            value = getattr(holder, field, None) if holder is not None else None
            if value is not None:
                return cast(int, value), cast(Literal["override", "metadata", "catalog"], source)
        return None, None

    window, window_source = pick("context_window_tokens")
    max_input, input_source = pick("max_input_tokens")
    max_output, output_source = pick("max_output_tokens")
    # 来源按窗口判定：界面上的占比以窗口为分母，它的出处比附属字段更能解释数字。
    source = window_source or input_source or output_source or "unknown"

    reserve = output_reserve_tokens
    safety = min(_MAX_AUTO_SAFETY, window // _SAFETY_SHARE) if window else 0
    if window is not None and window - reserve - safety <= 0:
        raise ValueError(_invalid("回复预留与安全余量吃掉了整个窗口"))
    return _limits(
        profile,
        model,
        context_window=window,
        max_input=max_input,
        max_output=max_output,
        reserve=reserve,
        safety=safety,
        source=source,
    )


def _limits(
    profile: _LimitProfile,
    model: str,
    *,
    context_window: int | None,
    max_input: int | None,
    max_output: int | None,
    reserve: int,
    safety: int,
    source: Literal["override", "metadata", "catalog", "unknown"],
) -> ModelLimits:
    identity = "|".join(
        [
            profile.name,
            profile.api,
            model,
            str(context_window),
            str(max_input),
            str(max_output),
            str(reserve),
            str(safety),
            source,
        ]
    )
    return ModelLimits(
        connection_id=profile.name,
        api=profile.api,
        model=model,
        context_window_tokens=context_window,
        max_input_tokens=max_input,
        max_output_tokens=max_output,
        output_reserve_tokens=reserve,
        safety_tokens=safety,
        source=source,
        revision=hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12],
    )
