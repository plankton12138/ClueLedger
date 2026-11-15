# 分模块实现示例

日期：2026-10-28。状态：**拟议代码，不是已实现接口**。示例突出容易出错的边界，不代替完整实现。文中 Repository、Grant 等为待实现的应用内部接口，其职责由[数据契约](data-and-runtime.md)规定；不能直接复制成没有持久化或权限检查的空壳。

## 1. 结构：推进关系和重新解释分别校验

这段纯函数可独立实现与测试。实际时间关系另调用相同的无环算法，不能与揭示关系混算。

```python
from graphlib import CycleError, TopologicalSorter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Edge(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=1)
    from_id: str = Field(min_length=1)
    to_id: str = Field(min_length=1)
    kind: Literal["next", "reframe", "answer"]
    reason: str = Field(min_length=1)


def validate_edges(node_ids: set[str], edges: list[Edge]) -> None:
    predecessors: dict[str, set[str]] = {node: set() for node in node_ids}
    seen_ids: set[str] = set()
    for edge in edges:
        if edge.id in seen_ids:
            raise ValueError("duplicate edge id")
        seen_ids.add(edge.id)
        if edge.from_id not in node_ids or edge.to_id not in node_ids:
            raise ValueError("edge references a missing node")
        if edge.from_id == edge.to_id:
            raise ValueError("self reference is not a story transition")
        if edge.kind == "next":
            predecessors[edge.to_id].add(edge.from_id)
    try:
        tuple(TopologicalSorter(predecessors).static_order())
    except CycleError as exc:
        raise ValueError("disclosure order contains a cycle") from exc
```

调用层把校验异常映射为 STORY_GRAPH_INVALID 及统一错误文案。还需检查作品与方案归属、线索引用、知情来源、分支成员、删除后的悬空引用；不能只有无环检查。

## 2. 提交：先识别重试，再校验代次与来源

Repository 的提交方法必须实现持久 prepared、提交决定、恢复和完整收据；单个 os.replace 或 Git commit 都不等同于该接口。

```python
def commit_change(repo, operation_store, change, app_grant):
    # 同一作品的暂停和提交决定共享这个跨进程协调锁。
    with repo.owner_lock(change.workspace_lineage):
        previous = repo.receipt_for(change.request_key)
        if previous is not None:
            repo.require_same_payload(previous, change.payload_digest)
            return previous

        operation = operation_store.require_current(change.operation_id)
        operation.require_running_generation(change.generation)
        repo.require_grant(app_grant, change.targets)
        repo.require_revision(change.variant_id, change.base_revision)
        repo.require_sources(change.read_dependencies)
        repo.validate_complete_change(change)

        prepared = repo.prepare_durable(change)
        # 从决定到应用之间崩溃，恢复时继续完成；不重复调用模型。
        decided = repo.decide_commit_durable(prepared)
        return repo.apply_and_record_receipt(decided)
```

这些内部参数必须有严格类型，示例省略业务对象定义以展示顺序。app_grant 来自服务端受理，不取模型负载。prepare/decide/apply 每一步都要有故障注入测试；半提交期间受管读取只能获得恢复状态，不能拼接新旧内容。

撤销调用同一提交器，反向变更只包含原目标，read set 使用当前目标状态。后续编辑冲突时拒绝覆盖，并保留可继续调整的信息。

## 3. 输入：必需材料不能因预算被悄悄省略

先在策略层完成作用域筛选和必需依赖闭包，再调用此选择器。measure 必须测量包含固定指令、工具定义及材料的完整候选请求；capacity 已扣除输出和后续工具读取预算。

```python
from collections.abc import Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Material:
    source_id: str
    revision: str
    text: str
    required: bool


class RequiredContextTooLarge(Exception):
    pass


def select_materials(
    items: Sequence[Material],
    *,
    capacity: int,
    measure: Callable[[Sequence[Material]], int],
) -> tuple[list[Material], list[Material]]:
    chosen = [item for item in items if item.required]
    if measure(chosen) > capacity:
        raise RequiredContextTooLarge("split the task; keep required material intact")
    omitted: list[Material] = []
    for item in items:
        if item.required:
            continue
        if measure([*chosen, item]) <= capacity:
            chosen.append(item)
        else:
            omitted.append(item)
    return chosen, omitted
```

不按固定字数截断约定。预算估计保留余量；供应商仍报上下文超限时进入同一拆分／停止流程。选取结果与实际发送 messages 一起冻结，工具补读也登记来源。

试读的隔离必须早于装配：

```python
def prepare_reader_task(repo, request):
    # 返回专用窄接口；不把整本作者结构传进试读执行器。
    prefix = repo.read_presentation_prefix(
        variant_id=request.variant_id,
        cutoff=request.reader_cutoff,
    )
    return {
        "messages": build_reader_messages(prefix),
        "tools": make_prefix_only_tools(prefix.allowed_source_refs),
        "history": [],
    }
```

build_reader_messages 使用固定中性试读模板，不接收作者自由问题或对话历史。自由表达只用于作者侧选择位置和任务，避免无法可靠过滤的提问暗示进入读者阶段。外部常识与模型猜测单独看待。

## 4. 操作：暂停先撤销写入资格

所有入口的发布函数，包括工具写入，都必须通过相同的持久代次检查。

```python
async def pause_operation(store, runners, operation_id, request_key):
    with store.owner_lock_for(operation_id):
        prior = store.control_receipt(request_key)
        if prior is not None:
            store.require_control_matches(prior, "pause", operation_id)
            return prior
        operation = store.read(operation_id)
        # 若提交决定已经先完成，保留其收据，只停止剩余阶段。
        operation.revoke_active_generation()
        receipt = store.save_paused_and_control_receipt(operation, request_key)
    runners.request_cancel(operation_id)
    return receipt


async def publish_text(store, operation_id, generation, block_id, delta):
    # 检查和写事件必须在同一临界区，避免检查后恰逢暂停。
    with store.owner_lock_for(operation_id):
        operation = store.read(operation_id)
        if not operation.accepts_output(generation):
            return
        store.append_text(operation, block_id=block_id, text=delta)
```

控制请求须绑定负载和 owner，不能只按全局 request_key 命中。重复暂停终结操作返回其真实终态，不把 completed 改成 paused。继续／插话的去重、追加指令和新代次登记在同一事务完成；远端取消放到锁外。

phase 中的模型请求由共同执行器观察；真实文本 token 进入 text_delta，工具参数增量只进入候选缓冲，完整解析和校验后才可执行。取消时丢弃不完整工具参数，不能当成合法空参数继续。

## 5. 来源：定位存在与语义成立分别判断

```python
import hashlib


def verify_quote(raw: bytes, digest: str, start: int, end: int, quote: str) -> bool:
    if hashlib.sha256(raw).hexdigest() != digest:
        return False
    text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    if not 0 <= start < end <= len(text):
        return False
    return text[start:end] == quote
```

实际接入使用 Workspace 的受管路径与同一规范化读取规则，并把编码异常映射为来源不可用。verify_quote 为真只说明原句和版本吻合，不授权写入“线索成立／已回应”。

语义报告需要覆盖要求并限制修正次数：

```python
async def check_candidate(candidate, requirements, reviewer, reviser):
    report = await reviewer.check(candidate, requirements)
    report.require_requirement_coverage(requirements)
    if not report.blocking_findings:
        return candidate, report
    revised = await reviser.repair(candidate, report, immutable_requirements=requirements)
    checked = await reviewer.check(revised, requirements)
    checked.require_requirement_coverage(requirements)
    # 调用方仅在无关键未解决问题时提交；否则保留候选并提问。
    return revised, checked
```

每次 reviewer/reviser 都是可见模型阶段。应用另查版本、读写范围与原文依据，模型不能修改 immutable_requirements。未完成报告、漏掉要求与明确无问题分别处理。

## 6. 前端：代次与事件序号防止串入旧输出

下面是共享归并器的核心子集；完整事件联合类型还包括候选、提交、提问与失败。

```typescript
type OperationEvent = {
  seq: number;
  operation_id: string;
  generation: number;
} & (
  | { type: "attempt_started" }
  | { type: "text_delta"; block_id: string; text: string }
  | { type: "operation_paused" }
);

type State = {
  operationId: string;
  cursor: number;
  generation: number;
  paused: boolean;
  needsSnapshot: boolean;
  blocks: Record<string, string>;
};

function reduce(state: State, event: OperationEvent): State {
  if (event.operation_id !== state.operationId || event.seq <= state.cursor) return state;
  if (event.type === "attempt_started") {
    if (event.generation <= state.generation) return { ...state, cursor: event.seq };
    return { ...state, cursor: event.seq, generation: event.generation, paused: false };
  }
  if (event.generation < state.generation) return { ...state, cursor: event.seq };
  if (event.generation > state.generation) return { ...state, needsSnapshot: true };
  const next = { ...state, cursor: event.seq };
  if (event.type === "operation_paused") return { ...next, paused: true };
  if (state.paused) return next;
  const key = `${event.generation}:${event.block_id}`;
  return { ...next, blocks: { ...state.blocks, [key]: (state.blocks[key] ?? "") + event.text } };
}
```

重连快照需同时给出 cursor、generation、状态、已有块与提交收据，安装后再处理后续事件。旧代内容保留用于回看；当前输出按 generation:block_id 分开，不能把新代继续文本直接拼进旧流。

## 7. 页面：阅读选择与运行目标独立

```tsx
function StoryTaskControls({ operation, pause, resume, openChat }: Props) {
  const running = operation.status === "running";
  return <div aria-label="当前模型任务">
    <span>{operation.targetLabel}</span>
    {running && <button onClick={() => pause(operation.id)}>暂停</button>}
    {operation.status === "paused" &&
      <button onClick={() => resume(operation.id, operation.generation)}>继续</button>}
    <button onClick={() => openChat(operation.conversationId)}>对话</button>
  </div>;
}
```

Props 在正式实现中使用 API 类型，按钮用现有组件。targetLabel 来自 operation 的固定目标，不从当前图中选中的节点推导。宽屏对话区与窄屏任务条共用这个控制逻辑。

节点点击只改变浏览状态；“调整这处”将精确引用加入输入上下文。已运行任务的插话通过 interject 一次受理；候选、提交与正文高亮分别读取实际结果，不能依赖助手回答中的文本标志。

## 8. 移除：验证旧接口退出且新能力正常

删除旧文件之前提取共享依赖，随后验证 API、工具注册和页面三方面。测试旧接口退出时还要验证未触发模型请求，防止旧入口仅改成另一种任务。

```python
def assert_removed_json_api(response) -> None:
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
```

对旧 GET/POST 资料入口分别执行请求；不能让静态站点回退的 200 HTML 冒充已经移除。配套新工作流测试必须通过：打开作品、查看正文、在构思中核对来源、正常发起写作；不能靠删除旧测试掩盖能力回退。
