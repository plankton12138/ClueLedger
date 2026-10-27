# 0.9 分模块实现示例

日期：2026-10-26。状态：**设计示例；实际实现与验证结果见[运行与验证](release.md)**。
示例不保证与正式实现逐字一致，接口、字段与持久校验以代码与运行说明为准。
本文件说明关键数据与边界，不是可直接复制后宣称完成的完整实现。
生产字段、持久校验和事件处理以[开发计划](development-plan.md)为准，验证见[验收矩阵](acceptance.md)。

## 1. 模型容量：未知值与回复预留

对应 U1，拟位于 `server/model_limits.py`。容量来源和实际回复上限一起进入受理快照。
显示配置与调用配置使用同一解析结果，避免显示预留 8K 而实际仅允许输出 4096。

```python
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class ModelLimits:
    context_window_tokens: int | None
    max_input_tokens: int | None
    max_output_tokens: int | None
    output_reserve_tokens: int
    safety_tokens: int
    source: Literal["override", "metadata", "unknown"]
    revision: str

    def input_limit(self) -> int | None:
        ceilings: list[int] = []
        if self.max_input_tokens is not None:
            ceilings.append(self.max_input_tokens)
        if self.context_window_tokens is not None:
            ceilings.append(
                self.context_window_tokens
                - self.output_reserve_tokens
                - self.safety_tokens
            )
        return min(ceilings) if ceilings else None

    def validate(self) -> None:
        configured = (
            self.context_window_tokens, self.max_input_tokens,
            self.max_output_tokens, self.output_reserve_tokens,
        )
        if any(value is not None and value <= 0 for value in configured):
            raise ValueError(
                "[MODEL_LIMITS_INVALID] 模型设置：容量和回复预留必须为正数。建议：修改对应模型配置。"
            )
        if self.safety_tokens < 0:
            raise ValueError(
                "[MODEL_LIMITS_INVALID] 模型设置：安全余量不能为负数。建议：使用非负值。"
            )
        effective_input_limit = self.input_limit()
        if (
            self.max_output_tokens is not None
            and self.output_reserve_tokens > self.max_output_tokens
        ) or (effective_input_limit is not None and effective_input_limit <= 0):
            raise ValueError(
                "[MODEL_LIMITS_INVALID] 模型设置：回复预留与模型容量冲突。建议：减小预留或核对容量。"
            )
```

配置边界还需拒绝布尔值、非整数和不合理大值，并按产品错误模型转换异常。
覆盖项按精确连接、协议与模型关联；旧配置无该字段时仍可读取，未知窗口不填默认 128K。

## 2. 上下文估算：指纹与实际消息分开

对应 U1，拟位于 `workspace/context_usage.py`。示例只展示组合与身份校验；分词或启发式策略可替换，
协议转换后结构开销由对应协议的估算策略计入。启发式不能作为严格上界证明。

```python
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json


@dataclass(frozen=True, slots=True)
class ContextEstimate:
    system_tokens: int
    tool_tokens: int
    message_tokens: int
    protocol_tokens: int
    method: str
    request_fingerprint: str

    @property
    def total(self) -> int:
        return (
            self.system_tokens + self.tool_tokens
            + self.message_tokens + self.protocol_tokens
        )


def estimate_request(
    *,
    model_identity: str,
    system: str,
    tool_wire: str,
    message_wire: str,
    source_revisions: Mapping[str, str],
    count: Callable[[str], int],
    method: str,
    protocol_tokens: int,
) -> ContextEstimate:
    # 输入来自同一份实际装配结果；不含 key、认证头或与上下文无关的 Usage。
    identity = json.dumps(
        [model_identity, system, tool_wire, message_wire, source_revisions, method],
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return ContextEstimate(
        system_tokens=count(system),
        tool_tokens=count(tool_wire),
        message_tokens=count(message_wire),
        protocol_tokens=protocol_tokens,
        method=method,
        request_fingerprint=hashlib.sha256(identity.encode("utf-8")).hexdigest(),
    )
```

只有相同模型、协议、前缀与资料版本的实测用量才参与校准。供应商实测总量与分项估算分开返回。
流式输出刷新需要节流，不为每个字符请求一次后端或重建全部历史。

## 3. 累计统计：可靠字段、去重与部分数据

对应 U0/U2，拟位于 `workspace/conversation_usage.py`。
下列输入是应用已经确认字段可用性的结果，不能直接把 `Usage()` 的默认零转换成它。
四类协议先列出可靠字段；缺字段又无法辨识时，采用未知，而不是猜测零。

```python
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReportedUsage:
    total_tokens: int | None
    input_tokens: int | None       # 已含缓存读写
    output_tokens: int | None      # 已含协议归入输出的推理
    cache_read_tokens: int | None  # input 的子项


@dataclass(frozen=True, slots=True)
class RequestEnd:
    operation_id: str
    usage: ReportedUsage | None
    duration_ms: int | None


def aggregate_request_ends(rows: list[RequestEnd]) -> dict[str, int | None]:
    unique: dict[str, RequestEnd] = {}
    for row in rows:
        previous = unique.get(row.operation_id)
        if previous is not None and previous != row:
            raise ValueError(
                "[USAGE_EVENT_CONFLICT] 用量记录：同一请求存在冲突终态。建议：保留原事件并检查记录链路。"
            )
        unique[row.operation_id] = row

    known = [row.usage for row in unique.values() if row.usage is not None]

    def sum_known(field: str) -> int | None:
        values = [getattr(usage, field) for usage in known]
        present = [value for value in values if value is not None]
        return sum(present) if present else None

    return {
        "finished_requests": len(unique),
        "requests_without_usage": sum(row.usage is None for row in unique.values()),
        "requests_without_total": sum(
            row.usage is None or row.usage.total_tokens is None
            for row in unique.values()
        ),
        "total_tokens": sum_known("total_tokens"),
        "input_tokens": sum_known("input_tokens"),
        "output_tokens": sum_known("output_tokens"),
        "cache_read_tokens": sum_known("cache_read_tokens"),
        "known_model_duration_ms": sum(
            row.duration_ms for row in unique.values() if row.duration_ms is not None
        ),
    }
```

生产聚合还需合并 started/finished、统计进行中请求、标记每个分项的覆盖度和缺失耗时。
冲突诊断不能抹掉此前可用统计或阻塞原写作任务；只保留一份终态作为已知结果，并暴露诊断。
没有请求时 UI 显示零；已有请求但没有可靠 token 时显示未知；已有部分数据时展示已知累计并标记不完整。

## 4. 共用请求投影与记录顺序

对应 U1/C1/C2。`PreparedRequest`、`ContextCoordinator` 是拟新增的应用接口：前者包含已验证的
system/messages/tools 与来源，后者负责只读投影、预算、候选提交和停止检查。
示例中的接口依赖需要在实施时实现，不能将占位接口当作已接线能力。

```python
from collections.abc import AsyncIterator

from tau_agent.messages import AgentMessage
from tau_agent.provider import CancellationToken, ModelProvider
from tau_agent.provider_events import AssistantMessageEvent
from tau_agent.tools import AgentTool


class ContextManagedProvider:
    def __init__(self, recorded: ModelProvider, coordinator: "ContextCoordinator") -> None:
        self.recorded = recorded
        self.coordinator = coordinator

    async def stream_response(
        self,
        *,
        model: str,
        system: str,
        messages: list[AgentMessage],
        tools: list[AgentTool],
        signal: CancellationToken | None = None,
        session_id: str | None = None,
    ) -> AsyncIterator[AssistantMessageEvent]:
        prepared = await self.coordinator.prepare_next_request(
            model=model, system=system, messages=tuple(messages),
            tools=tuple(tools), signal=signal,
        )
        # prepared 已完成必要整理的持久发布；原 messages 没有被原地改写。
        async for event in self.recorded.stream_response(
            model=model,
            system=prepared.system,
            messages=list(prepared.messages),
            tools=list(prepared.tools),
            signal=signal,
            session_id=session_id,
        ):
            yield event
```

组合关系为 `ContextManagedProvider(RecordedProvider(base_provider))`。
摘要另用 `RecordedProvider(base_provider, stage="compaction")`，不经过自身压缩编排器。
供应商溢出识别与一次恢复已在 C2 实现于请求记录器；上面的组合代码不包含重试逻辑。
恢复前必须确认没有可接受输出，并正确关闭失败响应、创建新请求 ID 与关联原请求，不能吞掉已发布的输出。

## 5. 安全切点：只切完整单元

对应 C1/C2，拟位于 `workspace/compaction.py`。跨轮单元是一轮完整公开对话；轮内单元由完整
assistant 消息与其全部工具结果构成。构建单元时校验重复调用 ID、孤立结果与缺失结果。

```python
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ContextUnit:
    first_message_id: str
    tokens: int
    closed: bool
    protected: bool = False


def first_kept_unit(
    units: tuple[ContextUnit, ...], *, keep_recent_tokens: int,
) -> int | None:
    if len(units) < 2 or keep_recent_tokens <= 0:
        return None
    accumulated = 0
    first_kept = len(units)
    for index in range(len(units) - 1, -1, -1):
        unit = units[index]
        if not unit.closed:
            # 本轮尚有未回执工具，先不压缩，留到下一个安全边界。
            return None
        if accumulated < keep_recent_tokens or unit.protected:
            first_kept = index
        accumulated += unit.tokens
    return first_kept if 0 < first_kept < len(units) else None
```

这只是近期保留切点，不是最终预算证明。随后必须计入摘要、固定保护项、工具定义、当前原文、
回复预留与安全余量，验证最终输入。若最新一个单元本身过大，按原始来源分段读取或报告必要输入超限，
不能通过切断工具对或丢弃原始请求达成目标。

保护项可以是确定性追加的原话与问题，不强迫所有历史轮次都留在后缀。
保留来源引用不能代替把必要原文真正提供给下一次模型调用。

## 6. 候选摘要与提交

对应 C1。语义条目只保存有来源的信息；执行状态与待回答问题由应用投影补入。
下列契约同时适用于校验、持久记录与前端详情。

```python
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class SummaryItem:
    item_id: str
    kind: Literal["goal", "constraint", "author_decision", "suggestion", "pending", "reference"]
    text: str
    source_event_ids: tuple[str, ...]
    quote: str | None
    supersedes: tuple[str, ...]
    profiles: tuple[str, ...]
    as_of_chapter: int | None


@dataclass(frozen=True, slots=True)
class SummaryCandidate:
    compaction_id: str
    source_head_seq: int
    covered_through_seq: int
    first_kept_turn_id: str
    source_digest: str
    items: tuple[SummaryItem, ...]
    tokens_before: int
    tokens_after: int


def validate_candidate(
    candidate: SummaryCandidate, *,
    allowed_sources: frozenset[str], input_limit: int | None,
) -> None:
    if not candidate.items or candidate.tokens_after >= candidate.tokens_before:
        raise ValueError(
            "[COMPACTION_NOT_EFFECTIVE] 对话上下文：摘要为空或未减少输入。建议：保留当前上下文。"
        )
    if not 0 <= candidate.covered_through_seq <= candidate.source_head_seq:
        raise ValueError(
            "[COMPACTION_SOURCE_INVALID] 对话上下文：摘要覆盖范围无效。建议：重新读取原会话后整理。"
        )
    for item in candidate.items:
        if not item.source_event_ids or not set(item.source_event_ids) <= allowed_sources:
            raise ValueError(
                "[COMPACTION_SOURCE_INVALID] 对话上下文：摘要引用不存在的原消息。建议：重新生成摘要。"
            )
    if input_limit is not None and candidate.tokens_after > input_limit:
        raise ValueError(
            "[CONTEXT_REQUIRED_INPUT_TOO_LARGE] 对话上下文：整理后仍超出输入预算。建议：缩小任务范围。"
        )
```

生产校验另需核对精确引文、条目标识唯一性、被替代关系、用途与章节、原话保护项、完整工具单元，
以及来源与当前模型/资料指纹。结构校验无法证明所有自然语言信息保留，在线语义验收不能省略。

提交顺序：

1. 在固定来源范围外完成摘要请求并记录其用量。
2. 持锁重查来源、资料与候选校验；元数据追加不扩大原历史范围。
3. 追加完整 `context_compacted` 事件，确认写入成功。
4. 使用已提交记录生成新投影，再发起原推理。

会话摘要和轮内摘要采用不同边界坐标。轮内记录包含稳定消息 ID；重启后仍按公开会话恢复，
不把某次运行的工具摘要自动当成下一轮的授权或可复用协议签名。

## 7. 后台操作：手动整理不伪造创作轮次

对应 C1，复用作品执行锁与会话事件。下列是拟新增接口的主路径示例，具体服务方法在 C1 实现。

```python
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field


class CompactRequest(BaseModel):
    request_key: str = Field(min_length=1, max_length=160)


class CompactAccepted(BaseModel):
    operation_id: str
    state: Literal["queued", "running", "noop"]
    through_seq: int


def compaction_router(service_for) -> APIRouter:
    router = APIRouter(prefix="/api/file-workspaces")

    @router.post(
        "/{workspace_id}/conversations/{conversation_id}/compact",
        status_code=202, response_model=CompactAccepted,
    )
    async def compact(
        workspace_id: str, conversation_id: str, body: CompactRequest,
    ) -> CompactAccepted:
        service = service_for(workspace_id)
        service.store.get(conversation_id)
        operation = await service.accept_compaction(
            conversation_id=conversation_id, request_key=body.request_key,
        )
        return CompactAccepted(**operation.view())

    return router
```

还必须实现计划列出的详情 GET、停止 POST、运行中排队合并和操作终态 SSE。
开始/成功/失败/停止/noop 都要持久化；同一幂等键返回同一操作，参数冲突返回明确错误。
操作本身不写 `turn_started.text`，不产生 Git 提交。空闲操作不得要求一个不存在的创作 turn_id。

## 8. 前端视图：服务端聚合与异步归属

对应 U2/U3。下面为 API 层转换后的精简展示视图，HTTP 与 SSE 继续使用 snake_case；
生产契约还需包含容量来源、估算方法、分项完整度和压缩操作详情。

```typescript
export interface ConversationUsage {
  workspaceId: string;
  conversationId: string;
  throughSeq: number;
  selectionRevision: string;
  context: {
    status: "pending" | "ready" | "stale" | "unavailable";
    estimatedActiveTokens: number | null;
    contextWindowTokens: number | null;
    outputReserveTokens: number | null;
    requestFingerprint: string | null;
    parts: { system: number; tools: number; messages: number } | null;
    includeDraft: false;
  };
  totals: {
    tokens: number | null;
    inputTokens: number | null;
    outputTokens: number | null;
    cacheReadTokens: number | null;
    requests: number;
    requestsWithoutUsage: number;
    requestsWithPartialUsage: number;
    requestsPending: number;
    modelDurationMs: number | null;
  };
}

export function formatContext(view: ConversationUsage["context"]): string {
  if (view.status === "pending") return "上下文 待计算";
  if (view.status !== "ready" || view.estimatedActiveTokens === null) return "上下文 待更新";
  if (view.contextWindowTokens === null) {
    return `上下文 ≈${formatTokens(view.estimatedActiveTokens)}`;
  }
  const ratio = view.estimatedActiveTokens / view.contextWindowTokens;
  // 不把超限值钳制为 100%；绘制圆环时单独限制几何比例。
  return `上下文 ≈${Math.round(ratio * 100)}%`;
}

function formatTokens(value: number): string {
  if (value < 1_000) return String(value);
  if (value < 1_000_000) return `${Number((value / 1_000).toFixed(1))}K`;
  return `${Number((value / 1_000_000).toFixed(1))}M`;
}
```

空闲手动整理的 `turn_id` 可为空，因此事件归并不能先假定所有事件都属于某个聊天轮次。
`context_usage_updated` 和整理事件进入独立会话视图，保留操作详情，不生成空白 assistant 消息。

```typescript
export function mayApplyUsage(
  next: ConversationUsage,
  current: ConversationUsage | null,
  active: { workspaceId: string; conversationId: string; selectionRevision: string },
): boolean {
  if (next.workspaceId !== active.workspaceId || next.conversationId !== active.conversationId) return false;
  if (next.selectionRevision !== active.selectionRevision) return false;
  return current === null
    || current.selectionRevision !== next.selectionRevision
    || next.throughSeq >= current.throughSeq;
}
```

相同游标的并发 GET 还需使用请求代次或投影版本防倒序，不能只依赖上述比较。
传入的 active 是当前展示上下文的身份，运行中来自受理快照，不能直接使用全局“下轮模型”选择。
状态按作品和会话缓存，切走不改任务归属；从 `usage` GET 恢复，再按 SSE 游标继续。
浮层复用已有弹层交互基础，按钮带 `aria-expanded` 和可读名称，不用自制悬停层替代键盘访问。
