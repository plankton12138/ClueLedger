# hastings.tools — 写作工具集

> 状态：P1 已实现（`b7384a2`）。代码：[src/hastings/tools/](../../src/hastings/tools/)，测试：[test_tools_bible.py](../../tests/test_tools_bible.py)、[test_tools_chapter.py](../../tests/test_tools_chapter.py)。

## 职责

LLM 触碰故事工作区的**唯一通道**。落实"工具是账本的守门人"（DESIGN.md §3.3）：
schema 校验、路径安全、覆盖保护都在工具层强制执行，工作区一致性靠工具保证，
不靠 prompt 自觉。

## 三个工具的分工

| 工具 | 角色 | 行为要点 |
|---|---|---|
| `bible_upsert` | 唯一写入口 | `data` 经 Pydantic 校验后 YAML 落盘，同名覆盖；错误消息带字段级原因，供模型自我修正 |
| `bible_query` | 只读检索口 | 带 `name` 返回角色 YAML 全文，不带返回全部摘要；查无结果返回**引导提示而非报错**，防重试死循环 |
| `write_chapter` | 正文出口 | 写 `chapters/chNN.md`（零填充），标题自动生成；已存在须 `force=true` 才覆盖 |

参数 Schema、guidelines 文案见 [phase-01 计划 §T3](../phase-01-minimal-loop-plan.md)。

## 公开接口

```python
# bible_tools.py / chapter_tools.py（工厂闭包模式，学 tau_coding 但不 import 它）
def create_bible_tools(ws: StoryWorkspace) -> list[AgentTool]    # upsert + query
def create_chapter_tools(ws: StoryWorkspace) -> list[AgentTool]  # write_chapter

# errors.py
class ToolInputError(ValueError)  # 参数不合法；loop 捕获后转为工具错误结果喂回模型
```

每个 `AgentTool` 带：`description`（选型依据）、`prompt_guidelines`（拼入 system prompt
的使用规范）、`render_call`（终端单行摘要）、`details`（结构化数据，供未来审计/前端）。

## 关键决策

| 决策 | 理由 |
|---|---|
| 参数错误抛 `ToolInputError` 而非返回错误结果 | tau loop 自动捕获并转 `is_error=True` 结果；工具代码更直白，单测直接断言异常消息 |
| agent 可见文案（description/guidelines/结果文本）全中文 | 产品是中文悬疑写作；候选端点（DeepSeek/GLM/Qwen）均中文优先 |
| `entity_type` 白名单仅 `character`，其余明确报"Phase 2 提供" | 范围红线：错误消息即路线图，防止模型反复试探 |
| 工厂闭包绑定 `StoryWorkspace` | 工具无全局状态；测试用 `tmp_path` 即可完全隔离 |

## 约束与红线

- hastings 任何代码**零 import** `tau_coding`（R2 红线，CI 验收时 grep 验证）；
- 新增工具必须：`prompt_guidelines` 非空 + `render_call` 非空 + 每条约束有单测；
- 工具内不做 LLM 调用（纯函数式文件操作）；审计类智能（P5）走独立 harness。

## 演化方向

- P2：`timeline_check` / `method_lock` / `method_unlock` + 五账本 upsert/query 扩展；
- P4：`style_search` / `style_profile`（RAG 检索，见 [rag 设计]）；
- P5：`foreshadow_audit` 与审计员 harness 联动。
