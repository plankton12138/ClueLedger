"""按精确模型解析容量，并与项目输入预算合成一个有效上限（0.9 U1，1.0.2 重写）。

容量回答的是「这一轮最多能带多少输入」，它必须同时服务于两个地方：界面上的占比，
和真正发出请求之前的预算检查。两处各算一套，就会出现「界面说还能写、请求被拒」。

六条取舍：

- **不猜窗口。** 模型发现列表只有名字，名称前缀相同也不代表容量相同。服务商未返回且没有
  目录条目时就是未知，不填 128K 之类的默认值；未知只影响窗口字段，不再让整个预算停摆。
- **来源有优先级。** 服务商在模型列表里返回的有效容量 → 内置模型目录 → 未知。逐字段补齐：
  服务商未返回的字段可由目录补上。
- **项目预算是应用策略，不是模型事实。** 作者可以为一个能装一百万的模型只开 128K：
  这是成本与响应时间的取舍。项目上限单独记在 ``project_input_limit_tokens``，
  绝不写进 ``context_window_tokens``。
- **有效上限取两者较小值。** 模型容量完全未知时用项目预算，自动压缩与发送前检查照常执行；
  已知的模型容量更小时不会被项目预算放大。
- **回复预留对齐实际请求。** 写作用途的回复上限是运行时固定的 4096，预算中的回复预留与它
  保持一致。显示 8K 而实际只允许 4K，会让占比看起来比真实情况宽松。
- **按精确模型名匹配。** 配置以模型名为键，``deepseek-chat`` 的容量不会顺带给
  ``deepseek-chat-v2``；自定义中转也不套用原厂目录，因为中转可能改写窗口。

``revision`` 把容量与策略事实压成一个短标识，供估算指纹使用：改容量或改项目预算即换指纹，
旧估算失效。``policy_revision`` 只描述策略（项目上限与预算规则），与模型容量分开表达。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from hastings.server.model_catalog import catalog_capacity
from hastings.server.provider_catalog import ProviderApi

__all__ = [
    "AUTO_COMPACTION_THRESHOLD",
    "DEFAULT_PROJECT_INPUT_TOKENS",
    "WORKER_OUTPUT_TOKENS",
    "InputBudget",
    "ModelCapacity",
    "LimitProfile",
    "ModelLimitOverride",
    "ModelLimits",
    "resolve_input_budget",
    "resolve_model_limits",
]

#: 写作用途的回复上限；与 provider_runtime 构造 provider 时实际发送的值一致。
WORKER_OUTPUT_TOKENS = 4_096

#: 项目单次请求的输入上限默认值。这是产品策略选择，不是服务商推荐值或实测结论：
#: 它主动牺牲超大窗口的一次装载量，换取更小的单次请求与更早的压缩。
DEFAULT_PROJECT_INPUT_TOKENS = 128_000

#: 自动压缩与「接近预算」共用的比例。摘要本身也要预留空间，所以按有效输入预算算。
AUTO_COMPACTION_THRESHOLD = 0.9

#: 预算规则版本。规则本身变了（例如安全余量的算法）就换一个值，旧估算随之失效。
_POLICY_RULES = "input-budget-v1"

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
    """作者为某个精确模型填的容量；未填的项保持未知。

    1.0.2 起只作旧配置的兼容读取：解析时不再采用这些值，也不允许新增手填入口。
    """

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


def _policy_invalid(why: str, *, fix: str) -> str:
    return f"[CONTEXT_POLICY_INVALID] 模型设置：{why}。建议：{fix}。"


@dataclass(frozen=True, slots=True)
class InputBudget:
    """项目上限与模型上限合成后的有效输入预算。

    ``source`` 说明最后生效的是谁：模型更小时是 ``model``，项目更小时是 ``project``，
    两者相同是 ``both``。模型容量完全未知时只能是 ``project``。
    """

    project_limit: int
    model_limit: int | None
    effective_limit: int
    source: Literal["project", "model", "both"]


def resolve_input_budget(
    *,
    project_limit: int,
    window: int | None,
    max_input: int | None,
    output_reserve: int,
    safety: int,
) -> InputBudget:
    """纯计算：已知限制算出零或负数即报配置错误，不跳过后退到更大的上限。"""
    if isinstance(project_limit, bool) or not isinstance(project_limit, int) or project_limit <= 0:
        raise ValueError(
            _policy_invalid("max_input_tokens 必须是正整数", fix="核对项目输入上限后重试")
        )
    ceilings: list[int] = []
    if max_input is not None:
        ceilings.append(max_input)
    if window is not None:
        remaining = window - output_reserve - safety
        if remaining <= 0:
            raise ValueError(
                _policy_invalid(
                    "回复预留与安全余量超过已知窗口", fix="核对模型容量与输出额度后重试"
                )
            )
        ceilings.append(remaining)
    model_limit = min(ceilings) if ceilings else None
    if model_limit is None:
        return InputBudget(project_limit, None, project_limit, "project")
    effective = min(project_limit, model_limit)
    if model_limit == project_limit:
        source: Literal["project", "model", "both"] = "both"
    elif model_limit < project_limit:
        source = "model"
    else:
        source = "project"
    return InputBudget(project_limit, model_limit, effective, source)


@dataclass(frozen=True, slots=True)
class ModelLimits:
    """一个模型在一条连接上的容量事实，加上本轮生效的项目预算。

    ``context_window_tokens`` 只描述模型；项目上限另存 ``project_input_limit_tokens``。
    有效上限由两者现算（``input_limit_tokens``），不另存一份——否则改了一个字段忘了
    另一个，就会出现「界面按 A 算、请求按 B 检查」。
    """

    connection_id: str
    api: str
    model: str
    context_window_tokens: int | None
    max_input_tokens: int | None
    max_output_tokens: int | None
    #: 实际为本轮回复预留的空间；未知窗口时也给出运行时上限。
    output_reserve_tokens: int
    safety_tokens: int
    #: 项目策略上限；已绑定有效模型时始终是正整数。
    project_input_limit_tokens: int
    #: 窗口数字的来源；界面在展开明细时显示它，避免把目录值当成作者填的。
    source: Literal["override", "metadata", "catalog", "unknown"]
    policy_revision: str
    revision: str

    @property
    def budget(self) -> InputBudget:
        """项目上限与模型上限合成后的有效预算。"""
        return resolve_input_budget(
            project_limit=self.project_input_limit_tokens,
            window=self.context_window_tokens,
            max_input=self.max_input_tokens,
            output_reserve=self.output_reserve_tokens,
            safety=self.safety_tokens,
        )

    @property
    def model_input_limit_tokens(self) -> int | None:
        """由已知模型容量算出的可用输入；两项容量都未知时为 ``None``。"""
        return self.budget.model_limit

    @property
    def input_limit_tokens(self) -> int:
        """有效输入预算：项目与模型上限的较小值。"""
        return self.budget.effective_limit

    @property
    def input_limit_source(self) -> Literal["project", "model", "both"]:
        return self.budget.source

    def input_limit(self) -> int:
        """这一轮可用的输入上限；模型容量未知时就是项目预算。"""
        return self.input_limit_tokens

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
            "project_input_limit_tokens": self.project_input_limit_tokens,
            "model_input_limit_tokens": self.model_input_limit_tokens,
            "input_limit_tokens": self.input_limit_tokens,
            "input_limit_source": self.input_limit_source,
            "source": self.source,
            "policy_revision": self.policy_revision,
            "revision": self.revision,
        }


class LimitProfile(Protocol):
    """解析所需的最小连接事实，避免反向依赖配置模型。"""

    name: str
    api: ProviderApi
    catalog_id: str
    model_limits: dict[str, ModelLimitOverride]
    model_metadata: dict[str, ModelCapacity]


def policy_revision(project_limit: int) -> str:
    """策略版本：项目上限、压缩阈值与预算规则一起压成短标识。"""
    identity = "|".join(
        [str(project_limit), f"{AUTO_COMPACTION_THRESHOLD:.4f}", _POLICY_RULES]
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]


def resolve_model_limits(
    profile: LimitProfile,
    model: str,
    *,
    output_reserve_tokens: int = WORKER_OUTPUT_TOKENS,
    project_limit: int = DEFAULT_PROJECT_INPUT_TOKENS,
) -> ModelLimits:
    """按精确模型名解析容量，再与项目预算合成有效上限。

    依次查服务商返回、内置目录；旧手工覆盖不再生效，都没有就是模型容量未知。
    """
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
    budget = resolve_input_budget(
        project_limit=project_limit,
        window=window,
        max_input=max_input,
        output_reserve=reserve,
        safety=safety,
    )
    return _limits(
        profile,
        model,
        context_window=window,
        max_input=max_input,
        max_output=max_output,
        reserve=reserve,
        safety=safety,
        budget=budget,
        source=source,
    )


def _limits(
    profile: LimitProfile,
    model: str,
    *,
    context_window: int | None,
    max_input: int | None,
    max_output: int | None,
    reserve: int,
    safety: int,
    budget: InputBudget,
    source: Literal["override", "metadata", "catalog", "unknown"],
) -> ModelLimits:
    revision_of_policy = policy_revision(budget.project_limit)
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
            str(budget.project_limit),
            revision_of_policy,
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
        project_input_limit_tokens=budget.project_limit,
        source=source,
        policy_revision=revision_of_policy,
        revision=hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12],
    )
