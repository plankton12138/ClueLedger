# V06-E1 装配容器

## 目标

把「一份合并注入」拆成按用途、按分区、可回放的装配。状态域本身不在本阶段：
E1 只提供容器与记录，E2 起才往里放数据。

## 实际改动

| 位置 | 改动 |
|---|---|
| `workspace/assembly.py`（新） | 分区、预算、跳过原因与 `AssembledContext`；必装分区超预算抛 `CONTEXT_ASSEMBLY_FAILED` |
| `workspace/context.py` | `build_assembled_context` 按用途组装分区；`build_system_prompt` 收敛为只取文本的包装 |
| `workspace/agent.py` | `build_turn_assembly` 成为唯一装配入口；`build_harness` 接受已装配结果，避免同一轮装配两次 |
| `workspace/service.py` | 装配后先写 `state_injected` 事件，再把同一份结果交给 harness |
| `workspace/state.py` | `conversation_root` 改名 `workspace_state_root`，旧名保留为别名，路径不变、无数据迁移 |
| `frontend/src/api/workspace.ts` | 事件联合类型增加 `state_injected` |
| `frontend/src/state/chatMessages.ts` | 轮次记录增加 `injection`，只存分区规模与跳过原因 |
| `frontend/src/components/chat/TurnContextSummary.tsx`（新） | 回复上方一行「本轮上下文」，展开看分区与未注入原因 |

## 接口与数据影响

- 新增事件 `state_injected`，在 `turn_started` 之后、`run_phase` 之前落盘。它只记规模
  （分区 key 与字符数、跳过原因、预算用量），不含注入正文；旧前端忽略未知事件即可。
- 接口路径与响应结构未变；`/api/file-workspaces` 下无新增端点。
- **行为变化**：建索引任务不再装线索与真相，也不再注入章节索引摘要。理由是两个都要
  「收敛」：抽取只需原文，注入旧摘要会让摘要由摘要生成。
- 每轮系统提示的分区顺序固定，任务要求与目录超出预算时整轮失败，不再静默截断。

## 取舍与偏差

**偏差一：写作上下文仍装真相。** 计划里原本要求写作类型排除 `设定集/真相.md`，实施时
改为保留。原因：本产品是单 agent 循环，规划伏笔与自查都要用真相；把它移出写作上下文会
让模型无法判断线索是否支撑所有嫌疑人。隔离改为放在状态层——真身份与计划揭底章不进入
写作上下文，这一条在 E2 用真实数据实现，届时才有可断言的字段。

**偏差二：不单列「讨论」类型。** 受理时只有用途（写正文、建索引、检查），不知道作者
这一轮想讨论还是改稿；单列讨论类型会变成猜意图。现在由用途决定分区，讨论与写正文共用
`writer`。

**取舍：分区失败而不是截断。** 约定与目录被裁一半比缺一块更危险：模型会按残缺规则
行动，作者也看不出少了什么。目录自身仍有可见的「已截断，还剩多少」提示，那是目录内部
的分页说明，不是装配层的静默裁剪。

## 验证

2026-10-23 执行，均为本次重新运行：

| 检查 | 结果 |
|---|---|
| `uv run pytest -q --basetemp data/test-tmp/e1-full2 -o tmp_path_retention_count=100` | 468 通过（新增 7 项装配用例、1 项注入事件用例；流式顺序断言同步更新） |
| `uv run ruff check src/hastings scripts/model_acceptance.py tests` | 通过 |
| `uv run mypy src/hastings` | 43 个源文件通过 |
| `npm test -- --no-file-parallelism`（frontend） | 11 个文件、105 项通过 |
| `npm run build`（frontend） | 通过 |

未验证：浏览器端未重跑页面流程，`TurnContextSummary` 只有单元测试与构建通过；
在线模型未跑，本轮没有需要语义判断的部分。

## 未完成

- 状态域（披露、名词、时间线、线索）与对应面板从 E2 开始。
- 界面只展示分区规模，不展示注入内容：展示正文会把提示词内容搬进聊天记录，等状态域
  落地后按条目展示更有意义。
