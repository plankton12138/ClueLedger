# 1.0.2 分模块实现示例

日期：2026-10-30。状态：**设计示例，未接入生产代码**。名称可按项目既有类型调整；计算规则以[开发计划](development-plan.md)为准。

## 1. 容量目录与元数据

模型事实和项目预算分别定义；不要把默认预算写进未知模型的 `context_window_tokens`。

```python
# model_catalog.py：两个精确 ID 分别列出，并记录来源及核对日期。
# https://api-docs.deepseek.com/zh-cn/api/list-models/  2026-10-30
DEEPSEEK_ENTRIES = {
    "deepseek-flash": CatalogEntry(1_048_576, max_output_tokens=393_216),
    "deepseek-v4-pro": CatalogEntry(1_048_576, max_output_tokens=393_216),
}
```

发现层已经支持 `context_window` / `max_output_tokens`。补契约测试而非新写解析器：

```python
def test_deepseek_metadata_fields():
    capacity = _reported_capacity({
        "id": "deepseek-flash",
        "context_window": 1_048_576,
        "max_output_tokens": 393_216,
    })
    assert capacity == ModelCapacity(
        context_window_tokens=1_048_576,
        max_output_tokens=393_216,
    )
```

## 2. 项目配置与统一预算

配置示例（拟新增服务端字段）：

```yaml
context_policy:
  max_input_tokens: 128000
```

以下函数是纯计算示例。调用前由既有配置模型验证所有已知容量和策略为正整数，实际接入时复用项目错误类型。

```python
from dataclasses import dataclass
from typing import Literal

@dataclass(frozen=True)
class InputBudget:
    project_limit: int
    model_limit: int | None
    effective_limit: int
    source: Literal["project", "model", "both"]

def resolve_input_budget(
    *, project_limit: int, window: int | None,
    max_input: int | None, output_reserve: int,
) -> InputBudget:
    model_limits = []
    if max_input is not None:
        model_limits.append(max_input)
    if window is not None:
        safety = min(4096, window // 20)
        remaining = window - output_reserve - safety
        if remaining <= 0:
            raise WorkspaceError(
                code="CONTEXT_POLICY_INVALID", where="model.context",
                why="回复预留与安全余量超过已知窗口",
                fix="核对模型容量与输出额度后重试",
            )
        model_limits.append(remaining)
    model_limit = min(model_limits) if model_limits else None
    effective = min(project_limit, model_limit) if model_limit is not None else project_limit
    source = "project"
    if model_limit is not None:
        source = "both" if model_limit == project_limit else (
            "model" if model_limit < project_limit else "project"
        )
    return InputBudget(project_limit, model_limit, effective, source)
```

实际输出额度先取阶段额度与模型输出上限的较小值，再同时传给 provider 请求参数和预算函数。R 不得一处 4096、一处使用供应商默认值。

## 3. 请求、摘要与快照

每次模型调用使用已冻结的快照，输入预算检查放在已有请求记录边界，不散落在页面和工具里。

```python
# 表意伪代码：接入 RecordedProvider 的已有处理顺序。
working = await apply_request_boundary(messages, snapshot, signal)
working = trim_if_needed(working, snapshot.input_limit_tokens)
estimate = estimate_complete_request(system, tools, working)
if estimate.total > snapshot.input_limit_tokens:
    raise required_input_too_large()
# 事件记录的预算与真正调用使用同一 snapshot。
await record_and_send(working, estimate, snapshot)
```

自动压缩条件用 `estimated_input >= ceil(B * 0.9)`；摘要分段每次也使用 B，包括旧摘要、摘要提示和新增完整轮次。摘要完成后回到原请求重新估算，不能把摘要请求的估算当作写作上下文。

暂停继续从统一操作恢复冻结的 `policy_revision` 和预算；不得从当前全局配置覆盖已有快照。读取用量时不执行上面的发送流程。

## 4. API 与会话投影

新增字段必须从请求记录一直传到页面；不能只在响应模型里补默认值。

```python
class ContextBudgetView(BaseModel):
    model_identity: str
    context_window_tokens: int | None
    model_input_limit_tokens: int | None
    project_input_limit_tokens: int
    input_limit_tokens: int
    input_limit_source: Literal["project", "model", "both"]
    policy_revision: str
```

该示例字段合并到既有 ContextView，不新增平行接口。没有选中可用模型时使用外层 pending/不可用状态，不构造伪模型；估算数值仍为 null。idle 重建复用同一预算事实与估算规则。

## 5. 页面

主进度用有效输入预算；工具分项、真实累计用量和模型窗口分别表达。

```typescript
type BudgetDisplay = {
  estimated_active_tokens: number | null;
  input_limit_tokens: number | null;
};

function budgetPercent(view: BudgetDisplay): number | null {
  const used = view.estimated_active_tokens;
  const limit = view.input_limit_tokens;
  if (used === null || limit === null || limit <= 0) return null;
  return used / limit * 100;
}

// 数字可显示超过 100%；仅进度条宽度限制在容器内。
const percent = budgetPercent(context);
const fill = percent === null ? 0 : Math.min(100, Math.max(0, percent));
```

实际接入复用现有格式化函数，不在组件另写 K/M 换算。`context_window_tokens === null` 只影响模型窗口明细，不影响已知项目预算的进度条。窗口有值但输入尚未估算，也不能显示 0%。
